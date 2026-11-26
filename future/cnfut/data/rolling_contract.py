"""§8.4 执行合约、移仓与连续收益序列。

输入 ``bars`` 必须包含（一行 = 一个合约一个交易日）::

    symbol, trade_date, open, high, low, close, volume, close_oi, limit_date

``limit_date`` 是自然人最后持仓日（交割月前一月最后交易日），由数据层按交易所规则给出；
这里不解析合约代码推断交割月，避免郑商所 3 位年份（TA601/TA401）的十年循环歧义。

规则（只用 ≤ t 的数据）：
1. 合格合约：``session_pos(limit_date) - session_pos(t) > exit_buffer_days``；
2. 候选：合格合约中 t 日收盘持仓量最大者，并列取交割月更远者；
3. 切换：候选中交割月更远，且（候选持仓量 > 当前持仓量 × oi_switch_ratio 连续
   confirm_days 个交易日，或当前已不合格）→ t+1 起生效；
4. 只向远月切换，不切回近月；
5. 连续收益用同一合约的前收盘，换月价差不进入收益。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from ..config import RollParams

_DIGITS = re.compile(r"\d+$")
REQUIRED_COLUMNS = ("symbol", "trade_date", "open", "high", "low", "close",
                    "volume", "close_oi", "limit_date")


def product_of(symbol: str) -> str:
    """``DCE.m2401`` -> ``m``；``CZCE.TA601`` -> ``TA``。"""
    code = symbol.split(".", 1)[1] if "." in symbol else symbol
    return _DIGITS.sub("", code)


def delivery_key(limit_date) -> int:
    """自然人最后持仓日在交割月前一月 → 交割月 = 次月，返回可比较的月度序号。"""
    ts = pd.Timestamp(limit_date)
    return ts.year * 12 + ts.month + 1


def _session_position(limit_date: pd.Timestamp, sessions: np.ndarray,
                      position: Dict[pd.Timestamp, int]) -> int:
    """limit_date 在已有交易日序列中的位置；数据末端之后视为“很远”。"""
    if limit_date in position:
        return position[limit_date]
    index = int(np.searchsorted(sessions, np.datetime64(limit_date)))
    if index >= len(sessions):
        return len(sessions) + 10000
    return index - 1


def _best(candidates: Iterable[str], oi_row: pd.Series, dkey: Dict[str, int]) -> Optional[str]:
    """持仓量最大者；并列取交割月更远者；再并列取代码更小者（保证确定性）。"""
    ranked = sorted(candidates, key=lambda s: (-float(oi_row.get(s, np.nan)),
                                               -dkey.get(s, -1), s))
    return ranked[0] if ranked else None


def _next_contract(current: str, columns, oi_row: pd.Series, dkey: Dict[str, int],
                   min_oi: float) -> Optional[str]:
    """§10.3 次远合约：交割月晚于执行合约、持仓量 ≥ min_oi 中交割月最近者。"""
    candidates = [sym for sym in columns
                  if sym != current
                  and np.isfinite(oi_row.get(sym, np.nan))
                  and float(oi_row.get(sym, 0.0)) >= min_oi
                  and dkey.get(sym, -1) > dkey.get(current, -1)]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (dkey[s], -float(oi_row.get(s, 0.0))))


def _exec_series(sub: pd.DataFrame, sessions: np.ndarray,
                 position: Dict[pd.Timestamp, int], params: RollParams,
                 min_oi: float = 5000.0):
    """单个品种的执行合约序列（t 日收盘后判定，t+1 生效）。"""
    oi = sub.pivot_table(index="trade_date", columns="symbol",
                         values="close_oi", aggfunc="last")
    limits = sub.groupby("symbol")["limit_date"].first().map(pd.Timestamp)
    dkey = {sym: delivery_key(day) for sym, day in limits.items()}
    buffer_days = params.exit_buffer_days + params.broker_extra_days
    limit_pos = {sym: _session_position(day, sessions, position) for sym, day in limits.items()}

    current: Optional[str] = None
    pending: Optional[str] = None
    pending_days = 0
    exec_today: Dict[pd.Timestamp, Optional[str]] = {}
    roll_flags: Dict[pd.Timestamp, bool] = {}
    next_today: Dict[pd.Timestamp, Optional[str]] = {}

    for step, day in enumerate(oi.index):
        row = oi.loc[day]
        eligible = [sym for sym in oi.columns
                    if np.isfinite(row.get(sym, np.nan)) and limit_pos[sym] - step > buffer_days]
        if current is None and eligible:
            current = _best(eligible, row, dkey)
        exec_today[day] = current
        roll_flags[day] = False
        if current is None:
            next_today[day] = None
            continue
        next_today[day] = _next_contract(current, oi.columns, row, dkey, min_oi)

        candidate = _best(eligible, row, dkey)
        if candidate is None or candidate == current:
            pending, pending_days = None, 0
            continue
        if dkey[candidate] <= dkey[current]:
            pending, pending_days = None, 0
            continue

        forced = current not in eligible
        surge = (float(row.get(candidate, np.nan))
                 > float(row.get(current, np.nan)) * params.oi_switch_ratio)
        if forced or surge:
            pending_days = pending_days + 1 if pending == candidate else 1
            pending = candidate
            if forced or pending_days >= params.confirm_days:
                current = candidate
                pending, pending_days = None, 0
                roll_flags[day] = True
        else:
            pending, pending_days = None, 0

    return pd.Series(exec_today), pd.Series(roll_flags), pd.Series(next_today)


def _pick(frame: pd.DataFrame, symbols: pd.Series) -> pd.Series:
    """按 (日期, 合约) 从宽表取值；缺失返回 NaN。"""
    values: List[float] = []
    for day, symbol in symbols.items():
        if symbol is None or day not in frame.index or symbol not in frame.columns:
            values.append(np.nan)
        else:
            values.append(frame.at[day, symbol])
    return pd.Series(values, index=symbols.index, dtype="float64")


def build_continuous(bars: pd.DataFrame, params: RollParams,
                     atr_window: int = 20, carry_min_oi: float = 5000.0) -> pd.DataFrame:
    """逐品种生成执行合约、次远合约与连续收益序列（长表）。"""
    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars 缺少列: {missing}")

    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame["limit_date"] = pd.to_datetime(frame["limit_date"])
    frame["product"] = frame["symbol"].map(product_of)

    sessions = np.sort(frame["trade_date"].unique())
    position = {pd.Timestamp(day): idx for idx, day in enumerate(sessions)}

    results = []
    for product, sub in frame.groupby("product", sort=True):
        exec_series, roll_flags, next_series = _exec_series(
            sub, sessions, position, params, carry_min_oi)

        wide = {name: sub.pivot_table(index="trade_date", columns="symbol",
                                      values=name, aggfunc="last")
                for name in ("open", "high", "low", "close")}
        prev_close = wide["close"].shift(1)
        close = _pick(wide["close"], exec_series)
        open_ = _pick(wide["open"], exec_series)
        prev = _pick(prev_close, exec_series)

        tr = np.maximum(np.maximum(wide["high"] - wide["low"],
                                   (wide["high"] - prev_close).abs()),
                        (wide["low"] - prev_close).abs())
        tr_pct = tr / prev_close.where(prev_close > 0)
        atr_pct = tr_pct.rolling(atr_window, min_periods=atr_window).mean()

        limits_by_symbol = sub.groupby("symbol")["limit_date"].first()
        next_close = _pick(wide["close"], next_series)

        out = pd.DataFrame({
            "product": product,
            "exec_symbol": exec_series,
            "next_symbol": next_series,
            "r_close": close / prev - 1.0,
            "r_open": open_ / prev - 1.0,
            "close": close,
            "prev_close": prev,
            "next_close": next_close,
            "atr_pts": _pick(atr_pct, exec_series) * close,
            "exec_limit_date": exec_series.map(limits_by_symbol),
            "next_limit_date": next_series.map(limits_by_symbol),
            "roll_flag": roll_flags.reindex(exec_series.index).fillna(False).astype(bool),
        })
        out["P"] = (1.0 + out["r_close"].fillna(0.0)).cumprod()
        results.append(out.reset_index().rename(columns={"index": "trade_date"}))

    return pd.concat(results, ignore_index=True)
