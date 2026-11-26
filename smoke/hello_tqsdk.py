"""最小连通性自检：凭据只从环境变量读取，源码里不保存任何账号密码。

一次性设置（写入用户环境变量，之后新开的终端才生效）::

    setx TQ_AUTH_USER "你的快期账号"
    setx TQ_AUTH_PASSWORD "你的密码"

运行::

    python smoke/hello_tqsdk.py
"""

import os

from tqsdk import TqApi, TqAuth


def read_auth() -> TqAuth:
    user = os.environ.get("TQ_AUTH_USER", "").strip()
    password = os.environ.get("TQ_AUTH_PASSWORD", "").strip()
    if not (user and password):
        raise SystemExit(
            "缺少环境变量 TQ_AUTH_USER / TQ_AUTH_PASSWORD；"
            "请用 setx 设置后重开终端，不要把密码写进源码。")
    return TqAuth(user, password)


def main() -> None:
    api = TqApi(auth=read_auth())
    try:
        # 主连合约恒存在，避免因合约代码过期而一直等行情
        quote = api.get_quote("KQ.m@SHFE.rb")
        print("主连:", quote.underlying_symbol or "KQ.m@SHFE.rb")
        print("最新价:", quote.last_price)
    finally:
        api.close()


if __name__ == "__main__":
    main()
