"""模块：IP 访问控制中间件

支持三种模式：production（仅白名单）、whitelist（白名单优先）、blacklist（黑名单优先）。
支持 CIDR 网段、IPv4/IPv6、X-Real-IP/X-Forwarded-For 穿透、免过滤路径。

配置（app.ini [ip_filter] 段或环境变量 IP_FILTER_*）：
    MODE           = production | whitelist | blacklist
    WHITELIST      = 127.0.0.1,10.0.0.0/8
    BLACKLIST      = 1.2.3.4,5.6.7.0/24
    EXEMPT_PATHS   = /health,/API/Auth
    TRUSTED_PROXIES = 127.0.0.1   # 信任的代理 IP，用于正确解析 X-Forwarded-For
"""
import ipaddress
import logging

from gvsdsdk.config import Get as _app_get, GetList as _app_getlist

logger = logging.getLogger(__name__)


def _ParseIPList(raw):
    """解析 IP/CIDR 列表为 networks。"""
    nets = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        try:
            if '/' in item:
                nets.append(ipaddress.ip_network(item, strict=False))
            else:
                nets.append(ipaddress.ip_network(item + '/32', strict=False)
                            if ':' not in item else
                            ipaddress.ip_network(item + '/128', strict=False))
        except ValueError:
            logger.warning('[IPFilter] 无效的 IP/CIDR: %s', item)
    return nets


def _GetClientIP(request, trusted_proxies):
    """获取真实客户端 IP，穿透可信代理。"""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    xri = request.META.get('HTTP_X_REAL_IP', '')
    remote = request.META.get('REMOTE_ADDR', '')

    if xff:
        chain = [ip.strip() for ip in xff.split(',') if ip.strip()]
        if chain:
            if trusted_proxies:
                # 从右往左找到第一个不在可信代理链中的 IP
                for ip in reversed(chain):
                    try:
                        addr = ipaddress.ip_address(ip)
                        if not any(addr in net for net in trusted_proxies):
                            return ip
                    except ValueError:
                        continue
            return chain[0]
    if xri:
        return xri.split(',')[0].strip()
    return remote


class IPFilterMiddleware:
    """IP 白黑名单中间件。"""

    def __init__(self, get_response):
        self.get_response = get_response
        # 默认 disabled（安全默认值权衡：避免漏配即拒绝服务）。
        # 生产环境应显式配置 MODE=production/whitelist 并挂载中间件（见 app.ini [middleware]）。
        self.mode = _app_get('ip_filter', 'MODE', fallback='disabled').lower()
        self.whitelist = _ParseIPList(_app_getlist('ip_filter', 'WHITELIST', fallback=[]))
        self.blacklist = _ParseIPList(_app_getlist('ip_filter', 'BLACKLIST', fallback=[]))
        self.exempt_paths = tuple(p.strip() for p in _app_getlist('ip_filter', 'EXEMPT_PATHS', fallback=['/health', '/API/Auth']))
        self.trusted_proxies = _ParseIPList(_app_getlist('ip_filter', 'TRUSTED_PROXIES', fallback=['127.0.0.1/32']))

    def __call__(self, request):
        if self.mode == 'disabled':
            return self.get_response(request)

        path = request.path
        if path.startswith(self.exempt_paths):
            return self.get_response(request)

        client_ip = _GetClientIP(request, self.trusted_proxies)
        request.client_ip = client_ip

        try:
            addr = ipaddress.ip_address(client_ip)
        except ValueError:
            logger.warning('[IPFilter] 无法解析客户端 IP: %s', client_ip)
            return self._deny(client_ip, path, 'invalid ip')

        if self.mode == 'blacklist':
            if any(addr in net for net in self.blacklist):
                return self._deny(client_ip, path, 'blacklisted')
            return self.get_response(request)

        if self.mode == 'whitelist':
            if self.whitelist and not any(addr in net for net in self.whitelist):
                return self._deny(client_ip, path, 'not in whitelist')
            return self.get_response(request)

        if self.mode == 'production':
            # production 模式：仅白名单 + 黑名单豁免
            if any(addr in net for net in self.blacklist):
                return self._deny(client_ip, path, 'blacklisted')
            if self.whitelist and not any(addr in net for net in self.whitelist):
                return self._deny(client_ip, path, 'not in whitelist')
            return self.get_response(request)

        return self.get_response(request)

    def _deny(self, ip, path, reason):
        logger.warning('[IPFilter] 拒绝访问: ip=%s path=%s reason=%s', ip, path, reason)
        from django.http import JsonResponse
        return JsonResponse({'code': 403, 'msg': 'IP 访问被拒绝'}, status=403)
