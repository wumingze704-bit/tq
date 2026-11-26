"""按交易所代码规则生成历史合约（R2 数据回填的最小纵切）。

依据 `docs/research/SETUP_DONE.md` §2：`query_quotes(expired=True)` 返回的已下市合约列表被
截断（`ledger.blockers` B1 / 评审 R-25），SHFE/DCE 的老合约**不能靠合约列表发现**，只能按
"品种 + 交割年月"规则生成代码后直接订阅日线——`SETUP_DONE.md` §2 的逐合约实测已证明
`SHFE.rb1801`、`DCE.m1801`、`CZCE.TA601` 都能直接取到日线。

代码规则（实测口径）：

* SHFE / DCE / INE / GFEX：品种 + 4 位 ``YYMM``（2018-01 → ``1801``）
* CZCE：品种 + 3 位 ``YMM``（年份取末位，2018-01 → ``801``）

本模块只做纯计算（代码、交割月份、自然人最后持仓日、候选清单），不联网、不读数据文件，
便于单测；取数在 `scripts/backfill_history.py`。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence

import pandas as pd

FOUR_DIGIT_EXCHANGES = ("SHFE", "DCE", "INE", "GFEX")
THREE_DIGIT_EXCHANGES = ("CZCE",)
_DIGITS = re.compile(r"\d+$")


def contract_code(exchange: str, product: str, year: int, month: int) -> str:
    """``("SHFE", "rb", 2018, 1)`` → ``SHFE.rb1801``；``("CZCE", "TA", 2018, 1)`` → ``CZCE.TA601``。"""
    name = exchange.upper()
    if not 1 <= int(month) <= 12:
        raise ValueError(f"交割月份必须在 1~12: {month}")
    if name in FOUR_DIGIT_EXCHANGES:
        return f"{name}.{product}{int(year) % 100:02d}{int(month):02d}"
    if name in THREE_DIGIT_EXCHANGES:
        return f"{name}.{product}{int(year) % 10}{int(month):02d}"
    raise ValueError(f"未知交易所: {exchange}")


def symbol_digits(symbol: str) -> str:
    """``CZCE.TA601`` → ``601``；``SHFE.rb1801`` → ``1801``（无数字返回空串）。"""
    code = symbol.split(".", 1)[1] if "." in symbol else symbol
    match = _DIGITS.search(code)
    return match.group(0) if match else ""


def delivery_months(symbols: Iterable[str], product: str) -> List[int]:
    """从已有合约代码推断该品种**实际挂牌**的交割月份。

    rb/eg/jd/i/p 挂牌 1~12 个月（实测 52 个合约代码），c/m/y 只挂单月；按实测集合取，
    不对任何品种假设"每月都有"。
    """
    months = set()
    for symbol in symbols:
        code = symbol.split(".", 1)[1] if "." in symbol else symbol
        if not code.startswith(product):
            continue
        digits = symbol_digits(symbol)
        if len(digits) < 3:
            continue
        month = int(digits[-2:])
        if 1 <= month <= 12:
            months.add(month)
    return sorted(months)


def last_hold_date(year: int, month: int, sessions: Sequence) -> pd.Timestamp:
    """自然人最后持仓日 = 交割月前一月的最后一个交易日（同 `pull_data.last_hold_dates`）。"""
    days = pd.DatetimeIndex(sessions).sort_values()
    first_of_delivery = pd.Timestamp(year=int(year), month=int(month), day=1)
    candidates = days[days < first_of_delivery]
    if not len(candidates):
        raise ValueError(f"交易日历不覆盖 {first_of_delivery.date()} 之前，无法定 limit_date")
    return pd.Timestamp(candidates[-1])


def plan_contracts(exchange: str, product: str, months: Iterable[int], years: Iterable[int],
                   sessions: Sequence, start, end, extra_days: int = 120) -> List[Dict]:
    """生成 ``[start, end + extra_days]`` 区间内**到期**的候选合约清单。

    保留条件：``limit_date`` 落在 ``[start, end + extra_days]``。
    末尾多留 ``extra_days`` 是为了让连续合约在 ``end`` 附近仍有更远月可选（否则执行合约会在
    数据右端被迫用近月）。
    """
    start = pd.Timestamp(start)
    horizon = pd.Timestamp(end) + pd.Timedelta(days=int(extra_days))
    rows: List[Dict] = []
    for year in sorted({int(y) for y in years}):
        for month in sorted({int(m) for m in months}):
            try:
                limit = last_hold_date(year, month, sessions)
            except ValueError:
                continue          # 交易日历不覆盖 → 该交割月无法定 limit_date，直接跳过
            if limit < start or limit > horizon:
                continue
            rows.append({
                "symbol": contract_code(exchange, product, year, month),
                "product": product,
                "delivery_year": year,
                "delivery_month": month,
                "limit_date": limit,
            })
    return rows
