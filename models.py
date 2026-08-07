"""GVSDS SDK 数据模型模块

将 SAAS 主服务器的用户表（User）、权限表（Permission）、角色表（Role）、
用户角色关联表（UserRole）、角色权限关联表（RolePermission）、
岗位模板表（PositionTemplate）、岗位模板权限关联表（PositionTemplatePermission）、
人事职务标签表（Position）封装为独立的 Django ORM 模型，
供子服务器直接进行跨服务器 ORM 查询。

权限体系四大分层：
    1. 组织人事层：Tenant → Company → Department，员工通过 UserRoleDepartment/UserRoleWorkgroup 关联表归属部门/工作组
    2. 岗位模板层：PositionTemplate + PositionTemplatePermission，权限初始化快照
    3. 角色权限载体层：Role + RolePermission，运行时鉴权实体
    4. 鉴权规则层：操作权限校验（角色汇总）+ 数据权限校验（组织属性）

使用方式（子服务器）：
    from gvsdsdk.models import Permission, Role, UserRole, RolePermission

    # 查询所有启用的用户（User 为抽象基类，需经 _GetUserModel 获取具体模型）
    from gvsdsdk.model_utils import _GetUserModel
    UserModel = _GetUserModel()
    users = UserModel.objects.filter(UserAccountLicense=1)

    # 或配置 DatabaseRouter 后直接使用
    users = UserModel.objects.filter(UserAccountLicense=1)

设计原则：
    1. 独立：不依赖 gvsds 内部模块，可在任意 Django 项目中独立使用
    2. 非托管：managed=False，不创建/迁移表，仅映射已有表结构
    3. PascalCase：字段名、方法名遵循帕斯卡命名法
    4. 兼容：db_table、db_column 与 SAAS 主库完全一致
    5. 租户隔离：所有权限相关数据依附于独立租户空间，不同租户互相不可见

注意：User 为抽象基类，仅包含 SAAS 主库标准字段。
      业务子类应继承 User 并添加业务字段与方法（如 gvsds.apps.core.models.User）。
"""

import uuid as _uuid
import bcrypt
from django.db import models, transaction
from django.contrib.auth.models import BaseUserManager
from django.utils import timezone as _timezone
from gvsdsdk.model_base import QModel
from gvsdsdk.model_utils import UUIDObj, _GetUserModel

_BCRYPT_ROUNDS = 12  # bcrypt 计算成本因子，与 auth/crypto.py 保持一致
_BCRYPT_HASH_LEN = 60  # bcrypt 哈希固定长度


def _UtcNow():
    """获取当前 UTC 时间（兼容 Django timezone.now()）"""
    return _timezone.now()


# === UUID 字段 ===

class FixedBinaryField(models.BinaryField):
    """定长二进制字段：映射 MySQL BINARY(N)，可用于主键"""

    description = "Fixed-length binary field"

    def __init__(self, *args, **kwargs):
        self.binary_length = kwargs.pop('binary_length', 16)
        kwargs.setdefault('editable', False)
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        if connection.vendor == 'mysql':
            return f'BINARY({self.binary_length})'
        elif connection.vendor == 'sqlite':
            return 'BLOB'
        elif connection.vendor == 'postgresql':
            return 'BYTEA'
        return f'BINARY({self.binary_length})'

    def rel_db_type(self, connection):
        return self.db_type(connection)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs['binary_length'] = self.binary_length
        return name, path, args, kwargs


class UUIDField(FixedBinaryField):
    """UUID 字段：数据库存储为 BINARY(16)，Python 侧自动转换 str/bytes/uuid.UUID"""

    description = "UUID stored as BINARY(16)"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('editable', False)
        super().__init__(*args, **kwargs)

    def db_type(self, connection):
        vendor = connection.vendor
        if vendor == 'mysql':
            return 'BINARY(16)'
        elif vendor == 'sqlite':
            return 'BLOB'
        elif vendor == 'postgresql':
            return 'BYTEA'
        return 'BINARY(16)'

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if isinstance(value, UUIDObj):
            return value
        return UUIDObj(value)

    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, UUIDObj):
            return value.bytes
        if isinstance(value, str):
            try:
                return _uuid.UUID(value).bytes
            except ValueError as e:
                raise ValueError(f'无效的 UUID 字符串: {value!r}') from e
        if isinstance(value, _uuid.UUID):
            return value.bytes
        if isinstance(value, bytes):
            return value
        return None

    def get_prep_lookup(self, lookup_type, value):
        if lookup_type in ('exact', 'iexact', 'in'):
            if value is None:
                return None
            if lookup_type == 'in' and isinstance(value, (list, tuple)):
                return [self.get_prep_value(v) for v in value]
            return self.get_prep_value(value)
        return super().get_prep_lookup(lookup_type, value)

    def value_to_string(self, obj):
        value = self.value_from_object(obj)
        if isinstance(value, UUIDObj):
            return value.str or ''
        if isinstance(value, str):
            return value
        if isinstance(value, bytes) and len(value) == 16:
            return str(_uuid.UUID(bytes=value))
        if isinstance(value, _uuid.UUID):
            return str(value)
        return ''

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop('max_length', None)
        return name, path, args, kwargs


# === QModel 基础模型 ===

# === 用户管理器 ===

class UserManager(BaseUserManager):
    """用户管理器——提供用户创建与超级用户创建。
    方法名保留蛇形命名以兼容 Django 内部约定。"""

    def create_user(self, username, password=None, email='', **extra_fields):
        """创建普通用户"""
        if not username:
            raise ValueError('The Username field must be set')
        user = self.model(
            UserUUID=_uuid.uuid4().bytes,
            UserName=username,
            UserEmail=email or '',
            **extra_fields
        )
        if password:
            user.SetPassword(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        """创建超级用户"""
        extra_fields.setdefault('UserAccountLicense', 1)
        extra_fields.setdefault('UserPositionStatus', 0)
        extra_fields.setdefault('IsStaff', True)
        extra_fields.setdefault('IsSuperuser', True)
        return self.create_user(username, password, **extra_fields)


# === 用户表 ===
class User(QModel):
    """用户表——系统核心身份模型（抽象基类，映射 SAAS 主库 User 表标准字段）

    本类为抽象基类，仅包含 SAAS 主库 User 表的标准字段和方法。
    业务子类应继承本类并添加业务字段与方法（如 gvsds.apps.core.models.User）。
    """

    UserUUID = UUIDField(primary_key=True, db_column='UserUUID', verbose_name='用户UUID', default=_uuid.uuid4)
    UserName = models.CharField(max_length=256, unique=True, db_column='UserName', verbose_name='用户名')
    UserPassword = models.CharField(max_length=60, default='', db_column='UserPassword', verbose_name='密码')
    UserPositionStatus = models.SmallIntegerField(default=0, db_column='UserPositionStatus', verbose_name='职位状态')
    UserPersonalSignature = models.CharField(max_length=1024, default='', db_column='UserPersonalSignature', verbose_name='个人签名')
    UserEmail = models.CharField(max_length=256, default='', db_column='UserEmail', verbose_name='邮箱')
    UserEmailVerify = models.SmallIntegerField(default=0, db_column='UserEmailVerify', verbose_name='邮箱验证')
    UserAccountLicense = models.SmallIntegerField(default=1, db_column='UserAccountLicense', verbose_name='账户许可')
    UserGender = models.SmallIntegerField(default=-1, db_column='UserGender', verbose_name='性别')
    UserTele = models.CharField(max_length=16, null=True, blank=True, db_column='UserTele', verbose_name='电话')
    UserCreateTime = models.DateTimeField(default=_UtcNow, db_column='UserCreateTime', verbose_name='创建时间')
    UserLastLoginDate = models.DateTimeField(default=_UtcNow, db_column='UserLastLoginDate', verbose_name='最后登录')
    IsStaff = models.BooleanField(default=False, db_column='IsStaff', verbose_name='是否员工')
    IsSuperuser = models.BooleanField(default=False, db_column='IsSuperuser', verbose_name='是否超级用户')

    # Django auth 框架必需（由具体子类覆盖）
    USERNAME_FIELD = 'UserName'
    REQUIRED_FIELDS = []

    class Meta:
        abstract = True

    def __str__(self):
        return self.UserName

    @property
    def UUIDStr(self):
        """返回 UUID 字符串形式"""
        return UUIDObj(self.UserUUID).str

    @property
    def IsActive(self):
        """账户是否启用（UserAccountLicense == 1）"""
        return self.UserAccountLicense == 1

    @property
    def IsOnJob(self):
        """是否在职（UserPositionStatus == 0）"""
        return self.UserPositionStatus == 0

    @property
    def IsAnonymous(self):
        return False

    @property
    def IsAuthenticated(self):
        return True

    @property
    def PK(self):
        return UUIDObj(self.UserUUID)

    @property
    def ID(self):
        return UUIDObj(self.UserUUID)

    def SetPassword(self, RawPassword):
        """设置密码——bcrypt 哈希"""
        salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        hashed = bcrypt.hashpw(RawPassword.encode('utf-8'), salt)
        self.UserPassword = hashed.decode('utf-8')

    def CheckPassword(self, RawPassword):
        """验证密码——兼容 bcrypt 哈希和明文存储（明文仅用于旧数据迁移期）"""
        import hmac as _hmac
        import logging as _logging
        stored = self.UserPassword or ''
        # bcrypt 哈希以 $2b$ 或 $2a$ 开头，长度固定 60
        if stored.startswith(('$2b$', '$2a$')) and len(stored) == _BCRYPT_HASH_LEN:
            try:
                return bcrypt.checkpw(
                    RawPassword.encode('utf-8'),
                    stored.encode('utf-8')
                )
            except (ValueError, TypeError):
                return False
        else:
            # 明文密码（旧数据兼容）——记录警告，建议尽快迁移为 bcrypt 哈希
            # 生产环境可通过 settings.GVSDSDK_DISALLOW_PLAINTEXT_PASSWORD=True 关闭明文密码验证
            from django.conf import settings
            if getattr(settings, 'GVSDSDK_DISALLOW_PLAINTEXT_PASSWORD', False):
                _logging.getLogger(__name__).warning(
                    'User %s 密码仍为明文存储，生产环境已禁用明文密码验证',
                    getattr(self, 'UserUUID', 'unknown'),
                )
                return False
            _logging.getLogger(__name__).warning(
                'User %s 密码仍为明文存储，请尽快通过 hash_passwords 命令迁移为 bcrypt 哈希',
                getattr(self, 'UserUUID', 'unknown'),
            )
            return _hmac.compare_digest(stored, RawPassword)

    def GetTele(self):
        """获取电话，空则返回默认文本"""
        return self.UserTele or '未填写'

    def GetCompanies(self):
        """获取用户所属公司列表"""
        # Company 模型不在 gvsdsdk 中，此处返回空 QuerySet
        # 如需使用，请在子服务器中自行实现
        return []

    def GetRoles(self):
        """获取用户角色列表（需在同一 database 连接下使用）"""
        user_role_uuids = UserRole.objects.filter(
            UserUUID=self.UserUUID
        ).values_list('RoleUUID', flat=True)
        return Role.objects.filter(RoleUUID__in=user_role_uuids)

    def GetDepartments(self):
        """获取用户部门列表"""
        # Department 模型不在 gvsdsdk 中，此处返回空列表
        # 如需使用，请在子服务器中自行实现
        return []

    def HasPermission(self, PermCode):
        """检查用户是否拥有指定权限（需在同一 database 连接下使用）

        通过 UserRole → RolePermission → Permission 链路查询，
        汇总所有角色的操作权限后判断。
        """
        user_role_uuids = UserRole.objects.filter(
            UserUUID=self.UserUUID
        ).values_list('RoleUUID', flat=True)
        perm_uuids = RolePermission.objects.filter(
            RoleUUID__in=user_role_uuids
        ).values_list('PermUUID', flat=True)
        return Permission.objects.filter(
            PermUUID__in=perm_uuids,
            PermCode=PermCode,
            PermStatus=1,
        ).exists()

    # ── Django auth 框架必需属性 ──
    @property
    def is_active(self):
        return self.IsActive

    @property
    def is_anonymous(self):
        return self.IsAnonymous

    @property
    def is_authenticated(self):
        return self.IsAuthenticated

    @property
    def pk(self):
        return self.PK

    @property
    def id(self):
        return self.ID

    def set_password(self, raw_password):
        self.SetPassword(raw_password)

    def check_password(self, raw_password):
        return self.CheckPassword(raw_password)


# === 用户头像表 ===

class UserAvatar(QModel):
    """用户头像表（映射 SAAS 主库 UserAvatar 表）"""

    UserUUID = UUIDField(primary_key=True, db_column='UserUUID', verbose_name='用户UUID')
    UserAvatar = models.BinaryField(editable=True, db_column='UserAvatar', verbose_name='头像数据')
    AvatarUploadTime = models.DateTimeField(default=_UtcNow, db_column='AvatarUploadTime', verbose_name='上传时间')
    AvatarFileType = models.CharField(max_length=16, default='png', db_column='AvatarFileType', verbose_name='文件类型')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserAvatar'
        verbose_name = '用户头像'
        verbose_name_plural = '用户头像'


# === 用户双因素认证表 ===

class User2FA(QModel):
    """用户双因素认证表（映射 SAAS 主库 User2FA 表）"""

    User2FAUUID = UUIDField(primary_key=True, db_column='User2FAUUID', verbose_name='2FA UUID')
    UserUUID = UUIDField(db_index=True, db_column='UserUUID', verbose_name='用户UUID')
    SecretKey = models.CharField(max_length=64, db_column='SecretKey', verbose_name='密钥')
    IsEnabled = models.SmallIntegerField(default=0, db_column='IsEnabled', verbose_name='是否启用')
    Verified = models.SmallIntegerField(default=0, db_column='Verified', verbose_name='已验证')
    LastUsedAt = models.DateTimeField(null=True, blank=True, db_column='LastUsedAt', verbose_name='最后使用')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'User2FA'
        verbose_name = '双因素认证'
        verbose_name_plural = '双因素认证'


# === 双因素认证待验证表 ===

class UserPending2FA(QModel):
    """双因素认证待验证表（映射 SAAS 主库 UserPending2FA 表）"""

    PendingUUID = UUIDField(primary_key=True, db_column='PendingUUID', verbose_name='待确认UUID')
    UserUUID = UUIDField(unique=True, db_column='UserUUID', verbose_name='用户UUID')
    SecretKey = models.CharField(max_length=64, db_column='SecretKey', verbose_name='密钥')
    ExpireAt = models.DateTimeField(db_column='ExpireAt', verbose_name='过期时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserPending2FA'
        verbose_name = '2FA待确认'
        verbose_name_plural = '2FA待确认'


# === 用户登录日志表 ===

class UserLoginLog(QModel):
    """用户登录日志表（映射 SAAS 主库 UserLoginLog 表）"""

    LogUUID = UUIDField(primary_key=True, db_column='LogUUID', verbose_name='日志UUID')
    UserUUID = UUIDField(db_index=True, db_column='UserUUID', verbose_name='用户UUID')
    LoginIP = models.CharField(max_length=39, db_column='LoginIP', verbose_name='登录IP')
    LoginPlatform = models.CharField(max_length=32, db_column='LoginPlatform', verbose_name='登录平台')
    LoginStatus = models.SmallIntegerField(db_column='LoginStatus', verbose_name='登录状态')
    LoginTime = models.DateTimeField(default=_UtcNow, db_column='LoginTime', verbose_name='登录时间')
    UserAgent = models.CharField(max_length=512, default='', db_column='UserAgent', verbose_name='用户代理')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserLoginLog'
        verbose_name = '登录日志'
        verbose_name_plural = '登录日志'


# === 账户激活码表 ===

class AccountActivation(QModel):
    """账户激活码表（映射 SAAS 主库 AccountActivation 表）"""

    AccountActivationCode = FixedBinaryField(primary_key=True, binary_length=6, db_column='AccountActivationCode', verbose_name='激活码')
    UserUUID = UUIDField(db_column='UserUUID', verbose_name='用户UUID')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'AccountActivation'
        verbose_name = '账户激活'
        verbose_name_plural = '账户激活'


# === 租户表 ===

class Tenant(QModel):
    """租户表——多租户架构核心（映射 SAAS 主库 Tenant 表）"""

    TenantUUID = UUIDField(primary_key=True, db_column='TenantUUID', verbose_name='租户UUID')
    TenantName = models.CharField(max_length=256, db_column='TenantName', verbose_name='租户名称')
    TenantStatus = models.SmallIntegerField(default=1, db_column='TenantStatus', verbose_name='租户状态')
    TenantExpireTime = models.DateTimeField(db_column='TenantExpireTime', verbose_name='到期时间')
    ReviewStatus = models.SmallIntegerField(default=0, db_column='ReviewStatus', verbose_name='审核状态')
    ReviewRemark = models.CharField(max_length=512, default='', db_column='ReviewRemark', verbose_name='审核备注')
    ReviewerUUID = UUIDField(null=True, blank=True, db_column='ReviewerUUID', verbose_name='审核人UUID')
    ReviewTime = models.DateTimeField(null=True, blank=True, db_column='ReviewTime', verbose_name='审核时间')
    CreateTime = models.DateTimeField(default=_UtcNow, db_column='CreateTime', verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Tenant'
        verbose_name = '租户'
        verbose_name_plural = '租户'

    def __str__(self):
        return self.TenantName


# === 公司/部门表 ===

class Company(QModel):
    """公司表——系统内组织机构（映射 SAAS 主库 Company 表）"""

    CompanyUUID = UUIDField(primary_key=True, db_column='CompanyUUID', verbose_name='公司UUID')
    TenantUUID = UUIDField(db_column='TenantUUID', verbose_name='租户UUID')
    ParentCompanyUUID = UUIDField(null=True, blank=True, db_column='ParentCompanyUUID', verbose_name='上级公司UUID')
    CompanyName = models.CharField(max_length=256, db_column='CompanyName', verbose_name='公司全称')
    CompanyShort = models.CharField(max_length=100, db_column='CompanyShort', verbose_name='公司简称')
    CompanyIcon = models.CharField(max_length=512, null=True, blank=True, db_column='CompanyIcon', verbose_name='公司图标')
    CompanyStatus = models.SmallIntegerField(default=1, db_column='CompanyStatus', verbose_name='公司状态')
    CreateTime = models.DateTimeField(default=_UtcNow, db_column='CreateTime', verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Company'
        verbose_name = '公司'
        verbose_name_plural = '公司'

    def __str__(self):
        return self.CompanyName


class Department(QModel):
    """部门表——组织架构层级（映射 SAAS 主库 Dept 表）"""

    DeptUUID = UUIDField(primary_key=True, db_column='DeptUUID', verbose_name='部门UUID')
    CompanyUUID = UUIDField(db_column='CompanyUUID', verbose_name='公司UUID')
    ParentDeptUUID = UUIDField(null=True, blank=True, db_column='ParentDeptUUID', verbose_name='上级部门UUID')
    DeptName = models.CharField(max_length=256, db_column='DeptName', verbose_name='部门名称')
    DeptSort = models.IntegerField(default=0, db_column='DeptSort', verbose_name='排序')
    DeptStatus = models.SmallIntegerField(default=1, db_column='DeptStatus', verbose_name='部门状态')
    CreateTime = models.DateTimeField(default=_UtcNow, db_column='CreateTime', verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Dept'
        verbose_name = '部门'
        verbose_name_plural = '部门'

    def __str__(self):
        return self.DeptName


class UserRoleDepartment(QModel):
    """用户-角色对 与 部门 的多对多关联表（映射 SAAS 主库 UserRoleDepartment 表）"""

    UserRoleDepartmentUUID = UUIDField(primary_key=True, db_column='UserRoleDepartmentUUID', verbose_name='关联UUID')
    UserRoleUUID = UUIDField(db_column='UserRoleUUID', verbose_name='用户-角色关联UUID')
    DeptUUID = UUIDField(db_column='DeptUUID', verbose_name='部门UUID')
    CompanyUUID = UUIDField(db_column='CompanyUUID', verbose_name='公司UUID')
    RoleType = models.SmallIntegerField(default=0, db_column='RoleType', verbose_name='角色类型')
    CreateTime = models.DateTimeField(default=_UtcNow, db_column='CreateTime', verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserRoleDepartment'
        verbose_name = '用户-角色部门关联'
        verbose_name_plural = '用户-角色部门关联'


# === 消息表 ===

class Message(QModel):
    """消息表——系统通知与站内信（映射 SAAS 主库 Message 表）"""

    MessageUUID = UUIDField(primary_key=True, db_column='MessageUUID', verbose_name='消息UUID')
    SenderUUID = UUIDField(null=True, blank=True, db_column='SenderUUID', verbose_name='发送者UUID')
    ReceiverUUID = UUIDField(db_index=True, db_column='ReceiverUUID', verbose_name='接收者UUID')
    Title = models.CharField(max_length=256, db_column='Title', verbose_name='标题')
    Content = models.TextField(default='', db_column='Content', verbose_name='内容')
    MsgType = models.SmallIntegerField(default=0, db_column='MsgType', verbose_name='消息类型')
    IsRead = models.SmallIntegerField(default=0, db_column='IsRead', verbose_name='已读')
    RelatedUUID = models.CharField(max_length=64, default='', db_column='RelatedUUID', verbose_name='关联UUID')
    RelatedType = models.CharField(max_length=32, default='', db_column='RelatedType', verbose_name='关联类型')
    CreateTime = models.DateTimeField(default=_UtcNow, db_column='CreateTime', verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Message'
        verbose_name = '消息'
        verbose_name_plural = '消息'

    def __str__(self):
        return self.Title


# === 个人中心表 ===

class UserIDCard(QModel):
    """用户身份证表——实名信息（映射 SAAS 主库 UserIDCard 表）"""

    UserUUID = UUIDField(primary_key=True, db_column='UserUUID', verbose_name='用户UUID')
    Name = models.CharField(max_length=128, db_column='Name', verbose_name='姓名')
    OldName = models.CharField(max_length=128, default='', db_column='OldName', verbose_name='曾用名')
    Born = models.DateField(db_column='Born', verbose_name='出生日期')
    Sex = models.CharField(max_length=10, db_column='Sex', verbose_name='性别')
    Ethnic = models.CharField(max_length=20, db_column='Ethnic', verbose_name='民族')
    IdentificationNumber = models.CharField(max_length=20, db_column='IdentificationNumber', verbose_name='身份证号')
    StudentRegistrationNumber = models.CharField(max_length=30, default='', db_column='StudentRegistrationNumber', verbose_name='学籍号')
    ChangeStudentStatus = models.JSONField(default=list, db_column='ChangeStudentStatus', verbose_name='学籍变动')
    CorrespondenceAddress = models.JSONField(default=list, db_column='CorrespondenceAddress', verbose_name='通讯地址')
    HeadImage = models.CharField(max_length=4096, default='', db_column='HeadImage', verbose_name='头像')
    InformationAboutFamilyMembers = models.JSONField(default=dict, db_column='InformationAboutFamilyMembers', verbose_name='家庭成员信息')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserIDCard'
        verbose_name = '用户身份证'
        verbose_name_plural = '用户身份证'


class PersonalPage(QModel):
    """个人主页表——用户展示页面（映射 SAAS 主库 PersonalPage 表）"""

    PersonalPageUserUUID = UUIDField(primary_key=True, db_column='PersonalPageUserUUID', verbose_name='用户UUID')
    PersonalPageName = models.CharField(max_length=256, unique=True, db_column='PersonalPageName', verbose_name='主页名称')
    Subtitle = models.CharField(max_length=256, default='', db_column='Subtitle', verbose_name='副标题')
    BackgroundLink = models.CharField(max_length=256, default='', db_column='BackgroundLink', verbose_name='背景链接')
    Classification = models.CharField(max_length=256, default='', db_column='Classification', verbose_name='分类')
    Github = models.CharField(max_length=256, default='', db_column='Github', verbose_name='GitHub')
    Wechat = models.CharField(max_length=256, default='', db_column='Wechat', verbose_name='微信')
    QQ = models.CharField(max_length=256, default='', db_column='QQ', verbose_name='QQ')
    SelfIntroduction = models.TextField(default='', db_column='SelfIntroduction', verbose_name='自我介绍')
    ProjectExperience = models.CharField(max_length=256, default='', db_column='ProjectExperience', verbose_name='项目经验')
    SatisfiedCustomers = models.CharField(max_length=256, default='', db_column='SatisfiedCustomers', verbose_name='满意客户')
    ReceiveAward = models.CharField(max_length=256, default='', db_column='ReceiveAward', verbose_name='获奖')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'PersonalPage'
        verbose_name = '个人主页'
        verbose_name_plural = '个人主页'


class UserSettings(QModel):
    """用户设置表——存储每位用户的个性化系统设置（映射 SAAS 主库 UserSettings 表）"""

    UserUUID = UUIDField(primary_key=True, db_column='UserUUID', verbose_name='用户UUID')
    SettingsData = models.TextField(default='', db_column='SettingsData', verbose_name='设置数据')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserSettings'
        verbose_name = '用户设置'
        verbose_name_plural = '用户设置'


# === 角色表 ===

class Role(QModel):
    """角色表——权限分配单元（映射 SAAS 主库 Role 表）

    角色是真正承载操作权限的实体，在单个租户内部独立存在，
    不依附任何公司或部门。通过 AssignScope 控制角色的分配可见范围：
      - 0：全租户管理员可分发
      - 1：仅指定公司管理员可分发（AssignScopeUUID = CompanyUUID）
      - 2：仅指定部门管理员可分发（AssignScopeUUID = DeptUUID）
    """

    RoleUUID = UUIDField(primary_key=True, db_column='RoleUUID', verbose_name='角色UUID')
    TenantUUID = UUIDField(db_column='TenantUUID', verbose_name='租户UUID')
    RoleName = models.CharField(max_length=128, db_column='RoleName', verbose_name='角色名称')
    RoleType = models.SmallIntegerField(default=2, db_column='RoleType', verbose_name='角色类型')
    RoleStatus = models.SmallIntegerField(default=1, db_column='RoleStatus', verbose_name='角色状态')
    AssignScope = models.SmallIntegerField(default=0, db_column='AssignScope', verbose_name='分配可见范围')
    AssignScopeUUID = UUIDField(null=True, blank=True, db_column='AssignScopeUUID', verbose_name='分配范围UUID')
    RoleDesc = models.CharField(max_length=512, default='', db_column='RoleDesc', verbose_name='角色描述')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Role'
        verbose_name = '角色'
        verbose_name_plural = '角色'

    def __str__(self):
        return self.RoleName

    def GetPermissions(self):
        """获取角色拥有的权限列表（通过 RolePermission 中间表）"""
        perm_uuids = RolePermission.objects.filter(
            RoleUUID=self.RoleUUID
        ).values_list('PermUUID', flat=True)
        return Permission.objects.filter(PermUUID__in=perm_uuids, PermStatus=1)

    def GetUsers(self):
        """获取拥有该角色的用户列表"""
        UserModel = _GetUserModel()
        user_uuids = UserRole.objects.filter(
            RoleUUID=self.RoleUUID
        ).values_list('UserUUID', flat=True)
        return UserModel.objects.filter(UserUUID__in=user_uuids)


# === 权限表 ===

class Permission(QModel):
    """权限表——操作权限定义（映射 SAAS 主库 Permission 表）

    独立的权限定义表，租户内全局可用，不再挂在角色下。
    通过 RolePermission 中间表与角色建立多对多关系，
    通过 PositionTemplatePermission 中间表与岗位模板建立多对多关系。
    """

    PermUUID = UUIDField(primary_key=True, db_column='PermUUID', verbose_name='权限UUID')
    TenantUUID = UUIDField(db_column='TenantUUID', verbose_name='租户UUID')
    PermName = models.CharField(max_length=128, db_column='PermName', verbose_name='权限名称')
    PermCode = models.CharField(max_length=128, db_column='PermCode', verbose_name='权限键')
    PermCategory = models.CharField(max_length=64, default='', db_column='PermCategory', verbose_name='权限分类')
    PermStatus = models.SmallIntegerField(default=1, db_column='PermStatus', verbose_name='是否启用')
    PermDesc = models.CharField(max_length=512, default='', db_column='PermDesc', verbose_name='描述')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Permission'
        verbose_name = '权限'
        verbose_name_plural = '权限'

    def __str__(self):
        return f"{self.PermName} ({self.PermCode})"

    def GetRoles(self):
        """获取拥有该权限的角色列表"""
        role_uuids = RolePermission.objects.filter(
            PermUUID=self.PermUUID
        ).values_list('RoleUUID', flat=True)
        return Role.objects.filter(RoleUUID__in=role_uuids)


# === 用户角色关联表 ===

class UserRole(QModel):
    """用户角色关联表——用户与角色的多对多映射（映射 SAAS 主库 UserRole 表）

    通过 CompanyUUID 标识该用户-角色关联所属的公司，
    用于菜单按公司差异化显示、数据权限按公司过滤等场景。
    同一用户在不同公司下可绑定不同角色。
    RoleType 标识该用户在部门/工作组中的角色类型：0=普通成员/部门员工，1=组长/部门负责人。
    RoleName 为可选的角色名覆盖：非空时前端展示用此名，为空时回退到 Role.RoleName。
    这避免了为每个用户单独建角色，又能让同角色在不同用户视角下显示差异化名称。

    注意：DeptUUID、WorkgroupUUID 为遗留字段（已废弃）——部门/工作组归属已迁移到
    UserRoleDepartment/UserRoleWorkgroup 关联表，字段仅保留兼容存量数据，禁止再次写入。

    注意：UserUUID/RoleUUID/CompanyUUID/DeptUUID/WorkgroupUUID 在 gvsds 中是 ForeignKey，
    但此处改为 UUIDField 以保持独立性（不支持 select_related 和关联属性访问）。
    """

    UserRoleUUID = UUIDField(primary_key=True, db_column='UserRoleUUID', verbose_name='关联UUID')
    UserUUID = UUIDField(db_column='UserUUID', verbose_name='用户UUID')
    RoleUUID = UUIDField(db_column='RoleUUID', verbose_name='角色UUID')
    CompanyUUID = UUIDField(db_column='CompanyUUID', verbose_name='公司UUID')
    DeptUUID = UUIDField(null=True, blank=True, db_column='DeptUUID', verbose_name='部门UUID')
    WorkgroupUUID = UUIDField(null=True, blank=True, db_column='WorkgroupUUID', verbose_name='工作组UUID')
    RoleType = models.SmallIntegerField(default=0, db_column='RoleType', verbose_name='角色类型')
    RoleName = models.CharField(max_length=128, null=True, blank=True, db_column='RoleName', verbose_name='角色名覆盖')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserRole'
        verbose_name = '用户角色关联'
        verbose_name_plural = '用户角色关联'


# === 角色权限关联表 ===

class RolePermission(QModel):
    """角色权限关联表——角色与操作权限的多对多映射（映射 SAAS 主库 RolePermission 表）

    一个角色可以拥有多个权限，一个权限也可以被多个角色引用。
    角色和具体操作权限为多对多绑定关系，一套权限集合可以打包在一个角色内统一维护。
    """

    RolePermUUID = UUIDField(primary_key=True, db_column='RolePermUUID', verbose_name='关联UUID')
    CompanyUUID = UUIDField(db_column='CompanyUUID', verbose_name='公司UUID')
    RoleUUID = UUIDField(db_column='RoleUUID', verbose_name='角色UUID')
    PermUUID = UUIDField(db_column='PermUUID', verbose_name='权限UUID')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'RolePermission'
        verbose_name = '角色权限关联'
        verbose_name_plural = '角色权限关联'


# === 用户 ABAC 表 ===

class UserABAC(QModel):
    """用户 ABAC 表——基于属性的访问控制（映射 SAAS 主库 UserABAC 表）"""

    UserABACUUID = UUIDField(primary_key=True, db_column='UserABACUUID', verbose_name='ABAC UUID')
    UserUUID = UUIDField(db_column='UserUUID', verbose_name='用户UUID')
    HierarchicalPermissionIdentifiers = models.CharField(max_length=256, db_column='HierarchicalPermissionIdentifiers', verbose_name='层级权限标识')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'UserABAC'
        verbose_name = '用户ABAC'
        verbose_name_plural = '用户ABAC'


# === 密码表 ===

class Password(QModel):
    """密码表——独立密码存储（映射 SAAS 主库 Password 表）"""

    UserUUID = UUIDField(primary_key=True, db_column='UserUUID', verbose_name='用户UUID')
    Password = models.CharField(max_length=1024, db_column='Password', verbose_name='密码哈希')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Password'
        verbose_name = '密码'
        verbose_name_plural = '密码'


# === 商铺表 ===

class Shop(QModel):
    """商铺表——SAAS 多商铺体系中的商铺实体

    每个商铺属于一个公司（CompanyUUID），可关联多个角色（通过 RoleShop）。
    """

    ShopUUID = UUIDField(primary_key=True, db_column='ShopUUID', verbose_name='商铺UUID')
    CompanyUUID = UUIDField(db_column='CompanyUUID', verbose_name='公司UUID')
    ShopName = models.CharField(max_length=128, db_column='ShopName', verbose_name='商铺名称')
    ShopCode = models.CharField(max_length=64, default='', db_column='ShopCode', verbose_name='商铺编码')
    ShopType = models.SmallIntegerField(default=0, db_column='ShopType', verbose_name='商铺类型')
    ShopStatus = models.SmallIntegerField(default=1, db_column='ShopStatus', verbose_name='商铺状态')
    ShopAddress = models.CharField(max_length=512, default='', db_column='ShopAddress', verbose_name='商铺地址')
    ShopContact = models.CharField(max_length=128, default='', db_column='ShopContact', verbose_name='联系人')
    ShopPhone = models.CharField(max_length=32, default='', db_column='ShopPhone', verbose_name='联系电话')
    ShopDesc = models.CharField(max_length=512, default='', db_column='ShopDesc', verbose_name='描述')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(default=_UtcNow, verbose_name='更新时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Shop'
        verbose_name = '商铺'
        verbose_name_plural = '商铺'

    def __str__(self):
        return self.ShopName

    def GetRoles(self):
        """获取商铺关联的所有角色"""
        role_uuids = RoleShop.objects.filter(
            ShopUUID=self.ShopUUID
        ).values_list('RoleUUID', flat=True)
        return Role.objects.filter(RoleUUID__in=role_uuids)


# === 角色商铺关联表 ===

class RoleShop(QModel):
    """角色商铺关联表——角色与商铺的多对多映射

    一个角色可以管理多个商铺，一个商铺也可以被多个角色管理。
    """

    RoleShopUUID = UUIDField(primary_key=True, db_column='RoleShopUUID', verbose_name='关联UUID')
    RoleUUID = UUIDField(db_column='RoleUUID', verbose_name='角色UUID')
    ShopUUID = UUIDField(db_column='ShopUUID', verbose_name='商铺UUID')
    IsMainShop = models.SmallIntegerField(default=0, db_column='IsMainShop', verbose_name='主商铺')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'RoleShop'
        verbose_name = '角色商铺关联'
        verbose_name_plural = '角色商铺关联'


# === 岗位模板表 ===

class PositionTemplate(QModel):
    """岗位模板表——权限初始化快照模板（映射 SAAS 主库 PositionTemplate 表）

    租户内全局可重复使用，定位是权限初始化快照模板，
    不直接分配给员工、不参与运行时鉴权。
    新建角色时可一键复制模板内的全部权限内容生成新角色。
    模板后续调整只会作用于之后新创建的角色，
    已有角色不会同步变更，实现模板复用和存量权限独立管控互不干扰。
    """

    PositionTemplateUUID = UUIDField(primary_key=True, db_column='PositionTemplateUUID', verbose_name='岗位模板UUID')
    TenantUUID = UUIDField(db_column='TenantUUID', verbose_name='租户UUID')
    TemplateName = models.CharField(max_length=128, db_column='TemplateName', verbose_name='模板名称')
    TemplateCode = models.CharField(max_length=64, default='', db_column='TemplateCode', verbose_name='模板编码')
    TemplateStatus = models.SmallIntegerField(default=1, db_column='TemplateStatus', verbose_name='模板状态')
    TemplateDesc = models.CharField(max_length=512, default='', db_column='TemplateDesc', verbose_name='模板描述')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(default=_UtcNow, verbose_name='更新时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'PositionTemplate'
        verbose_name = '岗位模板'
        verbose_name_plural = '岗位模板'

    def __str__(self):
        return self.TemplateName

    def GetPermissions(self):
        """获取模板关联的权限列表"""
        perm_uuids = PositionTemplatePermission.objects.filter(
            PositionTemplateUUID=self.PositionTemplateUUID
        ).values_list('PermUUID', flat=True)
        return Permission.objects.filter(PermUUID__in=perm_uuids, PermStatus=1)

    def CopyToRole(self, RoleName, TenantUUID, CompanyUUID=None):
        """从模板复制权限到新角色（一次性复制，后续模板变更不影响已有角色）

        Args:
            RoleName: 新角色名称
            TenantUUID: 新角色所属租户
            CompanyUUID: 复制出的 RolePermission 所属公司。
                缺省用系统公司 UUID（与主项目 RolePermission 创建一致），
                RolePermission.CompanyUUID 为非空字段，漏传会导致插入失败。
        """
        with transaction.atomic():
            role = Role.objects.create(
                RoleUUID=_uuid.uuid4().bytes,
                TenantUUID=TenantUUID,
                RoleName=RoleName,
            )
            if CompanyUUID is None:
                CompanyUUID = _uuid.UUID('00000000-0000-0000-0000-000000000001')
            template_perms = PositionTemplatePermission.objects.filter(
                PositionTemplateUUID=self.PositionTemplateUUID
            )
            RolePermission.objects.bulk_create([
                RolePermission(
                    RolePermUUID=_uuid.uuid4().bytes,
                    RoleUUID=role.RoleUUID,
                    CompanyUUID=CompanyUUID,
                    PermUUID=tp.PermUUID,
                )
                for tp in template_perms
            ])
            return role


# === 岗位模板权限关联表 ===

class PositionTemplatePermission(QModel):
    """岗位模板权限关联表——岗位模板与操作权限的多对多映射

    仅作为复制源头，不参与运行时鉴权。
    新建角色时一键复制模板权限到角色，复制后模板与角色无运行时绑定。
    """

    PTPermUUID = UUIDField(primary_key=True, db_column='PTPermUUID', verbose_name='关联UUID')
    PositionTemplateUUID = UUIDField(db_column='PositionTemplateUUID', verbose_name='岗位模板UUID')
    PermUUID = UUIDField(db_column='PermUUID', verbose_name='权限UUID')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'PositionTemplatePermission'
        verbose_name = '岗位模板权限关联'
        verbose_name_plural = '岗位模板权限关联'


# === 人事职务标签表 ===

class Position(QModel):
    """人事职务标签表——仅用于人事管理标注（映射 SAAS 主库 Position 表）

    员工会标注对应的职务标签，该标签仅为人事标注信息，
    不直接绑定权限，和角色、权限体系完全解耦。
    人员调岗、升职只修改组织归属和人事职务标签，权限只需增删绑定角色即可，互不干扰。
    """

    PositionUUID = UUIDField(primary_key=True, db_column='PositionUUID', verbose_name='职务UUID')
    TenantUUID = UUIDField(db_column='TenantUUID', verbose_name='租户UUID')
    PositionName = models.CharField(max_length=128, db_column='PositionName', verbose_name='职务名称')
    PositionCode = models.CharField(max_length=64, default='', db_column='PositionCode', verbose_name='职务编码')
    PositionLevel = models.SmallIntegerField(default=0, db_column='PositionLevel', verbose_name='职务级别')
    PositionStatus = models.SmallIntegerField(default=1, db_column='PositionStatus', verbose_name='状态')
    PositionDesc = models.CharField(max_length=512, default='', db_column='PositionDesc', verbose_name='描述')
    CreateTime = models.DateTimeField(default=_UtcNow, verbose_name='创建时间')
    UpdateTime = models.DateTimeField(default=_UtcNow, verbose_name='更新时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Position'
        verbose_name = '人事职务标签'
        verbose_name_plural = '人事职务标签'

    def __str__(self):
        return self.PositionName

# （UserDept 表已废弃——员工部门归属已迁移到 UserRoleDepartment 关联表，不再用独立表）


# === 菜单表 ===

class Menu(QModel):
    """菜单表——系统导航与功能入口（映射 SAAS 主库 Menu 表）"""

    MenuUUID = UUIDField(primary_key=True, db_column='MenuUUID', verbose_name='菜单UUID')
    CompanyUUID = UUIDField(null=True, blank=True, db_column='CompanyUUID', verbose_name='公司UUID')
    ParentMenuUUID = UUIDField(null=True, blank=True, db_column='ParentMenuUUID', verbose_name='上级菜单UUID')
    MenuName = models.CharField(max_length=128, db_column='MenuName', verbose_name='菜单名称')
    MenuTitle = models.CharField(max_length=128, db_column='MenuTitle', verbose_name='菜单标题')
    MenuIcon = models.CharField(max_length=128, default='', db_column='MenuIcon', verbose_name='菜单图标')
    MenuLink = models.CharField(max_length=256, default='', db_column='MenuLink', verbose_name='菜单链接')
    MenuSort = models.SmallIntegerField(default=0, db_column='MenuSort', verbose_name='排序')
    PermissionLevel = models.FloatField(default=1.0, db_column='PermissionLevel', verbose_name='权限级别')
    PermissionAttr = models.CharField(max_length=256, default='', db_column='PermissionAttr', verbose_name='权限属性')
    MenuStatus = models.SmallIntegerField(default=1, db_column='MenuStatus', verbose_name='菜单状态')
    MenuType = models.SmallIntegerField(default=0, db_column='MenuType', verbose_name='菜单类型')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'Menu'
        verbose_name = '菜单'
        verbose_name_plural = '菜单'

    def __str__(self):
        return self.MenuTitle or self.MenuName


# === 工单表 ===

class WorkOrder(QModel):
    """工单表——任务与问题跟踪（映射 SAAS 主库 WorkOrder 表）"""

    OrderUUID = UUIDField(primary_key=True, db_column='OrderUUID', verbose_name='工单UUID')
    Title = models.CharField(max_length=256, db_column='Title', verbose_name='标题')
    Description = models.CharField(max_length=4096, default='', db_column='Description', verbose_name='描述')
    Category = models.CharField(max_length=64, default='other', db_column='Category', verbose_name='分类')
    Priority = models.SmallIntegerField(default=0, db_column='Priority', verbose_name='优先级')
    Status = models.SmallIntegerField(default=0, db_column='Status', verbose_name='状态')
    CreatorUUID = UUIDField(db_column='CreatorUUID', db_index=True, verbose_name='创建人UUID')
    AssigneeUUID = UUIDField(null=True, blank=True, db_column='AssigneeUUID', verbose_name='指派人UUID')
    Resolution = models.CharField(max_length=4096, default='', db_column='Resolution', verbose_name='解决方案')
    CreateTime = models.DateTimeField(default=_UtcNow, db_column='CreateTime', verbose_name='创建时间')
    UpdateTime = models.DateTimeField(null=True, blank=True, db_column='UpdateTime', verbose_name='更新时间')
    CloseTime = models.DateTimeField(null=True, blank=True, db_column='CloseTime', verbose_name='关闭时间')

    class Meta:
        managed = False
        app_label = 'gvsdsdk'
        db_table = 'WorkOrder'
        ordering = ['-CreateTime']
        verbose_name = '工单'
        verbose_name_plural = '工单'

    def __str__(self):
        return self.Title
