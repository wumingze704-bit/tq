"""`metrics.block_bootstrap_annual_return` 的手算测试。

判据 `ledger.acceptance.secondary` 第 1 条要求"分块 bootstrap 95% 下限 > 0"，
这里验证：固定种子可复现、常数收益时下限等于收益、负收益时下限为负、样本不足返回空。
"""

import numpy as np
import pandas as pd

from cnfut.backtest.metrics import block_bootstrap_annual_return, summarize


def equity_from_returns(returns, start=20000.0):
    return pd.Series(start * np.cumprod(1.0 + np.asarray(returns, dtype=float)))


def test_constant_returns_give_lower_equal_to_return():
    equity = equity_from_returns(np.full(400, 0.001))       # 每日 +0.1%
    result = block_bootstrap_annual_return(equity, samples=200, block=20)
    # pct_change 让序列首个收益为 0，且分块重采样会重复/丢弃个别块，
    # 因此只要求区间围绕理论年化 (1.001^243-1) 在 3% 相对误差内，且下限 <= 均值 <= 上限。
    expected = (1.001 ** 243) - 1.0
    assert result["lower"] is not None
    assert result["lower"] <= result["mean"] <= result["upper"]
    assert abs(result["lower"] - expected) / expected < 0.03
    assert abs(result["upper"] - expected) / expected < 0.03


def test_seed_makes_result_reproducible():
    rng = np.random.default_rng(7)
    equity = equity_from_returns(rng.normal(0.0003, 0.01, 500))
    first = block_bootstrap_annual_return(equity, samples=300, block=20)
    second = block_bootstrap_annual_return(equity, samples=300, block=20)
    assert first == second


def test_negative_mean_returns_give_negative_lower_bound():
    rng = np.random.default_rng(11)
    equity = equity_from_returns(rng.normal(-0.001, 0.01, 500))
    result = block_bootstrap_annual_return(equity, samples=300, block=20)
    assert result["lower"] < 0


def test_short_sample_returns_none():
    equity = equity_from_returns(np.full(10, 0.001))
    result = block_bootstrap_annual_return(equity, samples=100, block=20)
    assert result["lower"] is None
    assert result["days"] == 10
