"""§8.4 执行合约与连续收益的手算测试（对应 future.md F1/F9 的简化版）。"""

import pandas as pd
import pytest

from cnfut.config import RollParams
from cnfut.data.rolling_contract import build_continuous, delivery_key, product_of

SESSIONS = pd.bdate_range("2023-10-02", periods=40)
# 自然人最后持仓日 → 交割月 = 次月；两者交割月不同，才能判断“只向远月切换”
NEAR_LIMIT = pd.Timestamp("2023-12-29")   # 交割月 2024-01
FAR_LIMIT = pd.Timestamp("2024-04-30")    # 交割月 2024-05


def make_bars(specs):
    """specs: [(symbol, limit_date, [close...], [oi...])] -> bars DataFrame。"""
    rows = []
    for symbol, limit_date, closes, ois in specs:
        for day, close, oi in zip(SESSIONS, closes, ois):
            rows.append({
                "symbol": symbol, "trade_date": day,
                "open": close - 1.0, "high": close + 2.0, "low": close - 2.0,
                "close": close, "volume": 100000.0, "close_oi": oi,
                "limit_date": limit_date,
            })
    return pd.DataFrame(rows)


def switch_fixture():
    """近月持仓 10 万手，远月在第 18 个交易日起反超到 12 万手。"""
    oi_near = [100000.0] * 40
    oi_far = [50000.0] * 18 + [120000.0] * 22
    return make_bars([
        ("SHFE.rb2401", NEAR_LIMIT, [3000.0 + i for i in range(40)], oi_near),
        ("SHFE.rb2405", FAR_LIMIT, [3200.0 - i for i in range(40)], oi_far),
    ])


def test_product_and_delivery_key():
    assert product_of("DCE.m2401") == "m"
    assert product_of("CZCE.TA601") == "TA"
    # 自然人最后持仓日 2023-12-29 → 交割月 2024-01
    assert delivery_key("2023-12-29") == 2024 * 12 + 1


def test_oi_switch_takes_effect_next_session():
    """持仓量反超 → t 日收盘判定、t+1 执行合约切换。"""
    out = build_continuous(switch_fixture(), RollParams()).set_index("trade_date")

    assert out["exec_symbol"].iloc[17] == "SHFE.rb2401"
    assert bool(out["roll_flag"].iloc[18]) is True          # 18 日收盘判定
    assert out["exec_symbol"].iloc[18] == "SHFE.rb2401"     # 当日仍持近月
    assert out["exec_symbol"].iloc[19] == "SHFE.rb2405"     # 次日生效


def test_roll_day_return_uses_same_contract():
    """换月日收益 = 新合约自身收益，不含换月价差（F9）。"""
    out = build_continuous(switch_fixture(), RollParams()).set_index("trade_date")

    roll_day = out.index[19]
    expected = (3200.0 - 19) / (3200.0 - 18) - 1.0   # 新合约前收 3182
    naive = (3200.0 - 19) / (3000.0 + 18) - 1.0      # 若错用旧合约前收
    assert out.loc[roll_day, "r_close"] == pytest.approx(expected, rel=0, abs=1e-12)
    assert abs(out.loc[roll_day, "r_close"] - naive) > 0.01


def test_forced_switch_when_current_becomes_ineligible():
    """当前执行合约逼近自然人最后持仓日 → 强制向远月切换。"""
    bars = make_bars([
        ("SHFE.rb2401", SESSIONS[20], [3000.0] * 40, [200000.0] * 40),
        ("SHFE.rb2405", FAR_LIMIT, [3100.0] * 40, [50000.0] * 40),
    ])
    out = build_continuous(bars, RollParams(exit_buffer_days=5)).set_index("trade_date")

    # limit 位于第 20 个交易日；20 - step > 5 才合格 → step=15 起不合格
    assert out["exec_symbol"].iloc[14] == "SHFE.rb2401"
    assert bool(out["roll_flag"].iloc[15]) is True
    assert out["exec_symbol"].iloc[16] == "SHFE.rb2405"


def test_no_switch_back_to_near_month():
    """只向远月切换：远月持仓量随后变小也不切回近月。"""
    oi_far = [50000.0] * 18 + [120000.0] * 2 + [1000.0] * 20
    bars = make_bars([
        ("SHFE.rb2401", NEAR_LIMIT, [3000.0] * 40, [100000.0] * 40),
        ("SHFE.rb2405", FAR_LIMIT, [3200.0] * 40, oi_far),
    ])
    out = build_continuous(bars, RollParams()).set_index("trade_date")

    assert out["exec_symbol"].iloc[21] == "SHFE.rb2405"
    assert set(out["exec_symbol"].iloc[21:]) == {"SHFE.rb2405"}


def test_truncation_guard():
    """t 之后的持仓量改动不影响 t 及以前的执行合约（F1 截断测试）。"""
    def specs(tail):
        return [
            ("SHFE.rb2401", NEAR_LIMIT, [3000.0] * 40, [100000.0] * 40),
            ("SHFE.rb2405", FAR_LIMIT, [3200.0] * 40, [50000.0] * 25 + tail),
        ]

    left = build_continuous(make_bars(specs([50000.0] * 15)), RollParams())
    right = build_continuous(make_bars(specs([900000.0] * 15)), RollParams())

    head_left = left.set_index("trade_date")["exec_symbol"].iloc[:20]
    head_right = right.set_index("trade_date")["exec_symbol"].iloc[:20]
    pd.testing.assert_series_equal(head_left, head_right)


def test_atr_available_after_window():
    bars = make_bars([
        ("SHFE.rb2401", NEAR_LIMIT, [3000.0 + i for i in range(40)], [100000.0] * 40),
    ])
    out = build_continuous(bars, RollParams()).set_index("trade_date")

    assert out["atr_pts"].iloc[:20].isna().all()
    assert out["atr_pts"].iloc[25] > 0
