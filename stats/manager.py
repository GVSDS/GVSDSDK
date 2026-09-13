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

    @staticmethod
    def ListAutoResetConfigs(tenant_uuid, status=1):
        """列出租户下启用"自动重置"的统计配置（供周期清零任务使用）

        Args:
            tenant_uuid: 租户 UUID
            status: 配置状态过滤（默认 1=启用；传 None 则不过滤）

        Returns:
            QuerySet[StatConfig]
        """
        qs = StatConfig.objects.filter(TenantUUID=tenant_uuid, AutoReset=True)
        if status is not None:
            qs = qs.filter(ConfigStatus=status)
        return qs

    # ---------- 编排层（SAAS 主服务器视图 / Celery 任务入口） ----------

    @staticmethod
    def DefineMetric(tenant_uuid, metric_code, metric_name, metric_type='count',
                     unit='次', default_value=None):
        """定义统计指标（同编码已存在则更新）

        Returns:
            StatMetric 实例
        """
        metric, _created = StatMetric.objects.update_or_create(
            TenantUUID=tenant_uuid,
            MetricCode=metric_code,
            defaults={
                'MetricName': metric_name,
                'MetricType': metric_type,
                'Unit': unit,
                'DefaultValue': default_value or 0,
                'MetricStatus': 1,
            },
        )
        return metric

    @staticmethod
    def DefineDimension(tenant_uuid, dimension_code, dimension_name):
        """定义统计维度（同编码已存在则更新）

        Returns:
            StatDimension 实例
        """
        dim, _created = StatDimension.objects.update_or_create(
            TenantUUID=tenant_uuid,
            DimensionCode=dimension_code,
            defaults={
                'DimensionName': dimension_name,
                'DimensionStatus': 1,
            },
        )
        return dim

    @staticmethod
    def ConfigureAutoReset(tenant_uuid, metric_uuid, auto_reset=True,
                           reset_cron='0 0 * * *', enable_ranking=False,
                           ranking_top_n=50, retention_days=365):
        """配置指标的自动重置和排行榜

        Returns:
            StatConfig 实例
        """
        config, _created = StatConfig.objects.update_or_create(
            TenantUUID=tenant_uuid,
            Metric_id=metric_uuid,
            defaults={
                'AutoReset': auto_reset,
                'ResetCron': reset_cron,
                'EnableRanking': enable_ranking,
                'RankingTopN': ranking_top_n,
                'RetentionDays': retention_days,
                'ConfigStatus': 1,
            },
        )
        return config

    @staticmethod
    def GenerateDailyRanking(tenant_uuid, metric_uuid, ranking_date=None,
                             dimension_filter=None, top_n=50):
        """生成日排行榜快照（先清理同日旧快照再生成）

        Returns:
            List[StatRanking]
        """
        import datetime
        from gvsdsdk.stats.models import StatRanking

        if ranking_date is None:
            ranking_date = datetime.date.today()

        StatRanking.objects.filter(
            TenantUUID=tenant_uuid,
            RankingPeriod='daily',
            RankingDate=ranking_date,
            Metric_id=metric_uuid,
        ).delete()

        return StatsManager.GenerateRanking(
            tenant_uuid=tenant_uuid,
            metric_uuid=metric_uuid,
            ranking_period='daily',
            ranking_date=ranking_date,
            dimension_filter=dimension_filter,
            top_n=top_n,
        )

    @staticmethod
    def GenerateMonthlyRanking(tenant_uuid, metric_uuid, ranking_date=None,
                               dimension_filter=None, top_n=50):
        """生成月排行榜快照（先清理同日旧快照再生成）

        Returns:
            List[StatRanking]
        """
        import datetime
        from gvsdsdk.stats.models import StatRanking

        if ranking_date is None:
            today = datetime.date.today()
            ranking_date = today.replace(day=1)

        StatRanking.objects.filter(
            TenantUUID=tenant_uuid,
            RankingPeriod='monthly',
            RankingDate=ranking_date,
            Metric_id=metric_uuid,
        ).delete()

        return StatsManager.GenerateRanking(
            tenant_uuid=tenant_uuid,
            metric_uuid=metric_uuid,
            ranking_period='monthly',
            ranking_date=ranking_date,
            dimension_filter=dimension_filter,
            top_n=top_n,
        )

    @staticmethod
    def QueryRanking(tenant_uuid, ranking_period, ranking_date=None,
                     metric_uuid=None, top_n=None):
        """查询排行榜快照

        Args:
            ranking_period: 'daily' | 'monthly'
            ranking_date: 排行日期
            metric_uuid: 指标UUID（None=全部）
            top_n: 取前N名（按指标分别截取）

        Returns:
            QuerySet[StatRanking]
        """
        from gvsdsdk.stats.models import StatRanking

        qs = StatRanking.objects.filter(
            TenantUUID=tenant_uuid,
            RankingPeriod=ranking_period,
        )
        if ranking_date:
            qs = qs.filter(RankingDate=ranking_date)
        if metric_uuid:
            qs = qs.filter(Metric_id=metric_uuid)

        qs = qs.order_by('Metric', 'Rank')

        if top_n:
            metric_ids = qs.values_list('Metric_id', flat=True).distinct()
            filtered = StatRanking.objects.none()
            for mid in metric_ids:
                filtered |= qs.filter(Metric_id=mid)[:top_n]
            return filtered

        return qs

    @staticmethod
    def DailyReset(tenant_uuid, metric_uuid, target_date=None):
        """手动执行日清零（默认昨天）

        Returns:
            int 删除记录数
        """
        import datetime

        if target_date is None:
            target_date = datetime.date.today() - datetime.timedelta(days=1)

        period_key = target_date.strftime('%Y-%m-%d')
        return StatsManager.ResetPeriod(
            tenant_uuid, metric_uuid, 'daily', period_key,
        )

    @staticmethod
    def MonthlyReset(tenant_uuid, metric_uuid, target_date=None):
        """手动执行月清零（默认上月）

        Returns:
            int 删除记录数
        """
        import datetime

        if target_date is None:
            today = datetime.date.today()
            last_month = today.month - 1 if today.month > 1 else 12
            last_year = today.year if today.month > 1 else today.year - 1
            target_date = datetime.date(last_year, last_month, 1)

        period_key = target_date.strftime('%Y-%m')
        return StatsManager.ResetPeriod(
            tenant_uuid, metric_uuid, 'monthly', period_key,
        )

    @staticmethod
    def RunScheduledResets(tenant_uuid, target_date=None):
        """执行所有配置了自动重置的指标的周期清零（默认清零昨天）

        由 Celery Beat 定时任务调用。

        Returns:
            int 累计删除记录数
        """
        configs = StatConfig.objects.filter(
            TenantUUID=tenant_uuid,
            AutoReset=True,
            ConfigStatus=1,
        )

        total_deleted = 0
        for config in configs:
            total_deleted += StatsManager.DailyReset(
                tenant_uuid=tenant_uuid,
                metric_uuid=config.Metric_id,
                target_date=target_date,
            )

        return total_deleted

    @staticmethod
    def BuildRankings(tenant_uuid, target_date=None):
        """执行所有配置了排行榜的指标的排名生成（默认今天）

        由 Celery Beat 定时任务调用。

        Returns:
            int 累计生成的排行数
        """
        configs = StatConfig.objects.filter(
            TenantUUID=tenant_uuid,
            EnableRanking=True,
            ConfigStatus=1,
        )

        total_rankings = 0
        for config in configs:
            rankings = StatsManager.GenerateDailyRanking(
                tenant_uuid=tenant_uuid,
                metric_uuid=config.Metric_id,
                ranking_date=target_date,
                dimension_filter=config.RankingDimensionFilter or None,
                top_n=config.RankingTopN,
            )
            total_rankings += len(rankings)

        return total_rankings
