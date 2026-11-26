"""§14.8 指标：年化收益、夏普、回撤、Calmar、成本拖累、换手。"""

from __future__ import annotations

import numpy as np
import pandas as pd

ANNUAL_DAYS = 243


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().fillna(0.0)


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
