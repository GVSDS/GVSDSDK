"""自定义 JWT 认证 — 向后兼容旧 token 的 user_id claim"""

import hmac

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed
from rest_framework_simplejwt.settings import api_settings
from django.utils.translation import gettext_lazy as _


class CompatibleJWTAuthentication(JWTAuthentication):
    """
    兼容旧 token 的 JWT 认证类。

    当前配置（settings.SIMPLE_JWT）：
      - USER_ID_CLAIM = 'UserUUID'
      - USER_ID_FIELD = 'UserUUID'

    旧 token（默认 SimpleJWT 配置时生成）payload 中使用 'user_id' claim，
    值为 user.pk（即 UserUUID 的字符串形式）。

    此类先尝试当前配置的 USER_ID_CLAIM（'UserUUID'），找不到时回退到旧
    'user_id' claim，两者均通过 UserUUID 字段查库。
    """

    def get_user(self, validated_token):
        # 1. 先尝试当前配置的 USER_ID_CLAIM
        user_id = validated_token.get(api_settings.USER_ID_CLAIM)
        lookup_field = api_settings.USER_ID_FIELD

        if user_id is None:
            # 2. 回退：尝试旧 token 中的 'user_id' claim
            user_id = validated_token.get('user_id')
            if user_id is None:
                raise InvalidToken(
                    {'detail': _('令牌未包含用户标识符'), 'code': 'token_not_valid'}
                )
            # 旧 token 的 user_id 来自 user.pk（即 UserUUID 字符串），
            # 需要用 UserUUID 字段查库
            lookup_field = 'UserUUID'

        try:
            user = self.user_model.objects.get(**{lookup_field: user_id})
        except self.user_model.DoesNotExist:
            raise AuthenticationFailed(_('用户不存在'), code='user_not_found')
        except (ValueError, TypeError):
            raise InvalidToken(
                {'detail': _('令牌中的用户标识符格式无效'), 'code': 'token_not_valid'}
            )

        if not user.is_active:
            raise AuthenticationFailed(_('用户已被禁用'), code='user_inactive')

        if api_settings.CHECK_REVOKE_TOKEN:
            # 注意：get_md5_hash_password 使用 MD5 哈希，这是 simplejwt 库的默认行为。
            # 此处仅用于令牌吊销校验（判断密码是否变更），不用于密码存储。
            # 如需更安全的哈希算法，需升级 simplejwt 版本或自定义校验逻辑。
            from rest_framework_simplejwt.utils import get_md5_hash_password
            if not hmac.compare_digest(
                str(validated_token.get(api_settings.REVOKE_TOKEN_CLAIM, '')),
                str(get_md5_hash_password(user.UserPassword))
            ):
                raise AuthenticationFailed(
                    _('用户密码已更改'), code='password_changed'
                )

        return user
