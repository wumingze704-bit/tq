"""§8.7 数据体检：把「数据是否可复现」固化成脚本（00 一次性准备 / R2 回填后共用）。

检查口径来自 `docs/research/SETUP_DONE.md` §3 的实测结论，检查项对应
`plan/00-一次性准备-最先发这个.txt` §2.4：

1. `symbol + trade_date` 唯一（同一合约同一交易日只能有一行）；
2. 不存在 `datetime == 0` 的占位行（落地到 `trade_date` 后是 1970-01-01，
   已被 `clean_daily` 过滤，此处复核没有被重新引入）；
3. 有成交行（`volume > 0`）的 `close` 非空且 > 0；
4. 无成交行（`volume` 为空或 0）的 `close` 必须为空——实测口径是"行保留、价格置空"，
   若这里出现非空收盘，说明有人用 ffill 覆盖了原始数据，收益会被污染。

另外输出报告用统计：行数、品种数、合约数、起止交易日、按品种最早交易日（判断哪些品种还需回填）。

用法::

    python future/scripts/data_health.py
    python future/scripts/data_health.py --data data/bars_all.parquet --out artifacts/data_health.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cnfut.data.rolling_contract import product_of                     # noqa: E402

REQUIRED = ("symbol", "trade_date", "close", "volume")
PLACEHOLDER_DATE = pd.Timestamp("1970-01-02")   # datetime == 0 落地后的日期
PRICE_COLUMNS = ("open", "high", "low", "close")


def load_bars(path: Path) -> pd.DataFrame:
    """读 bars parquet 并补 `product` 列（`DCE.m2401` -> `m`，`CZCE.TA601` -> `TA`）。"""
    bars = pd.read_parquet(path)
    missing = [column for column in REQUIRED if column not in bars.columns]
    if missing:
        raise ValueError(f"bars 缺少列: {missing}")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"])
    bars["product"] = bars["symbol"].map(product_of)
    return bars


def audit(bars: pd.DataFrame) -> Dict[str, Any]:
    """逐条执行 §2.4 的检查，返回 {checks, stats, earliest_by_product}。"""
    missing = [column for column in REQUIRED if column not in bars.columns]
    if missing:
        raise ValueError(f"bars 缺少列: {missing}")

    frame = bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    if "product" not in frame.columns:
        frame["product"] = frame["symbol"].map(product_of)
    traded = frame["volume"].fillna(0.0) > 0

    duplicate_rows = int(frame.duplicated(["symbol", "trade_date"]).sum())

    placeholder_rows = int((frame["trade_date"] <= PLACEHOLDER_DATE).sum())
    if "datetime" in frame.columns:
        placeholder_rows += int((frame["datetime"] <= 0).sum())

    traded_blank = int((traded & frame["close"].isna()).sum())
    traded_nonpositive = int((traded & (frame["close"] <= 0)).sum())

    idle_filled = int((~traded & frame["close"].notna()).sum())

    checks: List[Dict[str, Any]] = [
        {"id": "C1", "name": "symbol+trade_date 唯一", "ok": duplicate_rows == 0,
         "detail": f"重复行 {duplicate_rows}"},
        {"id": "C2", "name": "无 datetime=0 占位行", "ok": placeholder_rows == 0,
         "detail": f"占位行 {placeholder_rows}"},
        {"id": "C3", "name": "有成交行 close 非空且 >0",
         "ok": traded_blank == 0 and traded_nonpositive == 0,
         "detail": f"close 为空 {traded_blank}、close<=0 {traded_nonpositive}"},
        {"id": "C4", "name": "无成交行 close 为空（未被前向填充）", "ok": idle_filled == 0,
         "detail": f"无成交行却有 close 的 {idle_filled}"},
    ]

    first_by_product = frame.groupby("product")["trade_date"].min().sort_index()
    symbols_by_product = frame.groupby("product")["symbol"].nunique().sort_index()
    stats = {
        "rows": int(len(frame)),
        "products": int(frame["product"].nunique()),
        "symbols": int(frame["symbol"].nunique()),
        "sessions": int(frame["trade_date"].nunique()),
        "start": str(frame["trade_date"].min().date()),
        "end": str(frame["trade_date"].max().date()),
        "traded_rows": int(traded.sum()),
        "idle_rows": int((~traded).sum()),
    }
    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "stats": stats,
        "earliest_by_product": {product: str(day.date())
                                for product, day in first_by_product.items()},
        "symbols_by_product": {product: int(count)
                               for product, count in symbols_by_product.items()},
    }


def format_report(result: Dict[str, Any], source: str = "") -> str:
    """把 audit 结果排版成中文报告（markdown 片段，可直接贴进文档）。"""
    stats = result["stats"]
    lines = [f"数据源：{source}" if source else "数据源：",
             "",
             f"- 行数 {stats['rows']}；品种 {stats['products']}；合约 {stats['symbols']}；"
             f"交易日 {stats['sessions']}",
             f"- 区间 {stats['start']} ~ {stats['end']}"
             f"（有成交 {stats['traded_rows']} 行 / 无成交 {stats['idle_rows']} 行）",
             "",
             "| 检查 | 结论 | 明细 |",
             "| --- | --- | --- |"]
    for check in result["checks"]:
        lines.append(f"| {check['id']} {check['name']} | "
                     f"{'通过' if check['ok'] else '**不通过**'} | {check['detail']} |")
    lines.extend(["", "| 品种 | 合约数 | 最早交易日 |", "| --- | --- | --- |"])
    for product, day in result["earliest_by_product"].items():
        lines.append(f"| {product} | {result['symbols_by_product'][product]} | {day} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="§8.7 数据体检（bars_all.parquet）")
    parser.add_argument("--data", default="data/bars_all.parquet")
    parser.add_argument("--out", default="", help="把体检结果写成 JSON（便于与轮次文档对账）")
    args = parser.parse_args(argv)

    bars = load_bars(Path(args.data))
    result = audit(bars)
    result["source"] = args.data
    report = format_report(result, args.data)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
