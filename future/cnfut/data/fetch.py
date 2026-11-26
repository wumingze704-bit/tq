"""§8.1 数据拉取与本地缓存。

设计要点（含评审修正）：
* 单一 `TqApi` 实例、串行请求、单次超时与重试（§8.1）。
* 凭据只从环境变量读取，异常文本中的账号密码替换为 `***`。
* 日线入库前走 `quality.clean_daily`（过滤占位行、标记无成交日，R-23）。
* [R-25] 合约是否可用由"能否取到日线"决定，而不是由 `query_quotes` 列表决定。
* 缓存为 parquet，已存在且覆盖请求区间时直接复用，避免重复下载与限频。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

import pandas as pd

from .quality import clean_daily

_DIGITS = re.compile(r"\d+$")


def product_of(symbol: str) -> str:
    code = symbol.split(".", 1)[1] if "." in symbol else symbol
    return _DIGITS.sub("", code)


@dataclass
class FetchConfig:
    data_dir: Path
    retries: int = 3
    backoff_s: float = 5.0
    pause_s: float = 1.0
    data_length: int = 10000


class DataFetcher:
    """串行拉取 + parquet 缓存。所有 TqSdk 调用都必须经过这里（§8.1）。"""

    def __init__(self, api, config: FetchConfig, secrets: Sequence[str] = ()) -> None:
        self._api = api
        self._cfg = config
        self._secrets = [s for s in secrets if s]
        self.failures: List[dict] = []

    # ------------------------------------------------------------------ 工具
    def mask(self, value) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _bars_path(self, symbol: str) -> Path:
        exchange = symbol.split(".", 1)[0]
        product = product_of(symbol)
        return self._cfg.data_dir / "bars" / exchange / product / f"{symbol}.parquet"

    # ------------------------------------------------------------------ 拉取
    def kline_daily(self, symbol: str) -> Optional[pd.DataFrame]:
        """取单个合约日线；重试耗尽返回 None 并记录失败原因（不静默）。"""
        last_error = ""
        for attempt in range(1, self._cfg.retries + 1):
            try:
                raw = self._api.get_kline_serial(symbol, 86400,
                                                 data_length=self._cfg.data_length)
                frame = clean_daily(raw)
                if frame.empty or not frame["has_trade"].any():
                    raise ValueError("日线为空或无成交")
                return frame
            except Exception as exc:                       # noqa: BLE001 - 需要按合约记录
                last_error = f"{type(exc).__name__}: {self.mask(exc)[:160]}"
                if attempt < self._cfg.retries:
                    time.sleep(self._cfg.backoff_s * attempt)
        self.failures.append({"symbol": symbol, "error": last_error})
        return None

    def load_cached(self, symbol: str, start: pd.Timestamp,
                    end: pd.Timestamp) -> Optional[pd.DataFrame]:
        path = self._bars_path(symbol)
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        if frame.empty:
            return None
        if frame["trade_date"].min() > start or frame["trade_date"].max() < end:
            return None
        return frame

    def fetch_symbol(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
                     refresh: bool = False) -> Optional[pd.DataFrame]:
        """带缓存地取一个合约，并裁到 [start, end]。"""
        if not refresh:
            cached = self.load_cached(symbol, start, end)
            if cached is not None:
                return cached
        frame = self.kline_daily(symbol)
        if frame is None:
            return None
        frame = frame[(frame["trade_date"] >= start) & (frame["trade_date"] <= end)]
        if frame.empty:
            return None
        frame["symbol"] = symbol
        path = self._bars_path(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        time.sleep(self._cfg.pause_s)                      # 串行 + 限速，避免触发风控
        return frame


def contract_meta(api, symbols: Iterable[str], secrets: Sequence[str] = ()) -> pd.DataFrame:
    """合约主数据（§8.2）：分批查询，避免单次请求过大。"""
    rows = []
    batch: List[str] = []
    masked = [s for s in secrets if s]

    def flush() -> None:
        if not batch:
            return
        try:
            frame = api.query_symbol_info(list(batch))
        except Exception:                                  # noqa: BLE001 - 单个失效合约会整批失败
            for symbol in list(batch):
                try:
                    frame = api.query_symbol_info([symbol])
                    rows.extend(frame.to_dict("records"))
                except Exception:
                    rows.append({"instrument_id": symbol, "missing": True})
            batch.clear()
            return
        rows.extend(frame.to_dict("records"))
        batch.clear()

    for symbol in symbols:
        batch.append(symbol)
        if len(batch) >= 40:
            flush()
    flush()
    frame = pd.DataFrame(rows)
    if "instrument_id" in frame.columns:
        frame = frame.drop_duplicates("instrument_id")
    return frame
