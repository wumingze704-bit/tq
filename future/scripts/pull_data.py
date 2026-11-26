"""拉取日线并落地为 parquet（最小纵切：品种数可控、串行、可续跑）。

用法::

    python future/scripts/pull_data.py --products m,rb,TA,c --start 2019-01-01 --end 2023-12-31
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tqsdk import TqApi, TqAuth                      # noqa: E402

from cnfut.data.fetch import DataFetcher, FetchConfig, contract_meta  # noqa: E402

BAR_COLUMNS = ["symbol", "trade_date", "open", "high", "low", "close",
               "volume", "open_oi", "close_oi", "limit_date",
               "volume_multiple", "price_tick", "margin_rate"]


def read_auth() -> TqAuth:
    user = os.environ.get("TQ_AUTH_USER", "").strip()
    password = os.environ.get("TQ_AUTH_PASSWORD", "").strip()
    if not (user and password):
        raise SystemExit("缺少 TQ_AUTH_USER / TQ_AUTH_PASSWORD 环境变量")
    return TqAuth(user, password)


def last_hold_dates(meta: pd.DataFrame, trading_days: pd.DatetimeIndex) -> dict:
    """交割月前一月的最后一个交易日（自然人最后持仓日，§8.4）。"""
    result = {}
    for row in meta.itertuples():
        year = getattr(row, "delivery_year", None)
        month = getattr(row, "delivery_month", None)
        if pd.isna(year) or pd.isna(month):
            continue
        prev_month_end = pd.Timestamp(year=int(year), month=int(month), day=1) - pd.Timedelta(days=1)
        candidates = trading_days[trading_days <= prev_month_end]
        if len(candidates):
            result[row.instrument_id] = candidates[-1]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="拉取期货日线到 parquet")
    parser.add_argument("--products", default="m,rb,TA,c")
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2023-12-31")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--margin-rate", type=float, default=0.12,
                        help="§8.6 的历史保证金率未知，先用固定值并记入偏差")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)

    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    api = TqApi(auth=read_auth(), disable_print=True)
    fetcher = DataFetcher(api, FetchConfig(data_dir=data_dir),
                          secrets=[os.environ.get("TQ_AUTH_PASSWORD", "")])
    try:
        calendar = api.get_trading_calendar(start - pd.Timedelta(days=400),
                                            end + pd.Timedelta(days=30))
        trading_days = pd.DatetimeIndex(calendar.loc[calendar["trading"], "date"])

        frames = []
        summary = []
        for product in [p.strip() for p in args.products.split(",") if p.strip()]:
            symbols = list(api.query_quotes(ins_class="FUTURE", product_id=product,
                                            expired=True))
            symbols += list(api.query_quotes(ins_class="FUTURE", product_id=product,
                                             expired=False))
            if not symbols:
                summary.append({"product": product, "contracts": 0, "bars": 0})
                continue
            meta = contract_meta(api, symbols, secrets=[os.environ.get("TQ_AUTH_PASSWORD", "")])
            holds = last_hold_dates(meta, trading_days)
            meta = meta.set_index("instrument_id")

            got = 0
            n_contracts = 0
            for symbol in symbols:
                if symbol not in holds:
                    continue
                frame = fetcher.fetch_symbol(symbol, start, end, refresh=args.refresh)
                if frame is None:
                    continue
                limit_date = holds[symbol]
                if frame["trade_date"].max() >= limit_date:
                    frame = frame[frame["trade_date"] < limit_date]
                if frame.empty:
                    continue
                info = meta.loc[symbol]
                frame = frame.assign(
                    limit_date=limit_date,
                    volume_multiple=float(info.get("volume_multiple", float("nan"))),
                    price_tick=float(info.get("price_tick", float("nan"))),
                    margin_rate=args.margin_rate)
                frames.append(frame[BAR_COLUMNS])
                got += len(frame)
                n_contracts += 1
            summary.append({"product": product, "contracts": n_contracts, "bars": got})
            print(f"[ok] {product}: contracts={n_contracts} bars={got} "
                  f"failures={len(fetcher.failures)}")

        if not frames:
            print("[fail] 没有取到任何日线")
            return 3

        bars = pd.concat(frames, ignore_index=True).sort_values(["symbol", "trade_date"])
        out_path = data_dir / "bars_all.parquet"
        if out_path.exists():
            old = pd.read_parquet(out_path)
            bars = (pd.concat([old, bars], ignore_index=True)
                    .drop_duplicates(["symbol", "trade_date"], keep="last")
                    .sort_values(["symbol", "trade_date"]))
        bars.to_parquet(out_path, index=False)
        print(f"[done] rows={len(bars)} symbols={bars['symbol'].nunique()} "
              f"range={bars['trade_date'].min().date()}~{bars['trade_date'].max().date()}")
        print(f"[done] 写入 {out_path}")
        if fetcher.failures:
            pd.DataFrame(fetcher.failures).to_parquet(data_dir / "fetch_failures.parquet",
                                                      index=False)
            print(f"[warn] 失败 {len(fetcher.failures)} 个合约，明细见 fetch_failures.parquet")
        return 0
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
