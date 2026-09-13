import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    # 始终记录异常堆栈（走 logging，日志落盘可审计，替代 print）
    view = context.get('view', None) if context else None
    view_name = view.__class__.__name__ if view else 'Unknown'
    logger.error('[EXCEPTION] %s: %s', view_name, exc, exc_info=True)

    response = exception_handler(exc, context)

    if response is not None:
        data = {
            'code': response.status_code,
            'msg': str(exc) if settings.DEBUG else '请求处理失败',
            'data': None,
        }
        if isinstance(response.data, dict):
            if 'detail' in response.data:
                # 401/403 的 detail（如 "身份认证信息未提供"/"Token 已过期"）对前端是
                # 必要的业务信息，不属于敏感堆栈，即使在生产环境也直接暴露。
                if response.status_code in (status.HTTP_401_UNAUTHORIZED,
                                            status.HTTP_403_FORBIDDEN):
                    data['msg'] = str(response.data['detail'])
                else:
                    data['msg'] = str(response.data['detail']) if settings.DEBUG else '请求处理失败'
            elif 'msg' in response.data:
                data['msg'] = response.data['msg']
            else:
                if settings.DEBUG:
                    errors = []
                    for field, messages in response.data.items():
                        if isinstance(messages, list):
                            errors.extend([f"{field}: {m}" for m in messages])
                        else:
                            errors.append(f"{field}: {messages}")
                    data['msg'] = '; '.join(errors) if errors else str(exc)
                else:
                    data['msg'] = '请求参数错误'
        response.data = data
    else:
        # 未被 DRF 捕获的异常（如 Django FieldError、AttributeError 等）
        response = Response(
            {'code': 500, 'msg': str(exc) if settings.DEBUG else '服务器内部错误', 'data': None},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return response
