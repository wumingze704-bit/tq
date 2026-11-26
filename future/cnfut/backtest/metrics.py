"""§14.8 指标：年化收益、夏普、回撤、Calmar、成本拖累、换手。"""

from __future__ import annotations

import numpy as np
import pandas as pd

ANNUAL_DAYS = 243


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().fillna(0.0)


def block_bootstrap_annual_return(equity: pd.Series, samples: int = 2000,
                                  block: int = 20, seed: int = 20261126,
                                  alpha: float = 0.05) -> dict:
    """分块 bootstrap 的年化收益区间（`ledger.acceptance.secondary` 第 1 条要用）。

    为什么分块而不是逐日：策略日收益存在自相关（重叠持有期），逐日重采样会把标准误低估；
    按 `block` 天整块重采样保留块内自相关（§14.8 同口径）。固定随机种子保证可复现。
    返回 ``{lower, upper, mean, samples, block, days}``；样本不足返回 ``None`` 值。
    """
    rets = daily_returns(equity).to_numpy(dtype=float)
    n = int(len(rets))
    if n < block * 2:
        return {"lower": None, "upper": None, "mean": None,
                "samples": samples, "block": block, "days": n}
    count = n // block
    shaped = rets[: count * block].reshape(count, block)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, count, size=(samples, count))
    totals = np.prod(1.0 + shaped[picks], axis=(1, 2)) - 1.0        # 每个样本的累计收益
    span = count * block
    annual = np.power(1.0 + totals, ANNUAL_DAYS / span) - 1.0
    return {
        "lower": round(float(np.percentile(annual, 100 * alpha)), 4),
        "upper": round(float(np.percentile(annual, 100 * (1 - alpha))), 4),
        "mean": round(float(annual.mean()), 4),
        "samples": int(samples),
        "block": int(block),
        "days": n,
    }


def summarize(result, capital: float, rf_annual: float = 0.0) -> dict:
    """从 `Backtester.run` 的返回值计算指标。"""
    eq = result.equity["equity"]
    if len(eq) < 2:
        return {"days": int(len(eq))}
    rets = daily_returns(eq)
    years = len(eq) / ANNUAL_DAYS
    total_return = eq.iloc[-1] / capital - 1.0
    annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0 if years > 0 else np.nan
    vol = rets.std(ddof=1) * np.sqrt(ANNUAL_DAYS)
    rf_daily = (1.0 + rf_annual) ** (1.0 / ANNUAL_DAYS) - 1.0
    sharpe = (rets.mean() - rf_daily) / rets.std(ddof=1) * np.sqrt(ANNUAL_DAYS) \
        if rets.std(ddof=1) > 0 else np.nan

    peak = eq.cummax()
    drawdown = 1.0 - eq / peak
    max_dd = float(drawdown.max())
    calmar = annual_return / max_dd if max_dd > 0 else np.nan

    fees = float(result.trades["fee"].fillna(0).sum()) if len(result.trades) else 0.0
    trades = result.trades[result.trades["status"] == "成交"] if len(result.trades) else result.trades
    notional = 0.0
    if len(trades):
        notional = float((trades["price"].abs() * trades["lots"].abs()).sum())
    avg_equity = float(eq.mean())

    monthly = eq.resample("ME").last().pct_change().dropna()
    return {
        "days": int(len(eq)),
        "years": round(years, 2),
        "total_return": round(float(total_return), 4),
        "annual_return": round(float(annual_return), 4),
        "annual_vol": round(float(vol), 4),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown": round(max_dd, 4),
        "calmar": None if not np.isfinite(calmar) else round(float(calmar), 3),
        "worst_month": None if monthly.empty else round(float(monthly.min()), 4),
        "fee_total": round(fees, 2),
        "fee_drag_annual": round(fees / avg_equity / years, 4) if years > 0 else None,
        "turnover_annual": round(notional / avg_equity / years, 2) if years > 0 else None,
        "n_trades": int(len(trades)),
        "n_blocked": int((result.trades["status"] != "成交").sum()) if len(result.trades) else 0,
        "max_risk_ratio": round(float(result.equity["risk_ratio"].replace([np.inf], np.nan).max()), 3),
        "n_positions_max": int(result.equity["n_positions"].max()),
    }
