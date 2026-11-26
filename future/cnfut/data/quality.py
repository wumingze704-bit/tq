"""§8.7 数据质量：实测口径（见 docs/research/SETUP_DONE.md §3）。

两条来自实测、必须固化的规则：

1. 已下市合约的日线序列左端存在 `datetime == 0` 的占位行（转换后是 1970-01-01 08:00），
   必须过滤，否则连续价格指数 `P` 会从 1970 年开始累计（评审 R-23）。
2. 无成交交易日**返回整行**：`close = NaN`、`volume = 0`、`close_oi` 有值。
   这类行保留（`close_oi` 仍要参与持仓量统计），但价格字段置空、标记 `has_trade=False`。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from tqsdk.tafunc import time_to_datetime

REQUIRED = ("datetime", "open", "high", "low", "close", "volume", "close_oi")


class DataQualityError(ValueError):
    """日线数据不满足 §8.7 的质量约定。"""


def clean_daily(raw: pd.DataFrame) -> pd.DataFrame:
    """过滤占位行、换算交易日、标记无成交日。输入为 `get_kline_serial` 的原始返回。"""
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        raise DataQualityError(f"日线缺少列: {missing}")

    frame = raw.loc[raw["datetime"] > 0].copy()          # [R-23] 丢弃 1970 占位行
    frame["trade_date"] = pd.to_datetime(
        [time_to_datetime(int(value)).date() for value in frame["datetime"]])
    frame["has_trade"] = frame["volume"].fillna(0.0) > 0
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").where(frame["has_trade"])
    return frame.reset_index(drop=True)


def check_daily(frame: pd.DataFrame) -> None:
    """§8.7 第 2 条的价格与成交量检查；失败抛 DataQualityError 并带上日期。"""
    traded = frame[frame["has_trade"]]
    if traded.empty:
        return
    body_high = traded[["open", "close", "high"]].max(axis=1)
    body_low = traded[["open", "close", "low"]].min(axis=1)
    broken = traded[(traded["low"] > body_low) | (traded["high"] < body_high)
                    | (traded["low"] > traded["high"])]
    if not broken.empty:
        first = broken.iloc[0]
        raise DataQualityError(f"{first.get('symbol', '')} {first['trade_date']} 的 OHLC 不自洽")
    if (traded[["open", "high", "low", "close"]] <= 0).to_numpy().any():
        raise DataQualityError("存在非正价格")
    if (traded["volume"] < 0).to_numpy().any() or (frame["close_oi"].fillna(0) < 0).to_numpy().any():
        raise DataQualityError("成交量或持仓量为负")


def forward_fill_close(frame: pd.DataFrame) -> pd.DataFrame:
    """无成交日不参与收益计算：用最近一个有效收盘填补，仅用于拼接连续收益。"""
    result = frame.copy()
    result["close_filled"] = result["close"].ffill()
    if result["close_filled"].isna().any():
        raise DataQualityError("序列起点即无有效收盘，无法做连续收益")
    return result
