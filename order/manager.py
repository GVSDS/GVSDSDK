"""模块：订单管理器
SAAS 级订单编排器——子服务通过此管理器操作订单生命周期。

职责：
  1. 基于模板快速创建订单（冻结模板快照）
  2. 完整订单状态机流转（确认/支付/处理/完成/取消/退款/关闭/重开）
  3. 订单查询（按租户/公司/买家/状态多维度）
  4. 行项目批量管理

用法:
    manager = OrderManager(tenant_uuid='xxx', company_uuid='yyy', user=admin_user)

    # 从模板创建订单
    order = manager.CreateFromTemplate(
        template=tpl,
        level=level,
        buyer_user_uuid='user_001',
        order_title='VIP 月卡',
    )

    # 状态流转
    manager.Confirm(order.OrderUUID)
    manager.Pay(order.OrderUUID, payment_transaction=tx)
    manager.Complete(order.OrderUUID)

    # 查询
    orders = manager.ListOrders(status=Order.ORD_PAID)
"""
import uuid as _uuid
import logging
import datetime
from decimal import Decimal
from typing import Optional, List

from django.db import transaction
from django.utils import timezone as _timezone

from gvsdsdk.order.models import (
    OrderLevel,
    OrderTemplate,
    OrderTemplateItem,
    Order,
    OrderItem,
)

logger = logging.getLogger(__name__)


class OrderError(Exception):
    """订单业务异常"""

    def __init__(self, message, order=None):
        super().__init__(message)
        self.Order = order


class OrderManager:
    """订单编排器——SAAS 主服务器核心入口

    所有方法均按 tenant_uuid + company_uuid 隔离操作。
    状态流转方法会自动校验当前状态是否允许该流转。
    """

    # 状态流转规则：{ target_status: (allowed_from_statuses, timestamp_field) }
    FLOW_RULES = {
        Order.ORD_CONFIRMED:  ({Order.ORD_PENDING},                          'ConfirmTime'),
        Order.ORD_PAID:       ({Order.ORD_CONFIRMED},                        'PayTime'),
        Order.ORD_PROCESSING: ({Order.ORD_PAID},                             None),
        Order.ORD_COMPLETED:  ({Order.ORD_PROCESSING},                       'CompleteTime'),
        Order.ORD_CANCELLED:  ({Order.ORD_PENDING, Order.ORD_CONFIRMED},     'CancelTime'),
        Order.ORD_REFUNDING:  ({Order.ORD_PAID, Order.ORD_PROCESSING, Order.ORD_COMPLETED}, None),
        Order.ORD_REFUNDED:   ({Order.ORD_REFUNDING},                        None),
        Order.ORD_CLOSED:     ({Order.ORD_CANCELLED, Order.ORD_REFUNDED, Order.ORD_COMPLETED}, None),
    }

    # Reopen 特殊处理：CLOSED → PENDING
    REOPEN_FROM = Order.ORD_CLOSED
    REOPEN_TO = Order.ORD_PENDING

    def __init__(self, tenant_uuid, company_uuid=None, user=None):
        self.TenantUUID = self._to_bytes(tenant_uuid)
        self.CompanyUUID = self._to_bytes(company_uuid)
        self.User = user

    # ========== 查询方法 ==========

    def GetOrder(self, order_uuid):
        """查询单个订单（按租户+公司隔离）"""
        order_uuid_bytes = self._to_bytes(order_uuid)
        if not order_uuid_bytes:
            return None
        qs = Order.objects.filter(TenantUUID=self.TenantUUID, OrderUUID=order_uuid_bytes)
        if self.CompanyUUID:
            qs = qs.filter(CompanyUUID=self.CompanyUUID)
        return qs.first()

    def ListOrders(self, status=None, buyer_user_uuid=None, order_type=None,
                   page=1, page_size=20):
        """列出订单（多维度过滤）"""
        try:
            page = max(int(page), 1)
            page_size = min(max(int(page_size), 1), 200)
        except (TypeError, ValueError):
            page = 1
            page_size = 20
        qs = Order.objects.filter(TenantUUID=self.TenantUUID)
        if self.CompanyUUID:
            qs = qs.filter(CompanyUUID=self.CompanyUUID)
        if status is not None:
            qs = qs.filter(OrderStatus=status)
        if buyer_user_uuid:
            buyer_bytes = self._to_bytes(buyer_user_uuid)
            if buyer_bytes:
                qs = qs.filter(BuyerUserUUID=buyer_bytes)
        if order_type:
            qs = qs.filter(OrderType=order_type)
        qs = qs.order_by('-CreateTime')
        total = qs.count()
        start = (page - 1) * page_size
        records = list(qs[start:start + page_size])
        return {'records': records, 'total': total, 'page': page, 'page_size': page_size}

    @staticmethod
    def GetStats(tenant_uuid=None, company_uuid=None, buyer_user_uuids=None):
        """仪表盘订单统计——按租户/公司/买家多维度计数

        Args:
            tenant_uuid: 租户 UUID（None 不限制）
            company_uuid: 公司 UUID（None 不限制）
            buyer_user_uuids: 买家 UUID 集合（None 不限制；空集合返回全 0）

        Returns:
            dict: TotalOrders/TodayOrders/WeekOrders/PendingOrders/
                  PaidOrders/CompletedOrders
        """
        now = _timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - datetime.timedelta(days=today_start.weekday())

        qs = Order.objects.all()
        if tenant_uuid:
            qs = qs.filter(TenantUUID=tenant_uuid)
        if company_uuid:
            qs = qs.filter(CompanyUUID=company_uuid)
        if buyer_user_uuids is not None:
            buyer_bytes = [OrderManager._to_bytes(u) for u in buyer_user_uuids]
            buyer_bytes = [b for b in buyer_bytes if b]
            qs = qs.filter(BuyerUserUUID__in=buyer_bytes) if buyer_bytes else qs.none()

        return {
            'TotalOrders': qs.count(),
            'TodayOrders': qs.filter(CreateTime__gte=today_start).count(),
            'WeekOrders': qs.filter(CreateTime__gte=week_start).count(),
            'PendingOrders': qs.filter(OrderStatus=Order.ORD_PENDING).count(),
            'PaidOrders': qs.filter(
                OrderStatus__in=[Order.ORD_PAID, Order.ORD_PROCESSING, Order.ORD_COMPLETED]
            ).count(),
            'CompletedOrders': qs.filter(OrderStatus=Order.ORD_COMPLETED).count(),
        }

    def GetOrderItems(self, order_uuid):
        """获取订单行项目列表"""
        order_uuid_bytes = self._to_bytes(order_uuid)
        if not order_uuid_bytes:
            return []
        # 使用 Order_id 直接按 FK 主键过滤（与 create 中 Order=order 风格不同但语义一致：
        # 过滤时无需加载 Order 对象，创建时已有 Order 实例直接赋值）
        return list(OrderItem.objects.filter(Order_id=order_uuid_bytes))

    # ========== 创建方法 ==========

    @transaction.atomic
    def CreateFromTemplate(self, template, buyer_user_uuid, level=None,
                           seller_user_uuid=None, order_title=None,
                           order_description='', order_type=None,
                           discount_amount=Decimal('0'), currency='CNY',
                           expire_time=None, remark='', items_override=None,
                           payment_config=None):
        """从模板创建订单——自动冻结模板/等级快照，复制模板项到订单项

        Args:
            template: OrderTemplate 实例
            level: OrderLevel 实例（可选）
            buyer_user_uuid: 买家用户 UUID
            seller_user_uuid: 卖家用户 UUID（可选）
            order_title: 订单标题（默认用模板名）
            order_description: 订单描述
            order_type: 订单类型（默认用模板类型）
            discount_amount: 优惠金额
            currency: 币种
            expire_time: 过期时间
            remark: 备注
            items_override: 自定义行项目列表（覆盖模板项），每项为 dict:
                {ItemCode, ItemName, ItemDescription, ItemType, UnitPrice, Quantity, SubTotal, ItemMetadata}
            payment_config: PaymentGatewayConfig 实例（可选）

        Returns:
            Order 实例
        """
        if not template:
            raise OrderError('订单模板不能为空')
        if not buyer_user_uuid:
            raise OrderError('买家用户 UUID 不能为空')

        buyer_bytes = self._to_bytes(buyer_user_uuid)
        seller_bytes = self._to_bytes(seller_user_uuid) if seller_user_uuid else None

        # 生成订单编号
        order_no = self._generate_order_no()

        # 冻结模板快照
        template_snapshot = self._snapshot_template(template)
        level_snapshot = self._snapshot_level(level) if level else {}

        # 计算金额
        total_amount = Decimal('0')
        if level:
            total_amount = level.Price or Decimal('0')
        pay_amount = total_amount - (discount_amount or Decimal('0'))
        if pay_amount < 0:
            pay_amount = Decimal('0')

        order = Order.objects.create(
            OrderUUID=_uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            CompanyUUID=self.CompanyUUID,
            OrderNO=order_no,
            Template=template,
            Level=level,
            OrderTitle=order_title or template.TemplateName,
            OrderDescription=order_description,
            OrderType=order_type or template.TemplateType,
            BuyerUserUUID=buyer_bytes,
            SellerUserUUID=seller_bytes,
            TotalAmount=total_amount,
            DiscountAmount=discount_amount or Decimal('0'),
            PayAmount=pay_amount,
            Currency=currency,
            OrderStatus=Order.ORD_PENDING,
            PaymentConfig=payment_config,
            ExpireTime=expire_time,
            TemplateSnapshot=template_snapshot,
            LevelSnapshot=level_snapshot,
            OrderMetadata={},
            Remark=remark,
        )

        # 创建订单项
        item_list = []
        if items_override:
            for item_data in items_override:
                item_list.append(OrderItem(
                    ItemUUID=_uuid.uuid4().bytes,
                    Order=order,
                    ItemCode=item_data.get('ItemCode', ''),
                    ItemName=item_data.get('ItemName', ''),
                    ItemDescription=item_data.get('ItemDescription', ''),
                    ItemType=item_data.get('ItemType', 'default'),
                    UnitPrice=item_data.get('UnitPrice', Decimal('0')),
                    Quantity=item_data.get('Quantity', 1),
                    Subtotal=item_data.get('SubTotal') or (
                        item_data.get('UnitPrice', Decimal('0')) * item_data.get('Quantity', 1)
                    ),
                    ItemMetadata=item_data.get('ItemMetadata', {}),
                ))
        else:
            # 复制模板项
            for tpl_item in template.Items.all():
                item_list.append(OrderItem(
                    ItemUUID=_uuid.uuid4().bytes,
                    Order=order,
                    ItemCode=tpl_item.ItemCode,
                    ItemName=tpl_item.ItemName,
                    ItemDescription=tpl_item.ItemDescription,
                    ItemType=tpl_item.ItemType,
                    UnitPrice=tpl_item.UnitPrice,
                    Quantity=tpl_item.DefaultQuantity,
                    Subtotal=tpl_item.UnitPrice * tpl_item.DefaultQuantity,
                    ItemMetadata=tpl_item.ItemMetadata,
                ))
        if item_list:
            OrderItem.objects.bulk_create(item_list)

        logger.info('[OrderManager] 创建订单: %s (template=%s, buyer=%s)',
                    order_no, template.TemplateName, buyer_user_uuid)
        return order

    @transaction.atomic
    def CreateGeneric(self, order_title, buyer_user_uuid, order_type='default',
                      order_description='', seller_user_uuid=None,
                      total_amount=None, discount_amount=None, pay_amount=None,
                      currency='CNY', expire_time=None, remark='',
                      items=None, order_metadata=None):
        """通用创建订单——自动生成单号、强制 PENDING 状态

        供通用 CRUD（form_service）接入，替代裸 ``model.objects.create``，
        保证订单受状态机保护（OrderStatus 恒为 PENDING，后续流转必须走状态机端点）。

        Args:
            order_title: 订单标题（必填）
            buyer_user_uuid: 买家用户 UUID（必填）
            order_type: 订单类型（默认 'default'）
            order_description: 订单描述
            seller_user_uuid: 卖家用户 UUID（可选）
            total_amount: 总金额（Decimal/str；不传按 items 汇总，无 items 则为 0）
            discount_amount: 优惠金额
            pay_amount: 应付金额（不传 = total - discount，最低 0）
            currency: 币种
            expire_time: 过期时间
            remark: 备注
            items: 明细列表，每项 dict:
                {ItemCode, ItemName, ItemDescription, ItemType, UnitPrice, Quantity, SubTotal, ItemMetadata}
            order_metadata: 附加元数据

        Returns:
            Order 实例

        Raises:
            OrderError: order_title 或 buyer_user_uuid 缺失时
        """
        if not order_title:
            raise OrderError('订单标题不能为空')
        if not buyer_user_uuid:
            raise OrderError('买家用户 UUID 不能为空')

        buyer_bytes = self._to_bytes(buyer_user_uuid)
        seller_bytes = self._to_bytes(seller_user_uuid) if seller_user_uuid else None
        order_no = self._generate_order_no()

        # 金额计算：优先显式传值，否则按 items 汇总
        computed_total = Decimal('0')
        for item in items or []:
            computed_total += Decimal(str(item.get('UnitPrice', Decimal('0')))) * Decimal(str(item.get('Quantity', 1)))
        total_amount = total_amount if total_amount is not None else (computed_total or Decimal('0'))
        discount_amount = discount_amount or Decimal('0')
        pay_amount = pay_amount if pay_amount is not None else max(total_amount - discount_amount, Decimal('0'))

        order = Order.objects.create(
            OrderUUID=_uuid.uuid4().bytes,
            TenantUUID=self.TenantUUID,
            CompanyUUID=self.CompanyUUID,
            OrderNO=order_no,
            OrderTitle=order_title,
            OrderDescription=order_description,
            OrderType=order_type,
            BuyerUserUUID=buyer_bytes,
            SellerUserUUID=seller_bytes,
            TotalAmount=total_amount,
            DiscountAmount=discount_amount,
            PayAmount=pay_amount,
            Currency=currency,
            OrderStatus=Order.ORD_PENDING,
            ExpireTime=expire_time,
            TemplateSnapshot={},
            LevelSnapshot={},
            OrderMetadata=order_metadata or {},
            Remark=remark,
        )

        item_list = []
        for item_data in items or []:
            unit_price = Decimal(str(item_data.get('UnitPrice', Decimal('0'))))
            quantity = int(item_data.get('Quantity', 1))
            item_list.append(OrderItem(
                ItemUUID=_uuid.uuid4().bytes,
                Order=order,
                ItemCode=item_data.get('ItemCode', ''),
                ItemName=item_data.get('ItemName', ''),
                ItemDescription=item_data.get('ItemDescription', ''),
                ItemType=item_data.get('ItemType', 'default'),
                UnitPrice=unit_price,
                Quantity=quantity,
                Subtotal=item_data.get('SubTotal') or (unit_price * quantity),
                ItemMetadata=item_data.get('ItemMetadata', {}),
            ))
        if item_list:
            OrderItem.objects.bulk_create(item_list)

        logger.info('[OrderManager] 通用创建订单: %s (buyer=%s)', order_no, buyer_user_uuid)
        return order

    # ========== 状态流转方法 ==========

    def Confirm(self, order_uuid):
        return self._flow(order_uuid, Order.ORD_CONFIRMED, 'Confirm')

    def Pay(self, order_uuid, payment_transaction=None):
        order = self._flow(order_uuid, Order.ORD_PAID, 'Pay')
        if order and payment_transaction:
            order.PaymentTransaction = payment_transaction
            order.save(update_fields=['PaymentTransaction'])
        return order

    def Process(self, order_uuid):
        return self._flow(order_uuid, Order.ORD_PROCESSING, 'Process')

    def Complete(self, order_uuid):
        return self._flow(order_uuid, Order.ORD_COMPLETED, 'Complete')

    def Cancel(self, order_uuid):
        return self._flow(order_uuid, Order.ORD_CANCELLED, 'Cancel')

    def Refund(self, order_uuid):
        return self._flow(order_uuid, Order.ORD_REFUNDING, 'Refund')

    def Refunded(self, order_uuid):
        return self._flow(order_uuid, Order.ORD_REFUNDED, 'Refunded')

    def Close(self, order_uuid):
        return self._flow(order_uuid, Order.ORD_CLOSED, 'Close')

    def Reopen(self, order_uuid):
        """重新打开已关闭的订单（回到待确认状态）"""
        order = self._get_order_or_raise(order_uuid)
        if order.OrderStatus != self.REOPEN_FROM:
            raise OrderError(
                f'订单状态流转失败：当前状态为「{order.OrderStatus}」，仅允许从「{self.REOPEN_FROM}」重新打开',
                order=order,
            )
        old_status = order.OrderStatus
        order.OrderStatus = self.REOPEN_TO
        # 清空所有流转时间戳
        order.ConfirmTime = None
        order.PayTime = None
        order.CompleteTime = None
        order.CancelTime = None
        order.UpdateTime = _timezone.now()
        order.save(update_fields=[
            'OrderStatus', 'ConfirmTime', 'PayTime',
            'CompleteTime', 'CancelTime', 'UpdateTime',
        ])
        logger.info('[OrderManager] 订单重新打开: %s (%s → %s)',
                    order.OrderNO, old_status, self.REOPEN_TO)
        return order

    # ========== 内部工具方法 ==========

    def _flow(self, order_uuid, target_status, action_label):
        """通用状态流转方法"""
        order = self._get_order_or_raise(order_uuid)
        allowed_from, timestamp_field = self.FLOW_RULES[target_status]
        if order.OrderStatus not in allowed_from:
            allowed_str = ', '.join(str(s) for s in sorted(allowed_from))
            raise OrderError(
                f'订单状态流转失败：当前状态为「{order.OrderStatus}」，仅允许从「{allowed_str}」流转到「{target_status}」',
                order=order,
            )
        old_status = order.OrderStatus
        order.OrderStatus = target_status
        now = _timezone.now()
        if timestamp_field:
            setattr(order, timestamp_field, now)
        order.UpdateTime = now
        update_fields = ['OrderStatus', 'UpdateTime']
        if timestamp_field:
            update_fields.append(timestamp_field)
        order.save(update_fields=update_fields)
        logger.info('[OrderManager] 订单状态流转: %s %s (%s → %s)',
                    order.OrderNO, action_label, old_status, target_status)
        return order

    def _get_order_or_raise(self, order_uuid):
        order = self.GetOrder(order_uuid)
        if not order:
            raise OrderError(f'订单不存在或无权访问: {order_uuid}')
        return order

    def _generate_order_no(self):
        """生成唯一订单编号: ORD-YYYYMMDD-XXXXXXXX"""
        now = _timezone.now()
        random_part = _uuid.uuid4().hex[:8].upper()
        return f'ORD-{now.strftime("%Y%m%d")}-{random_part}'

    def _snapshot_template(self, template):
        """冻结模板快照"""
        return {
            'TemplateUUID': self._to_str(template.pk),
            'TemplateName': template.TemplateName,
            'TemplateType': template.TemplateType,
            'TemplateStatus': template.TemplateStatus,
        }

    def _snapshot_level(self, level):
        """冻结等级快照"""
        return {
            'LevelUUID': self._to_str(level.pk),
            'LevelCode': level.LevelCode,
            'LevelName': level.LevelName,
            'Price': str(level.Price),
            'OriginalPrice': str(level.OriginalPrice),
            'Currency': level.Currency,
            'DurationDays': level.DurationDays,
            'DurationMonths': level.DurationMonths,
        }

    @staticmethod
    def _to_bytes(value):
        if value is None:
            return None
        if hasattr(value, 'bytes'):
            return value.bytes
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            try:
                return _uuid.UUID(value).bytes
            except ValueError:
                return None
        return None

    @staticmethod
    def _to_str(value):
        if value is None:
            return None
        if isinstance(value, bytes) and len(value) == 16:
            try:
                return str(_uuid.UUID(bytes=value))
            except ValueError:
                return None
        return str(value)
