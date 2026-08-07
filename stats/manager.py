"""模块：统计引擎（gvsdsdk 副本）
提供原子增量、周期查询、排行榜生成的底层能力。
所有数据库操作使用 select_for_update + F() 保证并发安全。

与 gvsds/apps/stats/engine.py 零行为差异复制——
8 个模块级函数包装为 StatsManager 的静态方法。"""

from django.db import transaction, connection
from django.db.models import F, Sum, Count
from gvsdsdk.stats.models import StatMetric, StatRecord, StatDimension, StatConfig


class StatsManager:
    """统计能力管理器——子服务器直接调用统计能力的统一入口

    用法:
        record, created = StatsManager.AtomicIncrement(
            tenant_uuid=tenant_uuid,
            period_type='daily',
            period_key='2026-06-11',
            metric_uuid=metric_uuid,
            dimension_values={'role': 'dashou', 'user_id': '123'},
            delta=1,
        )
    """

    @staticmethod
    def AtomicIncrement(tenant_uuid, period_type, period_key, metric_uuid,
                        dimension_values, delta):
        """原子增量更新——保证并发安全的单条统计记录累加

        使用 SELECT ... FOR UPDATE + INSERT ... ON DUPLICATE KEY UPDATE
        在 MySQL 上实现真正的原子 UPSERT。

        Args:
            tenant_uuid: 租户UUID (bytes)
            period_type: 周期类型 ('daily', 'hourly', 'monthly')
            period_key: 周期键 ('2026-06-11' 或 '2026-06-11T14')
            metric_uuid: 指标UUID (bytes)
            dimension_values: 维度值字典
            delta: 增量 (Decimal 或 int)

        Returns:
            (StatRecord, created) 元组
        """
        import json
        from decimal import Decimal

        delta = Decimal(str(delta))
        dim_json = json.dumps(dimension_values, sort_keys=True, ensure_ascii=False)

        with transaction.atomic():
            existing = list(
                StatRecord.objects.select_for_update().filter(
                    TenantUUID=tenant_uuid,
                    PeriodType=period_type,
                    PeriodKey=period_key,
                    Metric_id=metric_uuid,
                    DimensionValues=dimension_values,
                )[:1]
            )

            if existing:
                record = existing[0]
                record.Value = F('Value') + delta
                record.save(update_fields=['Value'])
                record.refresh_from_db(fields=['Value', 'UpdateTime'])
                return record, False
            else:
                record = StatRecord.objects.create(
                    TenantUUID=tenant_uuid,
                    PeriodType=period_type,
                    PeriodKey=period_key,
                    Metric_id=metric_uuid,
                    DimensionValues=dimension_values,
                    Value=delta,
                )
                return record, True

    @staticmethod
    def AtomicIncrementMulti(tenant_uuid, period_type, period_key,
                             increments):
        """批量原子增量——一次事务中更新多条统计

        Args:
            tenant_uuid: 租户UUID
            period_type: 周期类型
            period_key: 周期键
            increments: [(metric_uuid, dimension_values, delta), ...]

        Returns:
            List[StatRecord]
        """
        results = []
        with transaction.atomic():
            for metric_uuid, dim_values, delta in increments:
                record, _ = StatsManager.AtomicIncrement(
                    tenant_uuid, period_type, period_key,
                    metric_uuid, dim_values, delta,
                )
                results.append(record)
        return results

    @staticmethod
    def QueryPeriod(tenant_uuid, metric_uuid, period_type, period_key,
                    dimension_filter=None):
        """查询特定周期的统计数据

        Args:
            tenant_uuid: 租户UUID
            metric_uuid: 指标UUID
            period_type: 周期类型
            period_key: 周期键 ('2026-06-11')
            dimension_filter: 维度过滤 (JSON contains)

        Returns:
            QuerySet[StatRecord]
        """
        qs = StatRecord.objects.filter(
            TenantUUID=tenant_uuid,
            Metric_id=metric_uuid,
            PeriodType=period_type,
            PeriodKey=period_key,
        )
        if dimension_filter:
            qs = qs.filter(DimensionValues__contains=dimension_filter)
        return qs

    @staticmethod
    def QueryRange(tenant_uuid, metric_uuid, period_type,
                   start_key, end_key, dimension_filter=None):
        """查询时间范围内的统计数据

        Args:
            tenant_uuid: 租户UUID
            metric_uuid: 指标UUID
            period_type: 周期类型
            start_key: 起始周期键
            end_key: 截止周期键
            dimension_filter: 维度过滤

        Returns:
            QuerySet[StatRecord]
        """
        qs = StatRecord.objects.filter(
            TenantUUID=tenant_uuid,
            Metric_id=metric_uuid,
            PeriodType=period_type,
            PeriodKey__gte=start_key,
            PeriodKey__lte=end_key,
        )
        if dimension_filter:
            qs = qs.filter(DimensionValues__contains=dimension_filter)
        return qs

    @staticmethod
    def AggregateByDimension(tenant_uuid, metric_uuid, period_type,
                             start_key, end_key, dimension_key=None):
        """按维度聚合——对指定维度求和排名

        Args:
            tenant_uuid: 租户UUID
            metric_uuid: 指标UUID
            period_type: 周期类型
            start_key: 起始周期键
            end_key: 截止周期键
            dimension_key: 聚合的维度键（如 'user_id', 'role'）

        Returns:
            list[{dim_value, total}], 按 total 降序
        """
        qs = StatRecord.objects.filter(
            TenantUUID=tenant_uuid,
            Metric_id=metric_uuid,
            PeriodType=period_type,
            PeriodKey__gte=start_key,
            PeriodKey__lte=end_key,
        )

        results = qs.values('DimensionValues').annotate(
            total=Sum('Value'),
        ).order_by('-total')

        aggregated = {}
        for row in results:
            dims = row['DimensionValues']
            if dimension_key:
                key = dims.get(dimension_key, '_unknown')
                aggregated[key] = aggregated.get(key, 0) + row['total']
            else:
                for k, v in dims.items():
                    aggregated[f'{k}={v}'] = aggregated.get(f'{k}={v}', 0) + row['total']

        return sorted(
            [{'dim_value': k, 'total': v} for k, v in aggregated.items()],
            key=lambda x: x['total'],
            reverse=True,
        )

    @staticmethod
    def GenerateRanking(tenant_uuid, metric_uuid, ranking_period, ranking_date,
                        dimension_filter=None, top_n=50, sort_order='desc'):
        """生成排行榜快照

        从 StatRecord 中按维度聚合后排序生成 StatRanking 记录。

        Args:
            tenant_uuid: 租户UUID
            metric_uuid: 指标UUID
            ranking_period: 'daily' | 'monthly'
            ranking_date: 排行日期
            dimension_filter: 过滤条件（如 {'role': 'dashou'}）
            top_n: 取前N名
            sort_order: 'desc' | 'asc'

        Returns:
            List[StatRanking]
        """
        import datetime
        from gvsdsdk.stats.models import StatRanking

        period_type = ranking_period

        if ranking_period == 'daily':
            period_key = ranking_date.strftime('%Y-%m-%d') if hasattr(ranking_date, 'strftime') else str(ranking_date)
        elif ranking_period == 'monthly':
            period_key = ranking_date.strftime('%Y-%m') if hasattr(ranking_date, 'strftime') else str(ranking_date)
        else:
            period_key = str(ranking_date)

        qs = StatRecord.objects.filter(
            TenantUUID=tenant_uuid,
            Metric_id=metric_uuid,
            PeriodType=period_type,
            PeriodKey=period_key,
        )

        if dimension_filter:
            for k, v in dimension_filter.items():
                qs = qs.filter(**{f'DimensionValues__{k}': v})

        items = qs.values('DimensionValues').annotate(
            total=Sum('Value'),
        ).order_by(
            '-total' if sort_order == 'desc' else 'total',
        )[:top_n]

        rankings = []
        target_dim_key = (dimension_filter or {}).get('_target_dim', 'user_id')

        for rank, item in enumerate(items, 1):
            dims = item['DimensionValues']
            target_id = str(dims.get(target_dim_key, '_unknown'))
            rk = StatRanking.objects.create(
                TenantUUID=tenant_uuid,
                RankingPeriod=ranking_period,
                RankingDate=ranking_date,
                Metric_id=metric_uuid,
                DimensionFilter=dimension_filter or {},
                TargetID=target_id,
                Rank=rank,
                RankValue=item['total'],
                TargetName=dims.get('name', target_id),
                TargetAvatar=dims.get('avatar', ''),
            )
            rankings.append(rk)

        return rankings

    @staticmethod
    def ResetPeriod(tenant_uuid, metric_uuid, period_type, period_key,
                    dimension_filter=None):
        """重置指定周期的统计数据（用于每日/每月清零）

        Args:
            tenant_uuid: 租户UUID
            metric_uuid: 指标UUID
            period_type: 周期类型
            period_key: 周期键
            dimension_filter: 可选，仅重置特定维度

        Returns:
            int 删除的记录数
        """
        qs = StatRecord.objects.filter(
            TenantUUID=tenant_uuid,
            Metric_id=metric_uuid,
            PeriodType=period_type,
            PeriodKey=period_key,
        )
        if dimension_filter:
            qs = qs.filter(DimensionValues__contains=dimension_filter)
        count, _ = qs.delete()
        return count

    @staticmethod
    def PurgeOld(tenant_uuid, before_date, period_type='daily'):
        """清理过期统计数据

        Args:
            tenant_uuid: 租户UUID
            before_date: 在此之前的数据将被删除
            period_type: 周期类型

        Returns:
            int 删除的记录数
        """
        before_key = before_date.strftime('%Y-%m-%d') if hasattr(before_date, 'strftime') else str(before_date)
        qs = StatRecord.objects.filter(
            TenantUUID=tenant_uuid,
            PeriodType=period_type,
            PeriodKey__lt=before_key,
        )
        count, _ = qs.delete()
        return count

    @staticmethod
    def ListMetrics(tenant_uuid, status=None):
        """列出租户下的统计指标（可按状态过滤，保持数据库顺序）"""
        qs = StatMetric.objects.filter(TenantUUID=tenant_uuid)
        if status is not None:
            qs = qs.filter(MetricStatus=status)
        return qs

    @staticmethod
    def ListDimensions(tenant_uuid, status=None):
        """列出租户下的统计维度（可按状态过滤，保持数据库顺序）"""
        qs = StatDimension.objects.filter(TenantUUID=tenant_uuid)
        if status is not None:
            qs = qs.filter(DimensionStatus=status)
        return qs

    @staticmethod
    def ListConfigs(tenant_uuid, status=None):
        """列出租户下的统计配置（可按状态过滤，保持数据库顺序）"""
        qs = StatConfig.objects.filter(TenantUUID=tenant_uuid)
        if status is not None:
            qs = qs.filter(ConfigStatus=status)
        return qs
