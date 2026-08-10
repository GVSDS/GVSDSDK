"""模块：服务请求验证中间件与权限装饰器
用于验证来自其他微服务的请求，提取服务身份信息。
在 Django 视图中使用 @require_service_scope 装饰器保护端点。"""

import functools
import logging
from django.http import JsonResponse
from rest_framework.response import Response
from gvsdsdk.msync.auth import ValidateAccessToken

logger = logging.getLogger(__name__)


class ServiceAuthMiddleware:
    """服务认证中间件——从请求中提取并验证服务令牌

    将验证结果附加到 request 对象：
      - request.service_name: 发起请求的服务名称
      - request.service_scope: 服务的权限范围列表
      - request.service_token: 原始令牌字符串
    """

    SERVICE_AUTH_HEADER = 'Authorization'
    SERVICE_AUTH_PREFIX = 'Service'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.service_name = None
        request.service_scope = []
        request.service_token = None

        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith(f'{self.SERVICE_AUTH_PREFIX} '):
            token = auth_header[len(self.SERVICE_AUTH_PREFIX) + 1:]
            payload = ValidateAccessToken(token)
            if payload:
                request.service_name = payload.get('sub')
                request.service_scope = payload.get('scope', [])
                request.service_token = token

        response = self.get_response(request)
        return response


def require_service_scope(*required_scopes):
    """视图装饰器——要求请求来自具有指定权限范围的已认证服务

    用法:
        @require_service_scope('ogm:read')
        def my_view(request):
            # request.service_name 已设置
            ...
    """

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.service_name:
                return JsonResponse(
                    {'code': 401, 'msg': '需要服务认证', 'data': None},
                    status=401,
                )

            if '*' not in request.service_scope:
                for scope in required_scopes:
                    if scope not in request.service_scope:
                        return JsonResponse(
                            {'code': 403, 'msg': f'缺少权限: {scope}', 'data': None},
                            status=403,
                        )

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_service_scope_drq(*required_scopes):
    """DRF 视图装饰器——返回 DRF Response 而非 Django JsonResponse"""

    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(self, request, *args, **kwargs):
            if not request.service_name:
                return Response(
                    {'code': 401, 'msg': '需要服务认证', 'data': None},
                    status=401,
                )

            if '*' not in request.service_scope:
                for scope in required_scopes:
                    if scope not in request.service_scope:
                        return Response(
                            {'code': 403, 'msg': f'缺少权限: {scope}', 'data': None},
                            status=403,
                        )

            return view_func(self, request, *args, **kwargs)

        return wrapper

    return decorator


class ServicePermission:
    """DRF 权限类——用于 ViewSet 的 permission_classes"""

    def __init__(self, *required_scopes):
        self.required_scopes = required_scopes

    def has_permission(self, request, view):
        if not request.service_name:
            return False
        if '*' in request.service_scope:
            return True
        return all(s in request.service_scope for s in self.required_scopes)


class ServiceTokenAuthentication:
    """DRF 认证类——从请求头中提取并验证服务令牌

    配合 ServiceAuthMiddleware 使用，中间件已将验证结果附加到 request 对象。
    此类使 DRF 的 request.user 和 request.auth 可用于权限检查。
    """

    def authenticate(self, request):
        service_name = getattr(request, 'service_name', None)
        if not service_name:
            return None
        # 返回 (user, auth) 元组，使 DRF 认为请求已认证
        # service_name 作为 user 标识，service_token 作为 auth
        return (ServicePrincipal(request), request.service_token)

    def authenticate_header(self, request):
        return 'Service'


class ServicePrincipal:
    """服务主体——作为 DRF 认证后的 request.user"""

    def __init__(self, request):
        self.service_name = request.service_name
        self.service_scope = request.service_scope
        self.service_token = request.service_token

    @property
    def is_authenticated(self):
        return True

    def __str__(self):
        return f'Service:{self.service_name}'
