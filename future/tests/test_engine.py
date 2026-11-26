"""§14 引擎手算测试：权益、成本、止损、涨跌停、移仓、风险度。

约定（与引擎一致）：滑点 1 tick 计入成交价；手续费按手；权益 = cash + 持仓浮盈。
[R-04] 平仓日先实现盈亏再盯市，因此下面每个用例都能用四则运算核对到 1e-9。
"""

import numpy as np
import pandas as pd
import pytest

from cnfut.backtest.engine import Backtester
from cnfut.config import Params

DAYS = pd.bdate_range("2024-01-02", periods=4)
FAR = 1e9


def bar(day, symbol, product, open_, close, high=None, low=None, atr=np.nan,
        fee=1.0, mult=10.0, margin_rate=0.1, tick=1.0, limit_up=FAR, limit_down=0.0):
    """构造一行日线；默认 high/low 在 open/close 之外各留 1 个价位，避免误判一字板。"""
    return {
        "trade_date": day, "symbol": symbol, "product": product,
        "open": float(open_), "close": float(close),
        "high": float(high if high is not None else max(open_, close) + 1),
        "low": float(low if low is not None else min(open_, close) - 1),
        "limit_up": float(limit_up), "limit_down": float(limit_down),
        "volume_multiple": mult, "price_tick": tick, "margin_rate": margin_rate,
        "fee_open": fee, "fee_close": fee, "atr_pts": atr,
    }


def exec_map(rows):
    return pd.DataFrame(rows, columns=["trade_date", "product", "exec_symbol"])


def test_long_round_trip_equity_hand_calc():
    bars = pd.DataFrame([
        bar(DAYS[0], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[1], "SHFE.aa2401", "aa", 100, 102),
        bar(DAYS[2], "SHFE.aa2401", "aa", 103, 103),
        bar(DAYS[3], "SHFE.aa2401", "aa", 103, 103),
    ])
    mapping = exec_map([(d, "aa", "SHFE.aa2401") for d in DAYS])
    result = Backtester(Params(), bars, mapping).run({DAYS[1]: {"aa": 1}, DAYS[2]: {"aa": 0}})

    # 开仓：成交 101、手续费 1 → cash 19999；收盘 102 → 浮盈 10
    assert result.equity.loc[DAYS[1], "equity"] == pytest.approx(20009.0, abs=1e-9)
    assert result.equity.loc[DAYS[1], "margin"] == pytest.approx(102.0, abs=1e-9)
    # 平仓：成交 102、实现 +10、手续费 1 → cash 20008
    assert result.equity.loc[DAYS[2], "equity"] == pytest.approx(20008.0, abs=1e-9)
    assert result.equity.loc[DAYS[2], "margin"] == pytest.approx(0.0, abs=1e-9)
    assert result.trades["fee"].sum() == pytest.approx(2.0)


def test_stop_gap_fills_at_open_minus_slippage():
    bars = pd.DataFrame([
        bar(DAYS[0], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[1], "SHFE.aa2401", "aa", 100, 100, atr=2.0),   # 止损 = 101 - 3×2 = 95
        bar(DAYS[2], "SHFE.aa2401", "aa", 90, 90, low=88),
        bar(DAYS[3], "SHFE.aa2401", "aa", 90, 90),
    ])
    mapping = exec_map([(d, "aa", "SHFE.aa2401") for d in DAYS])
    result = Backtester(Params(), bars, mapping).run({DAYS[1]: {"aa": 1}})

    stop_trade = result.trades.iloc[-1]
    assert stop_trade["reason"] == "止损"
    assert stop_trade["price"] == pytest.approx(89.0)          # 跳空 → 开盘价 − 1 tick
    assert result.equity.loc[DAYS[2], "equity"] == pytest.approx(19878.0, abs=1e-9)


def test_stop_intraday_fills_at_stop_price():
    bars = pd.DataFrame([
        bar(DAYS[0], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[1], "SHFE.aa2401", "aa", 100, 100, atr=2.0),
        bar(DAYS[2], "SHFE.aa2401", "aa", 100, 99, low=94),
        bar(DAYS[3], "SHFE.aa2401", "aa", 99, 99),
    ])
    mapping = exec_map([(d, "aa", "SHFE.aa2401") for d in DAYS])
    result = Backtester(Params(), bars, mapping).run({DAYS[1]: {"aa": 1}})

    stop_trade = result.trades.iloc[-1]
    assert stop_trade["reason"] == "止损"
    assert stop_trade["price"] == pytest.approx(94.0)          # 止损价 95 − 1 tick
    assert result.equity.loc[DAYS[2], "equity"] == pytest.approx(19928.0, abs=1e-9)


def test_limit_up_blocks_buy_open():
    """涨停（开盘价已到涨停价 − 余量）不能买开，且不留持仓。"""
    bars = pd.DataFrame([
        bar(DAYS[0], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[1], "SHFE.aa2401", "aa", 100, 101, limit_up=101),
        bar(DAYS[2], "SHFE.aa2401", "aa", 101, 101),
        bar(DAYS[3], "SHFE.aa2401", "aa", 101, 101),
    ])
    mapping = exec_map([(d, "aa", "SHFE.aa2401") for d in DAYS])
    result = Backtester(Params(), bars, mapping).run({DAYS[1]: {"aa": 1}})

    assert result.trades.iloc[0]["status"] == "涨停无法买入"
    assert result.equity.loc[DAYS[1], "n_positions"] == 0
    assert result.equity.loc[DAYS[1], "equity"] == pytest.approx(20000.0, abs=1e-9)


def test_one_price_board_blocks_everything():
    bars = pd.DataFrame([
        bar(DAYS[0], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[1], "SHFE.aa2401", "aa", 100, 100, high=100, low=100),
        bar(DAYS[2], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[3], "SHFE.aa2401", "aa", 100, 100),
    ])
    mapping = exec_map([(d, "aa", "SHFE.aa2401") for d in DAYS])
    result = Backtester(Params(), bars, mapping).run({DAYS[1]: {"aa": 1}})

    assert result.trades.iloc[0]["status"] == "一字板"
    assert result.equity.loc[DAYS[1], "n_positions"] == 0


def test_roll_closes_old_and_opens_new_with_two_costs():
    """移仓 = 平旧 + 开新两笔成交，各自计手续费与滑点。"""
    bars = pd.DataFrame([
        bar(DAYS[0], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[1], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[2], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[3], "SHFE.aa2401", "aa", 100, 100),
        bar(DAYS[0], "SHFE.aa2405", "aa", 210, 215),
        bar(DAYS[1], "SHFE.aa2405", "aa", 210, 215),
        bar(DAYS[2], "SHFE.aa2405", "aa", 210, 215),
        bar(DAYS[3], "SHFE.aa2405", "aa", 210, 215),
    ])
    mapping = exec_map([(d, "aa", "SHFE.aa2401" if i < 2 else "SHFE.aa2405")
                        for i, d in enumerate(DAYS)])
    result = Backtester(Params(), bars, mapping).run({DAYS[1]: {"aa": 1}})

    roll_trades = result.trades[result.trades["reason"] == "移仓"]
    assert len(roll_trades) == 2
    assert roll_trades.iloc[0]["price"] == pytest.approx(99.0)    # 平旧：100 − 1
    assert roll_trades.iloc[1]["price"] == pytest.approx(211.0)   # 开新：210 + 1
    # cash：20000 −1（开）−20（平旧盈亏）−1（平旧费）−1（开新费）= 19977，浮盈 (215−211)×10 = 40
    assert result.equity.loc[DAYS[2], "equity"] == pytest.approx(20017.0, abs=1e-9)
    assert result.positions.loc[result.positions["trade_date"] == DAYS[2], "symbol"].iloc[0] \
        == "SHFE.aa2405"

