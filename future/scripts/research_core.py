"""最小纵切研究：连续收益 → 特征 → 打分选品种 → 回测 → 指标。

策略（先用可解释的规则基线，对应 §14.6 的情景）：
* ``tsm_only``：时序动量 ``tsm_volsc_60`` 的截面百分位排序，方向由 ``tsm_sign`` 门控；
* ``xsm_only``：截面动量 ``xsm_skip_60_5`` 排序，方向同上。

信号在 t 日收盘产生，t+1 交易日开盘执行（§7.3）。手数在 t 日按 §4.3 估算；MVP 阶段用初始
本金（不随权益复利）计算，这是本纵切已知的简化，报告中注明。

用法::

    python future/scripts/research_core.py --strategy tsm_only --horizon 10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cnfut.backtest.engine import Backtester                       # noqa: E402
from cnfut.backtest.metrics import summarize                        # noqa: E402
from cnfut.config import Params, RollParams                         # noqa: E402
from cnfut.data.rolling_contract import build_continuous            # noqa: E402

LIMIT_PCT = 0.07          # §22 已知偏差：历史涨跌停幅度不可得，用固定值
FEE_RATE = 1e-4           # 成交额万分之 1（交易所标准量级）
DEFAULT_TICK = 1.0


def load_bars(path: Path) -> pd.DataFrame:
    bars = pd.read_parquet(path)
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars["limit_date"] = pd.to_datetime(bars["limit_date"])
    return bars


def build_features(cont: pd.DataFrame) -> pd.DataFrame:
    """§10.1/§10.2：时序动量、截面动量、方向门控。全部只用 ≤ t 的数据。"""
    frame = cont.sort_values(["product", "trade_date"]).copy()
    frame["log_p"] = np.log(frame["P"].where(frame["P"] > 0))
    group = frame.groupby("product", sort=False)
    for window in (20, 60, 120):
        frame[f"tsm_ret_{window}"] = frame["log_p"] - group["log_p"].shift(window)

    vol60 = group["r_close"].transform(lambda s: s.rolling(60, min_periods=40).std())
    frame["sigma60"] = vol60
    for window in (20, 60, 120):
        frame[f"tsm_volsc_{window}"] = frame[f"tsm_ret_{window}"] / (vol60 * np.sqrt(window))

    gate = frame[["tsm_volsc_20", "tsm_volsc_60", "tsm_volsc_120"]].sum(axis=1, min_count=3)
    frame["tsm_sign"] = np.sign(gate)
    frame["xsm_skip_60_5"] = group["log_p"].shift(5) - group["log_p"].shift(60)
    return frame


def cross_section_pct(frame: pd.DataFrame, column: str) -> pd.Series:
    """按交易日做截面百分位（§10.9）。"""
    return frame.groupby("trade_date")[column].rank(pct=True)


def build_targets(features: pd.DataFrame, params: Params, capital: float,
                  horizon: int, score_column: str) -> dict:
    """每 horizon 个交易日调仓；返回 {执行日(次日): {product: 带符号手数}}。"""
    frame = features.dropna(subset=[score_column, "tsm_sign", "atr_pts"]).copy()
    frame["pct"] = cross_section_pct(frame, score_column)
    dates = np.sort(frame["trade_date"].unique())
    next_date = {pd.Timestamp(dates[i]): pd.Timestamp(dates[i + 1])
                 for i in range(len(dates) - 1)}

    products = sorted(frame["product"].unique())
    targets = {}
    for step in range(0, len(dates), horizon):
        day = pd.Timestamp(dates[step])
        today = frame[frame["trade_date"] == day]
        if today.empty or day not in next_date:
            continue
        # §4.2：入选品种少于 min_pool_size 的日期不交易（持仓按规则自然退出）
        if today["product"].nunique() < params.universe.min_pool_size:
            targets[next_date[day]] = {product: 0 for product in products}
            continue

        longs = today[(today["pct"] >= 1.0 - params.portfolio.long_q) & (today["tsm_sign"] > 0)]
        shorts = today[(today["pct"] <= params.portfolio.short_q) & (today["tsm_sign"] < 0)]
        picked = {}
        if params.portfolio.structure == "long_short":
            if len(longs):
                picked[longs.sort_values("pct").iloc[-1]["product"]] = 1
            if len(shorts):
                picked[shorts.sort_values("pct").iloc[0]["product"]] = -1

        rows = {row.product: row for row in today.itertuples()}
        target = {}
        for product in products:
            direction = picked.get(product, 0)
            target[product] = 0 if direction == 0 else \
                direction * size_lots(rows[product], params, capital)
        targets[next_date[day]] = target
    return targets


def newey_west_t(series: pd.Series, lag: int) -> float:
    """Newey-West t 值（§14.8：重叠收益必须做自相关修正）。"""
    values = series.dropna().to_numpy(dtype=float)
    n = len(values)
    if n < 3:
        return float("nan")
    mean = values.mean()
    errors = values - mean
    variance = float(errors @ errors) / n
    for step in range(1, lag + 1):
        weight = 1.0 - step / (lag + 1.0)
        gamma = float(errors[step:] @ errors[:-step]) / n
        variance += 2.0 * weight * gamma
    if variance <= 0:
        return float("nan")
    return float(mean / np.sqrt(variance / n))


def rank_ic(features: pd.DataFrame, score_column: str, horizon: int) -> dict:
    """§14.8：score 与波动标准化远期收益的截面 Rank IC（流动性池内）。"""
    frame = features.dropna(subset=[score_column, "sigma60"]).copy()
    frame = frame.sort_values(["product", "trade_date"])
    forward = frame.groupby("product")["P"].shift(-horizon) / frame["P"] - 1.0
    frame["z"] = forward / (frame["sigma60"] * np.sqrt(horizon))

    values = []
    for _, group in frame.groupby("trade_date"):
        clean = group.dropna(subset=[score_column, "z"])
        if len(clean) >= 4:
            values.append(clean[score_column].corr(clean["z"], method="spearman"))
    series = pd.Series(values).dropna()
    return {
        "rank_ic_mean": round(float(series.mean()), 4) if len(series) else None,
        "rank_ic_t_nw": round(newey_west_t(series, horizon), 2) if len(series) else None,
        "rank_ic_days": int(len(series)),
    }


def size_lots(row, params: Params, equity: float) -> int:
    """§4.2/§4.3：按单手风险与三条门槛定手数。"""
    mult = float(getattr(row, "volume_multiple", np.nan))
    atr = float(row.atr_pts)
    close = float(row.close)
    tick = float(getattr(row, "price_tick", DEFAULT_TICK))
    if not (np.isfinite(mult) and np.isfinite(atr) and np.isfinite(close)) or atr <= 0:
        return 0
    risk_lot = (params.risk.risk_atr_k * atr * mult
                + 2 * params.cost.slippage_ticks * tick * mult)
    if risk_lot <= 0:
        return 0
    notional = close * mult
    if notional * 0.12 > params.universe.max_margin_lot_frac * equity:   # G1
        return 0
    if notional > params.risk.max_notional_lot_mult * equity:            # G3
        return 0
    lots = int(params.risk.risk_per_trade * equity // risk_lot)          # G2
    return max(0, min(lots, params.risk.max_lots_per_product))


def build_engine_inputs(bars: pd.DataFrame, cont: pd.DataFrame) -> tuple:
    """把合约日线与连续序列拼成引擎需要的两张表。"""
    merged = cont.merge(bars, left_on=["exec_symbol", "trade_date"],
                        right_on=["symbol", "trade_date"], how="inner", suffixes=("", "_bar"))
    merged["price_tick"] = merged["price_tick"].fillna(DEFAULT_TICK)
    merged["limit_up"] = merged["prev_close"] * (1.0 + LIMIT_PCT)
    merged["limit_down"] = merged["prev_close"] * (1.0 - LIMIT_PCT)
    fee = merged["close"] * merged["volume_multiple"] * FEE_RATE * 2.0
    merged["fee_open"] = fee
    merged["fee_close"] = fee
    merged["product"] = merged["product"].astype(str)
    engine_bars = merged[["trade_date", "symbol", "product", "open", "high", "low", "close",
                          "limit_up", "limit_down", "volume_multiple", "price_tick",
                          "margin_rate", "fee_open", "fee_close", "atr_pts"]]
    exec_map = merged[["trade_date", "product", "exec_symbol"]].drop_duplicates()
    return engine_bars, exec_map


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="最小纵切研究：单因子 -> 回测")
    parser.add_argument("--data", default="data/bars_all.parquet")
    parser.add_argument("--strategy", default="tsm_only", choices=["tsm_only", "xsm_only"])
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--capital", type=float, default=20000.0)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2023-12-31")
    parser.add_argument("--out", default="artifacts/backtest")
    args = parser.parse_args(argv)

    params = Params()
    bars = load_bars(Path(args.data))
    cont = build_continuous(bars, RollParams())
    features = build_features(cont)
    features = features.merge(
        bars[["symbol", "trade_date", "volume_multiple", "price_tick"]]
        .rename(columns={"symbol": "exec_symbol"}),
        on=["exec_symbol", "trade_date"], how="left")

    score_column = "tsm_volsc_60" if args.strategy == "tsm_only" else "xsm_skip_60_5"
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    window = features[(features["trade_date"] >= start) & (features["trade_date"] <= end)]
    targets = build_targets(window, params, args.capital, args.horizon, score_column)

    engine_bars, exec_map = build_engine_inputs(bars, cont)
    engine_bars = engine_bars[(engine_bars["trade_date"] >= start)
                              & (engine_bars["trade_date"] <= end)]
    exec_map = exec_map[(exec_map["trade_date"] >= start) & (exec_map["trade_date"] <= end)]

    result = Backtester(params, engine_bars, exec_map).run(targets)
    stats = summarize(result, args.capital)
    stats.update({"strategy": args.strategy, "horizon": args.horizon,
                  "products": int(window["product"].nunique()),
                  "rebalances": len(targets)})
    stats.update(rank_ic(window, score_column, args.horizon))

    out_dir = Path(args.out) / f"{args.strategy}_h{args.horizon}"
    out_dir.mkdir(parents=True, exist_ok=True)
    result.equity.to_csv(out_dir / "equity_daily.csv", encoding="utf-8-sig")
    result.trades.to_csv(out_dir / "trades.csv", index=False, encoding="utf-8-sig")
    (out_dir / "metrics.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"[done] {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
