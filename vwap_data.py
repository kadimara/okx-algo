"""
Candle data for the MS VWAP strategy.

Signal candles (what the trend, structure and VWAP are calculated on):
  "index" -> OKX index price + volume aggregated across exchanges (aggregate_volume.py)
  "perp"  -> OKX perpetual price + its own volume (matches TradingView's
             OKX:BTCUSDT.P chart, useful to compare with the Pine strategy)
Trade candles: always the OKX perpetual (BTC/USDT:USDT), where orders are filled.
"""
from datetime import date
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd

from aggregate_volume import build_aggregated
from market_data import fetch_candles

SIGNAL_KINDS = ("index", "perp")


def perp_symbol(base: str) -> str:
    return f"{base}/USDT:USDT"


def fetch_perp(base: str, timeframe: str, since_ms: int, verbose: bool = False) -> pd.DataFrame:
    okx = ccxt.okx({"enableRateLimit": True})
    return fetch_candles(okx, perp_symbol(base), timeframe, since_ms, verbose=verbose)


def fetch_signal(kind: str, base: str, timeframe: str, since_ms: int,
                 exchanges: Optional[str] = None, verbose: bool = False) -> pd.DataFrame:
    if kind == "perp":
        return fetch_perp(base, timeframe, since_ms, verbose)
    if kind == "index":
        df, report = build_aggregated(base, timeframe, since_ms=since_ms, exchanges=exchanges,
                                      price="okx-index", verbose=verbose)
        if report["skipped"]:
            print(f"  note: skipped exchanges: {', '.join(report['skipped'])}")
        return df[["open", "high", "low", "close", "volume"]]
    raise ValueError(f"signal data must be one of {SIGNAL_KINDS}")


def cached(name: str, loader, refresh: bool = False) -> pd.DataFrame:
    """Load from data/<name>_<today>.csv if present, otherwise download and save."""
    Path("data").mkdir(exist_ok=True)
    path = Path("data") / f"{name}_{date.today()}.csv"
    if path.exists() and not refresh:
        print(f"Using cached data: {path}")
        return pd.read_csv(path, index_col=0, parse_dates=True)
    df = loader()
    if df.empty:
        raise SystemExit(f"No data returned for {name}.")
    df.to_csv(path)
    return df


def extend(df: pd.DataFrame, fresh: pd.DataFrame, keep: Optional[int] = None) -> pd.DataFrame:
    """Merge newly downloaded candles into an existing set (new values win)."""
    merged = pd.concat([df[~df.index.isin(fresh.index)], fresh]).sort_index()
    return merged.tail(keep) if keep else merged
