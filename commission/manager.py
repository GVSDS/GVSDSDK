"""模块：分佣编排器
SAAS 级分佣引擎核心入口——子服务通过此引擎触发分佣事件。

职责：
  1. 接收分佣事件（Dispatch）
  2. 匹配分佣规则
  3. 执行分账策略计算
  4. 写入 CommissionRecord 流水
  5. 支持预结算（跨平台确认）
  6. 支持邀请链分佣（多级分佣）"""

import uuid
import logging
from decimal import Decimal
from typing import List, Optional, Dict, Any
from django.db import transaction, IntegrityError
from django.db.models import Sum as _AggSum
from django.utils import timezone as _timezone
from gvsdsdk.commission.models import (
    CommissionEvent,
    CommissionRule,
    CommissionRecord,
    RateTable,
    PreSettlement,
)
from gvsdsdk.commission.strategies.engine import (
    CreateStrategy,
    RemainderStrategy,
)

logger = logging.getLogger(__name__)


class CommissionError(Exception):
    def __init__(self, message, event=None):
        super().__init__(message)
        self.Event = event


class CommissionEngine:
    """分佣编排器——SAAS 主服务器核心入口

    用法:
        engine = CommissionEngine(tenant_uuid='xxx')

        # 配置分佣规则（SAAS管理员操作）
        engine.ConfigureRule(
            event_type='order_complete',
            rule_name='平台订单打手8成',
            participant_rules=[
                {'Role': 'worker',   'Type': 'percentage', 'Value': 0.80},
                {'Role': 'platform', 'Type': 'remainder'},
            ],
        )

        # 子服务触发分佣
        records = engine.Dispatch(
            event_type='order_complete',
            event_key='ORDER-20260611-001',
            total_amount=Decimal('100.00'),
            source_user_uuid='boss_001',
            participants={
                'worker': 'dashou_123',
                'agent': 'guanshi_456',
                'leader': 'zuzhang_789',
            },
            tier_index=1,
            payload={'order_id': 'xxx', 'game': 'LoL'},
        )

        # 跨平台预结算
        engine.PreSettle(
            event_key='ORDER-CROSS-001',
            external_platform='partner_club_002',
            external_order_id='P-ORDER-888',
            participant_id='dashou_123',
            amount=Decimal('80.00'),
            order_amount=Decimal('100.00'),
        )
    """

    def __init__(self, tenant_uuid):
        self.TenantUUID = tenant_uuid

    def ConfigureRule(self, event_type, rule_name, participant_rules,
                      rule_description='', max_invite_level=2,
                      rate_table_uuid=None, rule_priority=0):
        """配置分佣规则——SAAS 管理员为租户配置分佣策略

        Args:
            event_type: 事件类型 ('order_complete', 'member_purchase', ...)
            rule_name: 规则名称
            participant_rules: 参与者分账规则列表
                [
                  {'Role': 'worker', 'Type': 'percentage', 'Value': 0.80},
                  {'Role': 'agent', 'Type': 'fixed', 'Value': 10},
                  {'Role': 'leader', 'Type': 'tiered', 'Tiers': {1:5, 2:8, 3:10}},
                  {'Role': 'platform', 'Type': 'remainder'},
                ]
            rule_description: 规则描述
            max_invite_level: 邀请链最大层级
            rate_table_uuid: 关联费率表UUID
            rule_priority: 优先级（数字越大越优先）

        Returns:
            CommissionRule 实例
        """
        rate_table = None
        if rate_table_uuid:
            try:
                rate_table = RateTable.objects.get(
                    RateTableUUID=rate_table_uuid, TenantUUID=self.TenantUUID,
                )
            except RateTable.DoesNotExist:
                raise CommissionError(f'费率表不存在: {rate_table_uuid}')

        rule = CommissionRule.objects.create(
            RuleUUID=uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            EventType=event_type,
            RuleName=rule_name,
            RuleDescription=rule_description,
            ParticipantRules=participant_rules,
            Table=rate_table,
            MaxInviteLevel=max_invite_level,
            RulePriority=rule_priority,
        )
        logger.info(f'分佣规则已配置: {rule_name}@tenant={self.TenantUUID}')
        return rule

    def ConfigureRateTable(self, table_code, table_name, entries,
                           table_description=''):
        """配置费率表

        Args:
            table_code: 表编码（如 'dashou_order_split'）
            table_name: 表名
            entries: 费率条目字典 {key: value}
            table_description: 描述

        Returns:
            RateTable 实例
        """
        from gvsdsdk.commission.models import RateEntry

        table = RateTable.objects.create(
            RateTableUUID=uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            TableCode=table_code,
            TableName=table_name,
            TableDescription=table_description,
        )

        rate_entries = [
            RateEntry(
                EntryUUID=uuid.uuid4().bytes,
                Table=table,
                RateKey=str(key),
                RateValue=Decimal(str(value)),
            )
            for key, value in entries.items()
        ]
        if rate_entries:
            RateEntry.objects.bulk_create(rate_entries)

        logger.info(f'费率表已创建: {table_code}@tenant={self.TenantUUID}')
        return table

    def Dispatch(self, event_type, event_key, total_amount,
                 source_user_uuid=None, participants=None,
                 tier_index=0, payload=None, auto_settle=True):
        """触发分佣事件——子服务调用的核心入口

        Args:
            event_type: 事件类型
            event_key: 业务唯一键（幂等保护）
            total_amount: 可分佣总金额
            source_user_uuid: 触发者UUID（如付费用户）
            participants: 参与者映射 {role: user_id}
            tier_index: 阶梯索引（多次分红时用，第1次=1）
            payload: 业务上下文（JSON）
            auto_settle: 是否自动结算

        Returns:
            List[CommissionRecord] 分佣记录列表

        Raises:
            CommissionError: 重复事件或规则未配置
        """
        with transaction.atomic():
            if CommissionEvent.objects.filter(
                TenantUUID=self.TenantUUID, EventKey=event_key,
            ).exists():
                raise CommissionError(f'事件已处理: {event_key}')

            total_amount = Decimal(str(total_amount))
            if total_amount < 0:
                raise CommissionError('分佣总金额不能为负')

            try:
                event = CommissionEvent.objects.create(
                    EventUUID=uuid.uuid4().bytes,
                    TenantUUID=self.TenantUUID,
                    EventType=event_type,
                    EventKey=event_key,
                    TotalAmount=total_amount,
                    SourceUserUUID=source_user_uuid,
                    EventPayload=payload or {},
                    Participants=participants or {},
                    EventStatus=CommissionEvent.EVT_PENDING,
                )
            except IntegrityError:
                # 并发同 event_key 请求触发唯一约束，视为幂等重复
                raise CommissionError(f'事件已处理: {event_key}')

            rules = list(CommissionRule.objects.filter(
                TenantUUID=self.TenantUUID,
                EventType=event_type,
                RuleStatus=1,
            ).select_related('Table').order_by('-RulePriority'))

            if not rules:
                event.MarkFailed()
                raise CommissionError(f'未配置分佣规则: {event_type}', event=event)

            event.MarkProcessing()

            all_records = []
            total_allocated = Decimal('0')
            for rule in rules:
                records = self._execute_rule(
                    event=event,
                    rule=rule,
                    total_amount=total_amount,
                    participants=participants or {},
                    tier_index=tier_index,
                )
                # 跨规则累计校验：多规则合计不能超过 total_amount，防止超额分佣
                rule_total = sum((r.Amount for r in records), Decimal('0'))
                if total_allocated + rule_total > total_amount:
                    raise CommissionError(
                        f'多规则累计分佣金额超过总金额: '
                        f'{total_allocated + rule_total} > {total_amount}'
                    )
                total_allocated += rule_total
                all_records.extend(records)

            if auto_settle:
                event.MarkSettled()
                settle_time = _timezone.now()
                for rec in all_records:
                    rec.Status = CommissionRecord.REC_SETTLED
                    rec.SettleTime = settle_time
                if all_records:
                    CommissionRecord.objects.bulk_update(
                        all_records, ['Status', 'SettleTime']
                    )

            return all_records

    def _execute_rule(self, event, rule, total_amount, participants, tier_index):
        """执行单条分佣规则"""
        rate_table = rule.Table
        participant_rules = rule.ParticipantRules
        records = []
        allocated = Decimal('0')

        remainder_rule = None
        ordered_rules = sorted(
            participant_rules,
            key=lambda r: 0 if r.get('Type') == 'remainder' else 1,
        )

        for pr in ordered_rules:
            if pr.get('Type') == 'remainder':
                remainder_rule = pr
                continue

            strategy = CreateStrategy(pr, rate_table)
            amount = strategy.Calculate(
                total_amount,
                tier_index=tier_index,
            )
            if amount < 0:
                raise CommissionError(f'分佣金额不能为负: {amount}')
            allocated += amount

            participant_id = self._resolve_participant(pr, participants)
            if amount > 0:
                records.append(self._create_record(
                    event=event,
                    rule=rule,
                    participant_id=participant_id,
                    role=pr.get('Role', 'unknown'),
                    split_type=pr.get('Type', 'unknown'),
                    amount=amount,
                    rule_snapshot=pr,
                    tier_index=tier_index,
                ))

        if remainder_rule and allocated < total_amount:
            strategy = CreateStrategy(remainder_rule, rate_table)
            amount = strategy.Calculate(
                total_amount,
                AllocatedAmount=allocated,
            )
            if amount < 0:
                raise CommissionError(f'分佣金额不能为负: {amount}')
            participant_id = self._resolve_participant(remainder_rule, participants)
            if amount > 0:
                records.append(self._create_record(
                    event=event,
                    rule=rule,
                    participant_id=participant_id,
                    role=remainder_rule.get('Role', 'platform'),
                    split_type='remainder',
                    amount=amount,
                    rule_snapshot=remainder_rule,
                    tier_index=tier_index,
                ))

        return records

    def _resolve_participant(self, participant_rule, participants):
        role = participant_rule.get('Role') or participant_rule.get('role', 'unknown')
        Fallback = participant_rule.get('FallbackID', '')
        return participants.get(role, Fallback or f'{role}_default')

    def _create_record(self, event, rule, participant_id, role, split_type, amount, rule_snapshot, tier_index=0):
        # 注意：此处保留逐条 create 而非改为 bulk_create 批量插入。
        # 原因：Dispatch 的 auto_settle 分支调用 bulk_update(all_records, [...])，
        # bulk_update 依赖对象主键 pk；而 bulk_create 在部分数据库后端（如旧版 SQLite）
        # 不会回填 pk，会导致后续 bulk_update 失败。为保持兼容性暂不改为批量插入。
        return CommissionRecord.objects.create(
            RecordUUID=uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            Event=event,
            Rule=rule,
            ParticipantID=str(participant_id),
            ParticipantRole=role,
            SplitType=split_type,
            Amount=amount,
            RuleSnapshot=rule_snapshot,
            TierIndex=tier_index,
        )

    def PreSettle(self, event_key, external_platform, external_order_id,
                  participant_id, amount, order_amount=Decimal('0'),
                  participant_role='worker', confirm_deadline=None):
        """创建预结算记录——跨平台订单待确认

        Args:
            event_key: 业务唯一键
            external_platform: 外部平台标识
            external_order_id: 外部订单ID
            participant_id: 应分账对象
            amount: 预分金额
            order_amount: 订单原金额
            participant_role: 参与者角色
            confirm_deadline: 确认截止时间

        Returns:
            PreSettlement 实例
        """
        ps = PreSettlement.objects.create(
            SettlementUUID=uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            EventKey=event_key,
            ExternalPlatform=external_platform,
            ExternalOrderID=external_order_id,
            ParticipantID=participant_id,
            ParticipantRole=participant_role,
            Amount=Decimal(str(amount)),
            OrderAmount=Decimal(str(order_amount)),
            ConfirmDeadline=confirm_deadline,
        )
        logger.info(
            f'预结算: {event_key}->{participant_id}={amount}'
            f'@tenant={self.TenantUUID}'
        )
        return ps

    def ConfirmPreSettlement(self, event_key, participant_id=None,
                             confirmed_by=''):
        """确认预结算——转为正式分佣记录

        流程：
          1. select_for_update 锁定待确认的预结算记录，防止并发确认
          2. 通过 EventKey 查找对应 CommissionEvent；找不到则按预结算信息创建一个
          3. 为每条预结算创建一条 CommissionRecord（Status=REC_SETTLED）
          4. 标记预结算为 PST_CONFIRMED

        Args:
            event_key: 业务唯一键
            participant_id: 参与者ID（None=全部确认）
            confirmed_by: 确认人

        Returns:
            int 确认条数
        """
        confirm_time = _timezone.now()
        with transaction.atomic():
            qs = PreSettlement.objects.select_for_update().filter(
                TenantUUID=self.TenantUUID,
                EventKey=event_key,
                Status=PreSettlement.PST_PENDING,
            )
            if participant_id:
                qs = qs.filter(ParticipantID=participant_id)

            qs_list = list(qs)
            if not qs_list:
                return 0

            # 查找或创建 CommissionEvent——预结算不一定有 Dispatch 提前建 Event
            event = CommissionEvent.objects.filter(
                TenantUUID=self.TenantUUID,
                EventKey=event_key,
            ).first()
            if event is None:
                total_amount = sum(
                    (ps.Amount for ps in qs_list), Decimal('0')
                )
                event = CommissionEvent.objects.create(
                    EventUUID=uuid.uuid4().bytes,
                    TenantUUID=self.TenantUUID,
                    EventType=CommissionEvent.EVT_CUSTOM,
                    EventKey=event_key,
                    TotalAmount=total_amount,
                    EventPayload={
                        'source': 'pre_settlement',
                        'external_platform': qs_list[0].ExternalPlatform,
                        'external_order_id': qs_list[0].ExternalOrderID,
                    },
                    EventStatus=CommissionEvent.EVT_PROCESSING,
                )

            # 为每条预结算创建正式分佣记录
            records = []
            for ps in qs_list:
                ps.Status = PreSettlement.PST_CONFIRMED
                ps.ConfirmedBy = confirmed_by
                ps.ConfirmedAt = confirm_time
                ps.UpdateTime = confirm_time
                records.append(CommissionRecord(
                    RecordUUID=uuid.uuid4().bytes,
                    TenantUUID=self.TenantUUID,
                    Event=event,
                    Rule=None,
                    ParticipantID=ps.ParticipantID,
                    ParticipantRole=ps.ParticipantRole,
                    SplitType='pre_settled',
                    Amount=ps.Amount,
                    RuleSnapshot={
                        'external_platform': ps.ExternalPlatform,
                        'external_order_id': ps.ExternalOrderID,
                        'order_amount': str(ps.OrderAmount),
                    },
                    Status=CommissionRecord.REC_SETTLED,
                    SettleTime=confirm_time,
                ))

            PreSettlement.objects.bulk_update(
                qs_list, ['Status', 'ConfirmedBy', 'ConfirmedAt', 'UpdateTime']
            )
            if records:
                CommissionRecord.objects.bulk_create(records)

            # 若 Event 仍在处理态，标记为已结算
            if event.EventStatus == CommissionEvent.EVT_PROCESSING:
                event.MarkSettled()

            logger.info(
                f'预结算确认完成: {event_key} 确认 {len(qs_list)} 条@tenant={self.TenantUUID}'
            )
            return len(qs_list)

    def RejectPreSettlement(self, event_key, participant_id=None,
                            confirmed_by=''):
        """拒绝预结算

        Args:
            event_key: 业务唯一键
            participant_id: 参与者ID（None=全部拒绝）
            confirmed_by: 操作人

        Returns:
            int 拒绝条数
        """
        qs = PreSettlement.objects.filter(
            TenantUUID=self.TenantUUID,
            EventKey=event_key,
            Status=PreSettlement.PST_PENDING,
        )
        if participant_id:
            qs = qs.filter(ParticipantID=participant_id)

        count = 0
        qs_list = list(qs)
        reject_time = _timezone.now()
        for ps in qs_list:
            ps.Status = PreSettlement.PST_REJECTED
            ps.ConfirmedBy = confirmed_by
            ps.ConfirmedAt = reject_time
            ps.UpdateTime = reject_time
            count += 1
        if qs_list:
            PreSettlement.objects.bulk_update(
                qs_list, ['Status', 'ConfirmedBy', 'ConfirmedAt', 'UpdateTime']
            )
        return count

    def CalculateFee(self, total_amount, rate_table_code, rate_key):
        """计算手续费——用于提现场景

        Args:
            total_amount: 提现金额
            rate_table_code: 费率表编码
            rate_key: 费率键

        Returns:
            Decimal 手续费金额
        """
        total_amount = Decimal(str(total_amount))
        try:
            table = RateTable.objects.get(
                TenantUUID=self.TenantUUID,
                TableCode=rate_table_code,
                TableStatus=1,
            )
        except RateTable.DoesNotExist:
            return Decimal('0')

        rate = table.GetRate(rate_key)
        if rate is None:
            return Decimal('0')

        return (total_amount * rate).quantize(Decimal('0.0001'))

    def QueryRecords(self, participant_id=None, event_type=None,
                     status=None, start_date=None, end_date=None,
                     limit=50):
        """查询分佣记录

        Args:
            participant_id: 参与者ID
            event_type: 事件类型
            status: 状态
            start_date: 开始日期
            end_date: 结束日期
            limit: 返回条数

        Returns:
            QuerySet[CommissionRecord]
        """
        qs = CommissionRecord.objects.filter(TenantUUID=self.TenantUUID)

        if participant_id:
            qs = qs.filter(ParticipantID=participant_id)
        if status is not None:
            qs = qs.filter(Status=status)
        if event_type:
            qs = qs.filter(Event__EventType=event_type)
        if start_date:
            qs = qs.filter(CreateTime__gte=start_date)
        if end_date:
            qs = qs.filter(CreateTime__lte=end_date)

        return qs.order_by('-CreateTime')[:limit]

    def TotalByParticipant(self, participant_id, start_date=None, end_date=None):
        """统计某参与者的分佣总额"""
        # 仅统计已结算/已提现记录，排除已取消（REC_CANCELLED），避免总额虚高
        qs = CommissionRecord.objects.filter(
            TenantUUID=self.TenantUUID,
            ParticipantID=participant_id,
            Status__in=[CommissionRecord.REC_SETTLED, CommissionRecord.REC_WITHDRAWN],
        )
        if start_date:
            qs = qs.filter(CreateTime__gte=start_date)
        if end_date:
            qs = qs.filter(CreateTime__lte=end_date)

        result = qs.aggregate(total=_AggSum('Amount'))
        return result['total'] or Decimal('0')

    def BuildInviteChainCommission(self, event_type, event_key, total_amount,
                                   source_user_uuid, invite_chain,
                                   rule_name, participant_rules):
        """邀请链分佣——按层级对受邀人行为进行多级分佣

        Args:
            event_type: 事件类型
            event_key: 事件键
            total_amount: 总金额
            source_user_uuid: 行为触发者
            invite_chain: 邀请链 [{UserUUID, Level, Role}, ...]
                          Level=1 为直接邀请人，Level=2 为间推
            rule_name: 规则名称
            participant_rules: 参与者规则（按层级配置）
                例: [{'Role': 'agent_l1', 'Type': 'fixed', 'Value': 10},
                     {'Role': 'agent_l2', 'Type': 'fixed', 'Value': 5}]

        Returns:
            List[CommissionRecord]
        """
        rule = CommissionRule.objects.filter(
            TenantUUID=self.TenantUUID,
            EventType=event_type,
            RuleName=rule_name,
            RuleStatus=1,
        ).first()

        if not rule:
            rule = self.ConfigureRule(
                event_type=event_type,
                rule_name=rule_name,
                participant_rules=participant_rules,
            )

        participants = {
            item.get('Role', f'invite_l{item.get("Level",0)}'): item.get('UserUUID', '')
            for item in invite_chain
        }
        participants['source'] = source_user_uuid

        return self.Dispatch(
            event_type=event_type,
            event_key=event_key,
            total_amount=total_amount,
            source_user_uuid=source_user_uuid,
            participants=participants,
        )
