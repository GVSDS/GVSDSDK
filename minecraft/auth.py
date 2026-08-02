"""模块：gvsdsdk.minecraft.auth — EasyAuth 插件认证与绑定管理

提供：
    - ``VerifyPassword``：使用 bcrypt 校验游戏账号密码
    - ``EasyAuthManager``：游戏账号查询、密码验证、绑定标记读写
"""

import bcrypt


def VerifyPassword(plain_password: str, bcrypt_hash: str) -> bool:
    """校验明文密码与 bcrypt hash 是否匹配

    Args:
        plain_password: 玩家输入的明文密码
        bcrypt_hash: ``easyauth.data.password`` 中存储的 bcrypt hash 字符串

    Returns:
        True 如果密码匹配；False 如果不匹配或 hash 格式异常
    """
    if not plain_password or not bcrypt_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), bcrypt_hash.encode('utf-8'))
    except (ValueError, TypeError):
        return False


class EasyAuthManager:
    """EasyAuth 认证管理器——游戏账号查询、密码验证、绑定标记读写"""

    @staticmethod
    def GetByUsername(username: str):
        """按游戏名（不区分大小写）查询 EasyAuth 记录

        Args:
            username: 游戏名（原始大小写均可）

        Returns:
            ``EasyAuth`` 实例；不存在则返回 ``None``
        """
        from .models import EasyAuth
        if not username:
            return None
        return EasyAuth.objects.filter(UsernameLower=username.lower()).first()

    @staticmethod
    def VerifyPassword(easyauth, plain_password: str) -> bool:
        """校验 EasyAuth 记录的密码

        Args:
            easyauth: ``EasyAuth`` 实例
            plain_password: 明文密码

        Returns:
            True 如果密码匹配；False 如果不匹配、未设置密码或 hash 异常
        """
        if easyauth is None:
            return False
        data = easyauth.Data or {}
        password_hash = data.get('password') or ''
        return VerifyPassword(plain_password, password_hash)

    @staticmethod
    def IsBound(easyauth) -> bool:
        """判断该游戏账号是否已绑定 GVSDS 用户"""
        return easyauth is not None and easyauth.UserRoleUUID is not None

    @staticmethod
    def MarkBound(easyauth, user_role_uuid) -> int:
        """将 UserRoleUUID 写入 easyauth 作为绑定标记

        使用乐观锁（``UserRoleUUID__isnull=True``）防止并发重复绑定。

        Args:
            easyauth: ``EasyAuth`` 实例
            user_role_uuid: 新创建的 UserRole 关联 UUID（str / bytes / UUIDObj 均可）

        Returns:
            实际更新行数：1 成功，0 表示已被其他请求抢先绑定
        """
        from .models import EasyAuth
        if easyauth is None:
            return 0
        return EasyAuth.objects.filter(
            AuthID=easyauth.AuthID,
            UserRoleUUID__isnull=True,
        ).update(UserRoleUUID=user_role_uuid)

    @staticmethod
    def ClearBound(easyauth) -> int:
        """清空 easyauth 的 UserRoleUUID（用于解绑场景）"""
        from .models import EasyAuth
        if easyauth is None:
            return 0
        return EasyAuth.objects.filter(
            AuthID=easyauth.AuthID,
        ).update(UserRoleUUID=None)

    @staticmethod
    def GetPlayerUUIDByGVSDSUser(gvsds_user_uuid):
        """GVSDS UserUUID → MC 玩家 UUID 字符串（varchar36）

        反查路径：GVSDS UserUUID → 该用户的 UserRoleUUID 集合 →
        easyauth 表中 UserRoleUUID 在该集合内的记录 → PlayerUUID。

        与 MC 绑定流程（``UserBindView``）配套：绑定时会把新建的
        UserRoleUUID 写入 easyauth.UserRoleUUID 列作为绑定标记，
        因此反过来即可定位该 GVSDS 用户绑定的 MC 玩家。

        Args:
            gvsds_user_uuid: GVDS 用户 UUID（bytes/str/UUIDObj 均可）

        Returns:
            MC 玩家 UUID 字符串；未绑定或 easyauth 记录不存在时返回 None
        """
        from .models import EasyAuth
        from gvsdsdk.models import UserRole
        from gvsdsdk.model_utils import UUIDObj
        if not gvsds_user_uuid:
            return None
        raw_bytes = UUIDObj(gvsds_user_uuid).bytes
        # 该 GVSDS 用户名下的所有 UserRoleUUID（跨公司/角色）
        user_role_uuids = list(
            UserRole.objects.filter(UserUUID=raw_bytes).values_list('UserRoleUUID', flat=True)
        )
        if not user_role_uuids:
            return None
        # easyauth 表反查绑定标记，定位 MC 玩家
        ea = EasyAuth.objects.filter(UserRoleUUID__in=user_role_uuids).first()
        return ea.PlayerUUID if ea else None

    @staticmethod
    def GetByPlayerUUID(player_uuid):
        """按 MC 玩家 UUID 字符串查询 EasyAuth 记录

        Args:
            player_uuid: MC 玩家 UUID 字符串（easyauth.uuid 列，varchar36）

        Returns:
            ``EasyAuth`` 实例；不存在或入参为空则返回 ``None``
        """
        from .models import EasyAuth
        if not player_uuid:
            return None
        return EasyAuth.objects.filter(PlayerUUID=player_uuid).first()
