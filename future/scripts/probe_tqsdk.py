#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""future.md §23「待实测清单」的只读探测脚本。

只读约束：
* 不写 data/、artifacts/，不下单，不创建实盘账户（仅用 TqSim 探测回测可用性）。
* 凭据只从环境变量读取：TQ_AUTH_USER / TQ_AUTH_PASSWORD（或 TQ_AUTH="user,password"）。
* 输出与异常信息中的账号、密码一律替换为 ***，不落盘、不打印。

用法::

    set TQ_AUTH_USER=xxx
    set TQ_AUTH_PASSWORD=xxx
    python future/scripts/probe_tqsdk.py                # 运行全部探测
    python future/scripts/probe_tqsdk.py --only kline   # 只运行部分
    python future/scripts/probe_tqsdk.py --list         # 列出探测项（不需要凭据）

退出码：0 全部成功；2 缺少凭据；3 已运行但存在失败探测。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date

PROBES = {}
_SECRETS = []

# 已知的历史合约，用于判断已下市合约覆盖范围
HISTORICAL_SYMBOLS = [
    "SHFE.cu1501", "SHFE.rb1601", "SHFE.ru1505",
    "DCE.a1401", "DCE.m1601", "DCE.p1501",
    "CZCE.TA601", "CZCE.MA506", "CZCE.SR601",
]
FUTURE_RE = re.compile(r"^(SHFE|DCE|CZCE|INE|GFEX|CFFEX)\.([A-Za-z]{1,2})(\d{3,4})$")


def probe(name):
    """注册一个探测项。"""

    def register(fn):
        PROBES[name] = fn
        return fn

    return register


def mask(value):
    """把凭据从任意文本中抹掉。"""
    text = str(value)
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "***")
    return text


def to_text(ns):
    """纳秒时间戳 -> 北京时间字符串（TqSdk 的 datetime 列为北京时间）。"""
    from tqsdk import tafunc

    return tafunc.time_to_datetime(int(ns)).strftime("%Y-%m-%d %H:%M:%S")


def pick_active(symbols, product_id):
    """在市合约里挑交割月最近的一个，避免硬编码已到期的合约代码。"""
    candidates = [s for s in symbols
                  if s.split(".")[1].startswith(product_id)
                  and s.split(".")[1][len(product_id):].isdigit()]
    return sorted(candidates)[0] if candidates else None


@probe("calendar")
def probe_calendar(ctx):
    """§7.2 交易日历：可用范围与返回字段。"""
    api = ctx["api"]
    out = {}
    df = api.get_trading_calendar(date(2012, 1, 1), date(2012, 3, 31))
    out["2012Q1_rows"] = int(len(df))
    out["2012Q1_trading_days"] = int(df["trading"].sum())
    for label, start, end in (
        ("try_2005-01", date(2005, 1, 1), date(2005, 1, 31)),
        ("try_2028-12", date(2028, 12, 1), date(2028, 12, 31)),
    ):
        try:
            sub = api.get_trading_calendar(start, end)
            out[label] = {"ok": True, "rows": int(len(sub))}
        except Exception as exc:  # 异常文本里带支持范围
            out[label] = {"ok": False, "error": mask(exc)[:300]}
    return out


@probe("symbols")
def probe_symbols(ctx):
    """§8.2 合约覆盖：在市/已下市合约数量与历史合约是否存在。"""
    api = ctx["api"]
    expired = list(api.query_quotes(ins_class="FUTURE", expired=True))
    active = list(api.query_quotes(ins_class="FUTURE", expired=False))
    expired_set = set(expired)
    by_exchange = {}
    for sym in expired:
        match = FUTURE_RE.match(sym)
        if match:
            by_exchange[match.group(1)] = by_exchange.get(match.group(1), 0) + 1
    ctx["active_symbols"] = active
    return {
        "expired_count": len(expired),
        "active_count": len(active),
        "expired_by_exchange": by_exchange,
        "historical_symbols_present": {s: (s in expired_set) for s in HISTORICAL_SYMBOLS},
    }


@probe("symbol_info")
def probe_symbol_info(ctx):
    """§8.2 合约主数据：交付月、到期日、乘数、最小变动价位、夜盘时段是否可得。"""
    api = ctx["api"]
    expired = list(api.query_quotes(ins_class="FUTURE", expired=True))
    expired_set = set(expired)
    samples = [s for s in HISTORICAL_SYMBOLS if s in expired_set][:2]
    active = ctx.get("active_symbols") or list(
        api.query_quotes(ins_class="FUTURE", expired=False))
    sample_active = pick_active(active, "m") or (active[0] if active else None)
    if sample_active:
        samples.append(sample_active)
    if not samples:
        return {"ok": False, "error": "没有可取样的合约"}
    df = api.query_symbol_info(samples)
    wanted = ["instrument_id", "exchange_id", "product_id", "expired",
              "delivery_year", "delivery_month", "expire_datetime",
              "volume_multiple", "price_tick", "trading_time_night", "trading_time_day"]
    rows = {}
    for _, row in df.iterrows():
        item = {}
        for col in wanted:
            if col not in df.columns:
                continue
            value = row[col]
            if col.startswith("trading_time"):
                item[col] = type(value).__name__
            else:
                item[col] = None if value != value else str(value)
        rows[str(row["instrument_id"])] = item
    return {"columns_available": [c for c in wanted if c in df.columns],
            "missing_columns": [c for c in wanted if c not in df.columns],
            "rows": rows}


@probe("kline_expired")
def probe_kline_expired(ctx):
    """§8.1/§15.1 免费账户能否取已下市合约日线，以及最早可得的日期。"""
    api = ctx["api"]
    expired = set(api.query_quotes(ins_class="FUTURE", expired=True))
    out = {}
    for sym in [s for s in HISTORICAL_SYMBOLS if s in expired][:3]:
        try:
            df = api.get_kline_serial(sym, 86400, data_length=300)
            out[sym] = {
                "rows": int(len(df)),
                "first_datetime": to_text(df["datetime"].iloc[0]),
                "last_datetime": to_text(df["datetime"].iloc[-1]),
                "last_close": float(df["close"].iloc[-1]),
                "nan_close_rows": int(df["close"].isna().sum()),
                "nan_close_oi_rows": int(df["close_oi"].isna().sum()),
                "zero_volume_rows": int((df["volume"] == 0).sum()),
            }
        except Exception as exc:
            out[sym] = {"ok": False, "error": mask(exc)[:300]}
    return out


@probe("kline_active")
def probe_kline_active(ctx):
    """§7.1 日线 datetime 与交易日的对应关系；§8.7 无成交日的返回方式。"""
    api = ctx["api"]
    active = ctx.get("active_symbols") or list(
        api.query_quotes(ins_class="FUTURE", expired=False))
    sym = pick_active(active, "m")
    if not sym:
        return {"ok": False, "error": "找不到在市合约"}
    df = api.get_kline_serial(sym, 86400, data_length=20)
    tail = df.tail(5)
    bars = []
    for _, row in tail.iterrows():
        text = to_text(row["datetime"])
        bars.append({"datetime": text,
                     "hour": text[11:13],
                     "volume": None if row["volume"] != row["volume"] else float(row["volume"]),
                     "close": None if row["close"] != row["close"] else float(row["close"])})
    return {
        "symbol": sym,
        "bars": bars,
        "datetime_hour_samples": sorted({b["hour"] for b in bars}),
        "zero_volume_rows": int((df["volume"] == 0).sum()),
        "nan_close_rows": int(df["close"].isna().sum()),
    }


@probe("kline_cap")
def probe_kline_cap(ctx):
    """§8.1 单序列长度上限（文档写 8000，源码 api.py:755 截断到 10000）。"""
    api = ctx["api"]
    active = ctx.get("active_symbols") or list(
        api.query_quotes(ins_class="FUTURE", expired=False))
    sym = pick_active(active, "m")
    if not sym:
        return {"ok": False, "error": "找不到在市合约"}
    df = api.get_kline_serial(sym, 86400, data_length=20000)
    return {"symbol": sym, "requested": 20000, "returned_rows": int(len(df))}


@probe("his_cont")
def probe_his_cont(ctx):
    """§8.4 第 6 条：query_his_cont_quotes 的最大 n 与最早日期。"""
    api = ctx["api"]
    out = {}
    for n in (200, 500, 1000, 2000):
        try:
            df = api.query_his_cont_quotes("KQ.m@SHFE.rb", n)
            out[str(n)] = {
                "ok": True,
                "rows": int(len(df)),
                "first_date": str(df["date"].iloc[0])[:10],
                "last_date": str(df["date"].iloc[-1])[:10],
            }
        except Exception as exc:
            out[str(n)] = {"ok": False, "error": mask(exc)[:300]}
    return out


@probe("quote_rates")
def probe_quote_rates(ctx):
    """§8.6 当前保证金率、手续费、乘数、最小变动价位快照（只代表当前值）。"""
    api = ctx["api"]
    active = ctx.get("active_symbols") or list(
        api.query_quotes(ins_class="FUTURE", expired=False))
    out = {}
    for product in ("m", "rb", "TA", "MA", "c"):
        sym = pick_active(active, product)
        if not sym:
            continue
        quote = api.get_quote(sym)
        item = {}
        for field in ("volume_multiple", "price_tick", "margin", "commission",
                      "upper_limit", "lower_limit", "pre_settlement"):
            value = getattr(quote, field, None)
            try:
                item[field] = None if value is None or value != value else float(value)
            except (TypeError, ValueError):
                item[field] = mask(value)[:120]
        out[sym] = item
    return out


@probe("coverage")
def probe_coverage(ctx):
    """§15.1 关键：已下市合约的年份覆盖，以及最早合约的日线能否取到。"""
    api = ctx["api"]
    expired = list(api.query_quotes(ins_class="FUTURE", expired=True))
    by_exchange = {}
    for sym in expired:
        by_exchange.setdefault(sym.split(".", 1)[0], []).append(sym)

    out = {}
    for exchange, symbols in sorted(by_exchange.items()):
        step = max(1, len(symbols) // 40)
        sample = symbols[::step][:40]
        df = api.query_symbol_info(sample)
        years = pd_to_numeric(df["delivery_year"])
        valid = years.dropna()
        if valid.empty:
            out[exchange] = {"sampled": len(sample), "note": "无 delivery_year"}
            continue
        oldest_row = df.loc[valid.idxmin()]
        item = {
            "expired_total": len(symbols),
            "sampled": len(sample),
            "min_delivery_year": int(valid.min()),
            "max_delivery_year": int(valid.max()),
            "oldest_symbol": str(oldest_row["instrument_id"]),
            "oldest_expire_datetime": str(oldest_row["expire_datetime"]),
        }
        try:
            frame = api.get_kline_serial(item["oldest_symbol"], 86400, data_length=300)
            item["oldest_kline"] = {
                "rows": int(len(frame)),
                "first": to_text(frame["datetime"].iloc[0]),
                "last": to_text(frame["datetime"].iloc[-1]),
            }
        except Exception as exc:
            item["oldest_kline"] = {"ok": False, "error": mask(exc)[:200]}
        out[exchange] = item
    return out


def pd_to_numeric(series):
    """把 delivery_year 之类可能为字符串的列转成数值（缺失保留 NaN）。"""
    import pandas as pd

    return pd.to_numeric(series, errors="coerce")


@probe("backtest")
def probe_backtest(ctx):
    """§14.9：TqBacktest 在免费账户下的可用性与限制（独立 TqApi 实例）。"""
    from tqsdk import BacktestFinished, TqApi, TqAuth, TqBacktest, TqSim

    api = None
    klines = None
    balance = None
    symbol = "SHFE.rb2405"
    try:
        sim = TqSim(init_balance=20000)
        api = TqApi(sim, auth=TqAuth(ctx["user"], ctx["password"]),
                    backtest=TqBacktest(date(2024, 1, 2), date(2024, 1, 15)),
                    disable_print=True)
        klines = api.get_kline_serial(symbol, 86400, data_length=50)
        account = api.get_account()
        while True:
            api.wait_update()
            balance = float(account.balance)
    except BacktestFinished:
        return {"ok": True, "symbol": symbol,
                "bars": None if klines is None else int(len(klines)),
                "final_balance": balance}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "message": mask(exc)[:300]}
    finally:
        if api is not None:
            api.close()
    return {"ok": True, "symbol": symbol, "bars": None if klines is None else int(len(klines)),
            "final_balance": balance}


def read_credentials():
    """从环境变量读取快期账户，不接受命令行参数（避免凭据进入历史记录）。"""
    user = os.environ.get("TQ_AUTH_USER", "").strip()
    password = os.environ.get("TQ_AUTH_PASSWORD", "").strip()
    if not (user and password):
        combo = os.environ.get("TQ_AUTH", "")
        if "," in combo:
            user, password = [part.strip() for part in combo.split(",", 1)]
    return user, password


def main(argv=None):
    parser = argparse.ArgumentParser(description="TqSdk future.md §23 只读探测")
    parser.add_argument("--only", default="", help="逗号分隔的探测项，默认全部")
    parser.add_argument("--list", action="store_true", help="列出探测项")
    parser.add_argument("--out", default="", help="把 JSON 结果写入该文件（UTF-8）")
    args = parser.parse_args(argv)

    if args.list:
        print(json.dumps(list(PROBES), ensure_ascii=False, indent=2))
        return 0

    user, password = read_credentials()
    if not (user and password):
        print(json.dumps({
            "status": "auth_missing",
            "message": "未找到 TQ_AUTH_USER / TQ_AUTH_PASSWORD。TqApi 缺少快期账户会直接报错"
                       "（'请输入 auth （快期账户）参数'），无法读取行情、日线与交易日历。",
        }, ensure_ascii=False, indent=2))
        return 2

    _SECRETS.extend([user, password])

    import tqsdk
    from tqsdk import TqApi, TqAuth

    names = [name for name in PROBES
             if not args.only or name in [x.strip() for x in args.only.split(",")]]
    meta = {
        "python": sys.version.split()[0],
        "tqsdk_version": tqsdk.__version__,
        "tqsdk_module": tqsdk.__file__,
        "probes": names,
    }
    ctx = {"user": user, "password": password}
    results = {}
    api = None
    try:
        api = TqApi(auth=TqAuth(user, password), disable_print=True)
        ctx["api"] = api
        for name in names:
            if name == "backtest":
                continue
            try:
                out = PROBES[name](ctx)
                if isinstance(out, dict) and out.get("ok") is False:
                    results[name] = out
                else:
                    results[name] = {"ok": True, "data": out}
            except Exception as exc:
                results[name] = {"ok": False, "error": type(exc).__name__,
                                 "message": mask(exc)[:400]}
    except Exception as exc:
        results["connect"] = {"ok": False, "error": type(exc).__name__,
                              "message": mask(exc)[:400]}
    finally:
        if api is not None:
            api.close()

    if "backtest" in names:
        try:
            results["backtest"] = probe_backtest(ctx)
        except Exception as exc:
            results["backtest"] = {"ok": False, "error": type(exc).__name__,
                                   "message": mask(exc)[:400]}

    report = {"meta": meta, "results": results}
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(payload)
    print(payload)
    return 0 if all(item.get("ok") for item in results.values()) else 3


if __name__ == "__main__":
    raise SystemExit(main())
