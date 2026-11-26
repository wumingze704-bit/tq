"""§8.7 数据体检脚本的手算测试（对应 SETUP_DONE §3 的四条实测口径）。

用真实 parquet 做断言太脆，这里用最小的合成 bars 表逐条验证 `audit`：
唯一性、占位行、有成交行价格、无成交行价格置空。
"""

import numpy as np
import pandas as pd
import pytest

from scripts.data_health import audit, format_report, load_bars

DATES = pd.to_datetime(["2023-10-09", "2023-10-10", "2023-10-11"])


def make_bars(rows):
    """rows: [(symbol, trade_date, close, volume)] -> bars DataFrame。"""
    return pd.DataFrame([
        {"symbol": symbol, "trade_date": day, "open": close, "high": close,
         "low": close, "close": close, "volume": volume, "close_oi": 1000.0}
        for symbol, day, close, volume in rows])


def clean_bars():
    return make_bars([
        ("DCE.m2401", DATES[0], 3000.0, 1000.0),
        ("DCE.m2401", DATES[1], 3010.0, 1200.0),
        # 无成交日：行保留、volume=0、close=NaN（实测口径）
        ("DCE.m2401", DATES[2], np.nan, 0.0),
        ("CZCE.TA401", DATES[0], 5000.0, 800.0),
        ("CZCE.TA401", DATES[1], 5010.0, 900.0),
        ("CZCE.TA401", DATES[2], 5020.0, 700.0),
    ])


def test_clean_bars_pass_all_checks():
    result = audit(clean_bars())

    assert result["ok"] is True
    assert [check["id"] for check in result["checks"]] == ["C1", "C2", "C3", "C4"]
    assert result["stats"]["products"] == 2
    assert result["stats"]["symbols"] == 2
    assert result["stats"]["rows"] == 6
    assert result["stats"]["start"] == "2023-10-09"
    assert result["stats"]["end"] == "2023-10-11"
    assert result["stats"]["idle_rows"] == 1


def test_earliest_date_per_product_is_reported():
    """按品种最早交易日用于判断哪些品种还需回填（R2 的前置依据）。"""
    bars = make_bars([
        ("SHFE.rb2401", DATES[1], 3800.0, 500.0),
        ("CZCE.TA401", DATES[0], 5000.0, 800.0),
    ])
    result = audit(bars)

    assert result["earliest_by_product"] == {"TA": "2023-10-09", "rb": "2023-10-10"}
    assert result["symbols_by_product"] == {"TA": 1, "rb": 1}


def test_duplicate_symbol_date_is_rejected():
    bars = pd.concat([clean_bars(), clean_bars().iloc[[0]]], ignore_index=True)
    result = audit(bars)

    check = {item["id"]: item for item in result["checks"]}["C1"]
    assert check["ok"] is False
    assert result["ok"] is False


def test_placeholder_row_is_rejected():
    """datetime=0 的行落地后是 1970-01-01，必须为 0 行。"""
    bars = make_bars([
        ("DCE.m2401", pd.Timestamp("1970-01-01"), np.nan, 0.0),
        ("DCE.m2401", DATES[0], 3000.0, 1000.0),
    ])
    result = audit(bars)

    assert {item["id"]: item["ok"] for item in result["checks"]}["C2"] is False


def test_traded_row_with_blank_or_nonpositive_close_is_rejected():
    blank = make_bars([("DCE.m2401", DATES[0], np.nan, 1000.0)])
    assert {item["id"]: item["ok"] for item in audit(blank)["checks"]}["C3"] is False

    zero = make_bars([("DCE.m2401", DATES[0], 0.0, 1000.0)])
    assert {item["id"]: item["ok"] for item in audit(zero)["checks"]}["C3"] is False


def test_idle_row_with_filled_close_is_rejected():
    """无成交行若带非空收盘，说明被 ffill 覆盖过，收益会被污染。"""
    bars = make_bars([("DCE.m2401", DATES[0], 3000.0, 0.0)])
    result = audit(bars)

    check = {item["id"]: item for item in result["checks"]}["C4"]
    assert check["ok"] is False
    assert "1" in check["detail"]


def test_format_report_lists_stats_and_products():
    report = format_report(audit(clean_bars()), "data/bars_all.parquet")

    assert "数据源：data/bars_all.parquet" in report
    assert "品种 2" in report
    assert "| m | 1 | 2023-10-09 |" in report


def test_load_bars_requires_columns(tmp_path):
    path = tmp_path / "bars.parquet"
    pd.DataFrame({"symbol": ["DCE.m2401"]}).to_parquet(path)

    with pytest.raises(ValueError):
        load_bars(path)
