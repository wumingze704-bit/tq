"""§8.7 数据质量测试：占位行过滤、无成交日标记、OHLC 检查（依据实测）。"""

import numpy as np
import pandas as pd
import pytest

from cnfut.data.quality import (DataQualityError, check_daily, clean_daily,
                               forward_fill_close)


def ns(text):
    """把北京时间的字面量转成 TqSdk 的 datetime 纳秒值（北京时区按 UTC 字面量编码）。"""
    return int(pd.Timestamp(text).value)


def raw_frame(rows):
    return pd.DataFrame(rows)


def base_rows():
    return [
        {"id": 0, "datetime": 0, "open": np.nan, "high": np.nan, "low": np.nan,
         "close": np.nan, "volume": 0.0, "open_oi": np.nan, "close_oi": np.nan},
        {"id": 1, "datetime": ns("2026-11-18 00:00:00"), "open": 100.0, "high": 102.0,
         "low": 99.0, "close": 101.0, "volume": 1000.0, "open_oi": 5000.0, "close_oi": 5100.0},
        {"id": 2, "datetime": ns("2026-11-19 00:00:00"), "open": 101.0, "high": 103.0,
         "low": 100.0, "close": 102.0, "volume": 800.0, "open_oi": 5100.0, "close_oi": 5200.0},
        # 无成交日：行存在、volume=0、close=NaN、close_oi 有值（实测口径）
        {"id": 3, "datetime": ns("2026-11-20 00:00:00"), "open": np.nan, "high": np.nan,
         "low": np.nan, "close": np.nan, "volume": 0.0, "open_oi": 5200.0, "close_oi": 5200.0},
    ]


def test_placeholder_row_is_dropped():
    frame = clean_daily(raw_frame(base_rows()))
    assert len(frame) == 3
    assert (frame["datetime"] > 0).all()
    assert frame["trade_date"].iloc[0] == pd.Timestamp("2026-11-18")


def test_no_volume_row_kept_but_prices_blank():
    frame = clean_daily(raw_frame(base_rows()))
    last = frame.iloc[-1]
    assert last["trade_date"] == pd.Timestamp("2026-11-20")
    assert bool(last["has_trade"]) is False
    assert np.isnan(last["close"])
    assert last["close_oi"] == 5200.0          # 持仓量仍然可用


def test_check_daily_accepts_clean_frame():
    frame = clean_daily(raw_frame(base_rows()))
    check_daily(frame)                          # 不抛异常


def test_check_daily_rejects_inconsistent_ohlc():
    rows = base_rows()
    rows[2]["high"] = 98.0                      # high < close
    with pytest.raises(DataQualityError):
        check_daily(clean_daily(raw_frame(rows)))


def test_forward_fill_only_for_returns():
    frame = forward_fill_close(clean_daily(raw_frame(base_rows())))
    assert frame["close_filled"].iloc[-1] == 102.0   # 用前一个有成交日的收盘
    assert np.isnan(frame["close"].iloc[-1])          # 原始 close 不被改写
