"""gvsdsdk CLI 入口

用法：
    python -m gvsdsdk                 # 默认：显示版本信息
    python -m gvsdsdk --version       # 仅版本字符串
    python -m gvsdsdk --info          # 详细版本信息
    python -m gvsdsdk --check 1.0.0   # 检查最低版本
    python -m gvsdsdk --symbols       # 列出所有公开符号
    python -m gvsdsdk --ping          # 测试 SDK 可用性
"""

import sys
import argparse

# 当作为 __main__ 运行时，需显式 import 包（runpy 不会自动注入）
import gvsdsdk
from gvsdsdk import __version__, get_version_info, check_min_version, _PKG_DIR, _LAZY_SYMBOLS


def main():
    parser = argparse.ArgumentParser(
        prog='gvsdsdk',
        description='GVSDS SDK — 分布式服务通信 SDK',
    )
    parser.add_argument('--version', action='store_true', help='显示版本号')
    parser.add_argument('--info', action='store_true', help='显示详细信息')
    parser.add_argument('--check', metavar='VERSION', help='检查最低版本要求')
    parser.add_argument('--symbols', action='store_true', help='列出所有公开符号')
    parser.add_argument('--ping', action='store_true', help='测试 SDK 可用性')
    args = parser.parse_args()

    if args.version:
        print(__version__)
        return 0

    if args.check:
        ok, msg = check_min_version(args.check)
        print(msg)
        return 0 if ok else 1

    if args.symbols:
        from gvsdsdk import _LAZY_SYMBOLS
        print(f'GVSDS SDK {__version__} — {len(_LAZY_SYMBOLS)} lazy symbols:')
        by_mod = {}
        for sym, (mod, _) in _LAZY_SYMBOLS.items():
            by_mod.setdefault(mod, []).append(sym)
        for mod, syms in sorted(by_mod.items()):
            print(f'  [{mod}]')
            for s in sorted(syms):
                print(f'    - {s}')
        return 0

    if args.ping:
        from gvsdsdk import get_version_info, _LAZY_SYMBOLS
        info = get_version_info()
        print(f'Pong — SDK {info["Version"]} ready at {info["Location"]}')
        print(f'  {len(_LAZY_SYMBOLS)} symbols available (lazy)')
        return 0

    if args.info:
        from gvsdsdk import get_version_info
        info = get_version_info()
        print('GVSDS SDK Version Information:')
        for k, v in info.items():
            print(f'  {k:12s} : {v}')
        return 0

    # 默认行为：简要版本信息
    print(f'GVSDS SDK {__version__}')
    print(f'  Location: {_PKG_DIR}')
    print(f'  Use --info for details, --help for all commands')
    return 0


if __name__ == '__main__':
    sys.exit(main())