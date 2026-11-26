"""R2 数据回填：按代码规则补齐 SHFE/DCE 的 2017-09~2019-09 历史日线。

为什么需要这个脚本（`docs/research/SETUP_DONE.md` §2、`ledger.blockers` B1 / 评审 R-25）：
``query_quotes(expired=True)`` 返回的已下市合约列表被截断，`pull_data.py` 因此从未请求
2019 年以前到期的 SHFE/DCE 合约。逐个合约实测已证明 ``SHFE.rb1801``、``DCE.m1801`` 的日线
**直接按代码订阅可取**，所以回填必须"按品种挂牌月份生成合约代码 → 直接取日线"。

流程（对应 `plan/1-研究循环.txt` §四 R2.1~R2.4）：

1. 从现有 ``bars_all.parquet`` 统计每个品种**实际挂牌**的交割月份与交易所；
2. 用 `cnfut.data.contracts.plan_contracts` 生成 ``[start, end + extra_days]`` 内到期的候选合约；
3. 逐个 ``get_kline_serial(symbol, 86400, data_length=10000)``（T2），串行 + 1s 间隔（限频，
   不是"用 sleep 等数据"，T3），失败重试 2 次；仍失败写 ``fetch_failures_r2.parquet``，
   **不使用替代数据、不外推**；
4. ``limit_date`` 用"交割月前一月最后交易日"，只保留 ``trade_date < limit_date`` 的行；
5. 合并去重（``symbol + trade_date``）后写回 ``bars_all.parquet``；体检交给
   ``scripts/data_health.py``（C1~C4 全通过才允许重跑回测）。

口径说明（实测得出，写进 R2.md）：合约服务对**不存在**的合约是**立即报错**（``contains
non-existent instrument``），因此不存在合约只花 ~0.1s；``query_symbol_info`` 在老合约上
会因行情订阅超时而变慢，所以本脚本**不逐合约查主数据**，``volume_multiple`` / ``price_tick``
一律用同品种已有合约的众数回填（合约乘数是品种静态属性，不是行情数据）。

用法::

    cd future
    python scripts/backfill_history.py --dry-run          # 只打印计划，不联网
    python scripts/backfill_history.py                    # 实际回填
    python scripts/backfill_history.py --products rb,m    # 只补部分品种
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tqsdk import TqApi                                       # noqa: E402

from cnfut.data.contracts import delivery_months, plan_contracts   # noqa: E402
from cnfut.data.fetch import DataFetcher, FetchConfig, product_of   # noqa: E402
from scripts.pull_data import BAR_COLUMNS, read_auth           # noqa: E402

DEFAULT_PRODUCTS = "rb,eg,jd,i,p,c,m,y"     # barrier B1 的 8 个 SHFE/DCE 品种
TARGET_EXCHANGES = ("SHFE", "DCE")


def secret() -> str:
    """凭据只从环境变量读（T11），不写文件、不打印。"""
    return os.environ.get("TQ_AUTH_PASSWORD", "")


def product_fallbacks(existing: pd.DataFrame) -> dict:
    """按品种取 ``volume_multiple`` / ``price_tick`` 的众数，供查不到元数据的老合约回填。

    合约乘数与最小变动价位是**品种静态属性**，不是行情数据；用同品种已有合约的众数回填
    不构成外推（R1 红线针对的是价格/收益）。
    """
    result = {}
    for product, sub in existing.groupby("product", sort=True):
        row = {}
        for column in ("volume_multiple", "price_tick"):
            values = pd.to_numeric(sub[column], errors="coerce").dropna()
            row[column] = float(values.mode().iloc[0]) if len(values) else float("nan")
        result[product] = row
    return result


def build_plan(bars: pd.DataFrame, products, start, end, sessions, extra_days: int,
               force=()) -> tuple:
    """按品种生成待回填合约清单（已入库的合约跳过，``force`` 里的除外）。返回 (plan, notes)。"""
    existing = bars.assign(product=bars["symbol"].map(product_of))
    have = set(existing["symbol"]) - set(force)
    plan, notes = [], []
    for product in products:
        sub = existing[existing["product"] == product]
        if sub.empty:
            notes.append(f"[skip] {product}: bars 中无该品种，无法推断交易所与挂牌月份")
            continue
        exchange = sub["symbol"].iloc[0].split(".", 1)[0]
        if exchange not in TARGET_EXCHANGES:
            notes.append(f"[skip] {product}: 交易所 {exchange} 不在本轮范围（{TARGET_EXCHANGES}）")
            continue
        months = delivery_months(sub["symbol"].unique(), product)
        if not months:
            notes.append(f"[skip] {product}: 无法从合约代码推断交割月份")
            continue
        years = range(int(start.year) - 1, int(end.year) + 2)
        rows = plan_contracts(exchange, product, months, years, sessions, start, end,
                              extra_days=extra_days)
        selected = [row for row in rows if row["symbol"] not in have]
        plan.extend(selected)
        notes.append(f"[plan] {product} ({exchange}) 挂牌月份={months} "
                     f"候选={len(rows)} 已入库={len(rows) - len(selected)} 待取={len(selected)}")
    return plan, notes



def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="R2：按代码生成合约并回填 SHFE/DCE 历史日线")
    parser.add_argument("--products", default=DEFAULT_PRODUCTS)
    parser.add_argument("--start", default="2017-09-01")
    parser.add_argument("--end", default="2019-09-30")
    parser.add_argument("--extra-days", type=int, default=120,
                        help="候选合约的 limit_date 上界放宽（保证右端仍有远月可选）")
    parser.add_argument("--data", default="data/bars_all.parquet")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--margin-rate", type=float, default=0.12,
                        help="历史保证金率不可得，统一 12%（已知偏差）")
    parser.add_argument("--retries", type=int, default=2, help="失败重试次数（T3）")
    parser.add_argument("--backoff", type=float, default=3.0)
    parser.add_argument("--pause", type=float, default=1.0, help="串行间隔秒数（限频）")
    parser.add_argument("--session-size", type=int, default=20,
                        help="每个 TqApi 会话最多取多少个合约（实测累计订阅多后请求会超时）")
    parser.add_argument("--only-symbols", default="",
                        help="只处理这些合约（逗号分隔）；用于把已入库合约的区间往前补")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不联网、不写文件")
    args = parser.parse_args(argv)

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    data_dir = Path(args.data_dir)
    bars_path = Path(args.data)
    bars = pd.read_parquet(bars_path)
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars["limit_date"] = pd.to_datetime(bars["limit_date"])
    products = [p.strip() for p in args.products.split(",") if p.strip()]
    only = {s.strip() for s in args.only_symbols.split(",") if s.strip()}

    if args.dry_run:
        # 只读：工作日序列占位（不含真实假日，够打印量级与代码规则核对）
        sessions = pd.bdate_range(start - pd.Timedelta(days=400),
                                  end + pd.Timedelta(days=args.extra_days + 60))
        plan, notes = build_plan(bars, products, start, end, sessions, args.extra_days,
                                 force=only)
        if only:
            plan = [row for row in plan if row["symbol"] in only]
        print("\n".join(notes))
        print(f"[dry-run] 待取合约 {len(plan)} 个："
              f"{', '.join(row['symbol'] for row in plan[:12])}"
              f"{' ...' if len(plan) > 12 else ''}")
        print("[dry-run] 未联网、未写文件")
        return 0

    # 会话按块重启：实测同一 TqApi 累计订阅数变多后，K 线请求会退化成 85s 超时
    # （同样的请求在新会话里 0.2s 返回）。因此每块用一个新会话，取完即关。
    api = TqApi(auth=read_auth(), disable_print=True)
    try:
        calendar = api.get_trading_calendar(start - pd.Timedelta(days=400),
                                            end + pd.Timedelta(days=args.extra_days + 60))
        sessions = pd.DatetimeIndex(calendar.loc[calendar["trading"], "date"])
    finally:
        api.close()

    plan, notes = build_plan(bars, products, start, end, sessions, args.extra_days,
                             force=only)
    if only:
        plan = [row for row in plan if row["symbol"] in only]
    print("\n".join(notes), flush=True)
    if not plan:
        print("[done] 无需回填（所有候选合约已在库内）")
        return 0
    fallbacks = product_fallbacks(bars.assign(product=bars["symbol"].map(product_of)))

    failures, fallback_used, total_new = [], 0, 0
    for offset in range(0, len(plan), args.session_size):
        chunk = plan[offset:offset + args.session_size]
        print(f"[chunk] {offset + 1}~{offset + len(chunk)} / {len(plan)}", flush=True)
        frames = []
        api = TqApi(auth=read_auth(), disable_print=True)
        try:
            fetcher = DataFetcher(api, FetchConfig(data_dir=data_dir, retries=args.retries,
                                                   backoff_s=args.backoff,
                                                   pause_s=args.pause),
                                  secrets=[secret()])
            for index, row in enumerate(chunk, start=offset + 1):
                symbol, product = row["symbol"], row["product"]
                frame = fetcher.fetch_symbol(symbol, start, end)
                if frame is None:
                    print(f"[{index}/{len(plan)}] {symbol}: 无数据（已记失败清单）", flush=True)
                    continue
                frame = frame[frame["trade_date"] < row["limit_date"]]
                if frame.empty:
                    print(f"[{index}/{len(plan)}] {symbol}: limit_date 之后无行", flush=True)
                    continue
                values = {}
                for column in ("volume_multiple", "price_tick"):
                    values[column] = float(fallbacks.get(product, {}).get(column, float("nan")))
                    fallback_used += 1
                frames.append(frame.assign(limit_date=row["limit_date"],
                                           volume_multiple=values["volume_multiple"],
                                           price_tick=values["price_tick"],
                                           margin_rate=args.margin_rate)[BAR_COLUMNS])
                print(f"[{index}/{len(plan)}] {symbol}: bars={len(frame)} "
                      f"{frame['trade_date'].min().date()}~{frame['trade_date'].max().date()}",
                      flush=True)
            failures.extend(fetcher.failures)
        finally:
            api.close()

        if frames:
            new = pd.concat(frames, ignore_index=True)
            total_new += len(new)
            bars = (pd.concat([bars, new], ignore_index=True)
                    .drop_duplicates(["symbol", "trade_date"], keep="last")
                    .sort_values(["symbol", "trade_date"]))
            bars.to_parquet(bars_path, index=False)
            print(f"[merged] 本块新增 {len(new)} 行；累计 {total_new} 行；"
                  f"rows={len(bars)} symbols={bars['symbol'].nunique()}", flush=True)

    if failures:
        failure_path = data_dir / "fetch_failures_r2.parquet"
        frame = pd.DataFrame(failures)
        if failure_path.exists():                     # 多轮分品种回填时保留既有失败记录
            old = pd.read_parquet(failure_path)
            frame = (pd.concat([old, frame], ignore_index=True)
                     .drop_duplicates("symbol", keep="last"))
        frame.to_parquet(failure_path, index=False)
        print(f"[warn] 失败 {len(failures)} 个合约（累计 {len(frame)}），明细见 {failure_path}",
              flush=True)
        for item in failures[:10]:
            print(f"[fail] {item['symbol']}: {item['error']}", flush=True)

    after = bars.assign(product=bars["symbol"].map(product_of))
    earliest = after.groupby("product")["trade_date"].min().sort_index()
    print(f"[done] 累计新增 {total_new} 行；元数据用品种众数回填 {fallback_used} 处；"
          f"失败 {len(failures)} 个", flush=True)
    for product, day in earliest.items():
        print(f"[earliest] {product}: {day.date()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
