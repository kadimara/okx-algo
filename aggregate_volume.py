#!/usr/bin/env python3
"""
aggregate_volume.py - candles with volume aggregated across several exchanges.

Downloads the same coin's candles from multiple exchanges (public data, no API
keys), adds up their volume per candle, and pairs it with a price:

  --price okx-index   OKX index price (default; matches the trading bot)
  --price composite   volume-weighted average price across the exchanges

Volume is in the base coin (e.g. BTC) for every exchange, so it can be summed.
The candle that is still forming is never included.

Examples:
  python aggregate_volume.py --check                     test which exchanges work for you
  python aggregate_volume.py                             BTC, 1h, 30 days, default exchanges
  python aggregate_volume.py --timeframe 4h --days 365
  python aggregate_volume.py --exchanges binance,coinbase,bybit,coinbase:BTC/USDC
  python aggregate_volume.py --base ETH --price composite

Use from other code:
  from aggregate_volume import build_aggregated
  df, report = build_aggregated("BTC", "1h", since_ms)
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import ccxt
import pandas as pd

# Main pair per exchange ({base} is replaced by the coin). Large USD/USDT books.
DEFAULT_EXCHANGES = {
    "binance": "{base}/USDT",
    "bybit": "{base}/USDT",
    "okx": "{base}/USDT",
    "coinbase": "{base}/USD",
    "bitstamp": "{base}/USD",
    "bitfinex": "{base}/USD",
}
QUOTE_FALLBACK = ["USDT", "USD", "USDC"]
# Candles per request each exchange allows (smaller = more requests).
PAGE_LIMITS = {"binance": 1000, "bybit": 1000, "coinbase": 300, "okx": 100,
               "bitstamp": 1000, "bitfinex": 1000, "kraken": 720}
DEFAULT_PAGE = 200
OHLCV = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------- helpers
def tf_ms(timeframe: str) -> int:
    return ccxt.Exchange.parse_timeframe(timeframe) * 1000


def parse_exchange_list(text: Optional[str], base: str) -> List[Tuple[str, Optional[str]]]:
    """'binance,coinbase:BTC/USDC' -> [('binance', None), ('coinbase', 'BTC/USDC')]"""
    if not text:
        return [(name, None) for name in DEFAULT_EXCHANGES]
    specs = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, symbol = item.partition(":")
        specs.append((name.strip().lower(), symbol.strip() or None))
    return specs


def make_exchange(name: str):
    if not hasattr(ccxt, name):
        raise ValueError(f"ccxt has no exchange called '{name}'")
    return getattr(ccxt, name)({"enableRateLimit": True, "timeout": 20000})


def pick_symbol(ex, name: str, base: str, override: Optional[str]) -> str:
    if override:
        return override
    ex.load_markets()
    candidates = []
    if name in DEFAULT_EXCHANGES:
        candidates.append(DEFAULT_EXCHANGES[name].format(base=base))
    candidates += [f"{base}/{q}" for q in QUOTE_FALLBACK]
    for symbol in candidates:
        if symbol in ex.markets:
            return symbol
    raise ValueError(f"no {base} market with quote {QUOTE_FALLBACK}")


def fetch_timeframe_for(ex, timeframe: str) -> str:
    """The timeframe to download: the requested one, or a smaller one that we
    combine into it (e.g. Coinbase has no 4h candles, so we build them from 1h)."""
    supported = ex.timeframes or {}
    if not supported or timeframe in supported:
        return timeframe
    target = tf_ms(timeframe)
    options = [tf for tf in supported if target % tf_ms(tf) == 0]
    if not options:
        raise ValueError(f"does not support {timeframe} or any timeframe that divides it")
    return max(options, key=tf_ms)


def resample(df: pd.DataFrame, timeframe: str, source_tf: str) -> pd.DataFrame:
    """Combine smaller candles into bigger ones. Incomplete buckets are dropped."""
    if timeframe == source_tf or df.empty:
        return df
    per_bucket = tf_ms(timeframe) // tf_ms(source_tf)
    rule = f"{tf_ms(timeframe)}ms"
    agg = df.resample(rule, origin="epoch", label="left", closed="left")
    out = agg.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = agg["close"].count()
    return out[counts == per_bucket]


def to_frame(rows: list) -> pd.DataFrame:
    df = pd.DataFrame([r[:6] for r in rows], columns=["time"] + OHLCV)
    if df.empty:
        return pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    df = df.drop_duplicates("time").sort_values("time")
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df.set_index("time").apply(pd.to_numeric, errors="coerce").astype(float)


# ---------------------------------------------------------------- downloads
def fetch_exchange(name: str, override: Optional[str], base: str, timeframe: str,
                   since: int, until: int) -> Tuple[str, pd.DataFrame]:
    """All closed candles for one exchange between since and until (ms)."""
    ex = make_exchange(name)
    symbol = pick_symbol(ex, name, base, override)
    source_tf = fetch_timeframe_for(ex, timeframe)
    bar = tf_ms(source_tf)
    limit = PAGE_LIMITS.get(name, DEFAULT_PAGE)

    rows, cursor = [], since
    while cursor < until:
        batch = ex.fetch_ohlcv(symbol, source_tf, since=cursor, limit=limit) or []
        batch = [b for b in batch if cursor <= b[0] < until]  # some exchanges ignore `since`
        if not batch:
            cursor += bar * limit  # no data here (not listed yet / outage): skip ahead
            continue
        rows.extend(batch)
        cursor = max(b[0] for b in batch) + bar

    df = resample(to_frame(rows), timeframe, source_tf)
    return symbol, df


def fetch_okx_index(base: str, timeframe: str, since: int, until: int,
                    quote: str = "USDT") -> pd.DataFrame:
    """OKX index candles (e.g. BTC-USDT), called directly because ccxt's built-in
    index route sends an empty instrument id for spot pairs."""
    okx = make_exchange("okx")
    seconds = tf_ms(timeframe) // 1000
    bar_ms = seconds * 1000
    bar = okx.timeframes.get(timeframe, timeframe)
    if seconds >= 6 * 3600:
        bar += "utc"
    rows, cursor = [], since
    while cursor < until:
        request = {"instId": f"{base}-{quote}", "bar": bar, "limit": 100,
                   "before": max(cursor - 1, 0), "after": cursor + bar_ms * 100}
        if cursor < okx.milliseconds() - 1439 * bar_ms:
            data = okx.publicGetMarketHistoryIndexCandles(request).get("data", [])
        else:
            data = okx.publicGetMarketIndexCandles(request).get("data", [])
        batch = sorted(([int(r[0]), *map(float, r[1:5]), 0.0] for r in data), key=lambda r: r[0])
        batch = [b for b in batch if cursor <= b[0] < until]
        if not batch:
            cursor += bar_ms * 100
            continue
        rows.extend(batch)
        cursor = batch[-1][0] + bar_ms
    return to_frame(rows).drop(columns="volume")


# ---------------------------------------------------------------- main builder
def build_aggregated(base: str = "BTC", timeframe: str = "1h", since_ms: Optional[int] = None,
                     until_ms: Optional[int] = None, exchanges: Optional[str] = None,
                     price: str = "okx-index", verbose: bool = True,
                     days: Optional[int] = None) -> Tuple[pd.DataFrame, dict]:
    """
    Returns (candles, report).
    candles columns: open high low close volume n_exchanges vol_<exchange>...
    report: per-exchange coverage and volume share, plus skipped exchanges.
    """
    say = print if verbose else (lambda *a, **k: None)
    bar = tf_ms(timeframe)
    now = ccxt.Exchange.milliseconds()
    until = min(until_ms or now, now) // bar * bar          # only closed candles
    if since_ms is None:
        since_ms = until - (days or 30) * 86_400_000
    since = since_ms // bar * bar

    specs = parse_exchange_list(exchanges, base)
    volumes: Dict[str, pd.DataFrame] = {}
    symbols: Dict[str, str] = {}
    skipped: Dict[str, str] = {}

    say(f"Downloading {base} {timeframe} candles from {len(specs)} exchanges...")
    with ThreadPoolExecutor(max_workers=len(specs) or 1) as pool:
        futures = {pool.submit(fetch_exchange, n, o, base, timeframe, since, until): (n, o)
                   for n, o in specs}
        for fut in as_completed(futures):
            name, override = futures[fut]
            label = f"{name}:{override}" if override else name
            try:
                symbol, df = fut.result()
            except Exception as e:  # blocked region, unknown pair, network...
                skipped[label] = f"{type(e).__name__}: {str(e)[:120]}"
                say(f"  SKIPPED {label:<22} {skipped[label]}")
                continue
            if df.empty:
                skipped[label] = "no candles returned"
                say(f"  SKIPPED {label:<22} no candles returned")
                continue
            volumes[label], symbols[label] = df, symbol
            say(f"  ok      {label:<22} {symbol:<10} {len(df)} candles")

    if not volumes:
        raise RuntimeError("No exchange returned data. Check your internet connection.")

    # Price
    if price == "okx-index":
        say("Downloading OKX index price...")
        prices = fetch_okx_index(base, timeframe, since, until)
        if prices.empty:
            raise RuntimeError(f"OKX returned no index candles for {base}-USDT")
    elif price == "composite":
        prices = composite_price(volumes)
    else:
        raise ValueError("price must be 'okx-index' or 'composite'")

    vol = pd.concat({f"vol_{k}": v["volume"] for k, v in volumes.items()}, axis=1)
    vol = vol.reindex(prices.index)
    out = prices[["open", "high", "low", "close"]].copy()
    out["volume"] = vol.sum(axis=1, min_count=1).fillna(0.0)
    out["n_exchanges"] = vol.notna().sum(axis=1)
    out = out.join(vol)

    report = make_report(out, symbols, skipped)
    return out, report


def composite_price(volumes: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Volume-weighted average of each OHLC field across exchanges.
    (USD and USDT prices are treated as equal; the gap is usually tiny.)"""
    weights = pd.concat({k: v["volume"] for k, v in volumes.items()}, axis=1)
    out = {}
    for field in ("open", "high", "low", "close"):
        values = pd.concat({k: v[field] for k, v in volumes.items()}, axis=1)
        w = weights.where(values.notna()).fillna(0.0)
        total = w.sum(axis=1)
        weighted = (values.fillna(0.0) * w).sum(axis=1) / total.where(total > 0)
        # candles where nobody traded: fall back to a plain average
        out[field] = weighted.fillna(values.mean(axis=1))
    return pd.DataFrame(out).dropna()


def make_report(df: pd.DataFrame, symbols: Dict[str, str], skipped: Dict[str, str]) -> dict:
    total = df["volume"].sum()
    per_exchange = {}
    for label, symbol in symbols.items():
        col = df[f"vol_{label}"]
        per_exchange[label] = {
            "symbol": symbol,
            "coverage": col.notna().mean() if len(df) else 0.0,
            "volume_share": col.sum() / total if total else 0.0,
        }
    full = df["n_exchanges"] == len(symbols)
    return {
        "candles": len(df),
        "start": df.index[0] if len(df) else None,
        "end": df.index[-1] if len(df) else None,
        "exchanges": per_exchange,
        "skipped": skipped,
        "complete_candles": full.mean() if len(df) else 0.0,
    }


def print_report(report: dict, base: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {report['candles']} candles  {report['start']:%Y-%m-%d %H:%M} -> "
          f"{report['end']:%Y-%m-%d %H:%M} UTC")
    print(f"  {'Exchange':<22}{'Pair':<11}{'Coverage':>9}{'Vol share':>11}")
    for label, r in sorted(report["exchanges"].items(), key=lambda x: -x[1]["volume_share"]):
        flag = "  <- gaps" if r["coverage"] < 0.95 else ""
        print(f"  {label:<22}{r['symbol']:<11}{r['coverage']:>9.1%}{r['volume_share']:>11.1%}{flag}")
    for label, reason in report["skipped"].items():
        print(f"  {label:<22}SKIPPED: {reason[:60]}")
    print(f"  Candles with data from every exchange: {report['complete_candles']:.1%}")
    print(f"  Volume is in {base}.")
    print("=" * 60)


def check_exchanges(base: str, exchanges: Optional[str]) -> None:
    """Quick test: can we read data from each exchange from this connection?"""
    print(f"Checking {base} data access...\n")
    for name, override in parse_exchange_list(exchanges, base):
        label = f"{name}:{override}" if override else name
        try:
            ex = make_exchange(name)
            symbol = pick_symbol(ex, name, base, override)
            candles = ex.fetch_ohlcv(symbol, "1h", limit=2)
            status = f"OK    {symbol}" if candles else "EMPTY"
        except Exception as e:
            status = f"FAIL  {type(e).__name__}: {str(e)[:70]}"
        print(f"  {label:<22} {status}")
    try:
        okx = make_exchange("okx")
        data = okx.publicGetMarketIndexCandles({"instId": f"{base}-USDT", "bar": "1H", "limit": 2})
        status = "OK" if data.get("data") else "EMPTY"
    except Exception as e:
        status = f"FAIL  {type(e).__name__}: {str(e)[:70]}"
    print(f"  {'okx index':<22} {status}")


def main():
    p = argparse.ArgumentParser(description="Build candles with volume aggregated across exchanges")
    p.add_argument("--base", default="BTC", help="coin, e.g. BTC or ETH")
    p.add_argument("--timeframe", default="1h", help="1m 5m 15m 1h 4h 1d ...")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--exchanges", default=None,
                   help="comma list, optional pair per exchange: binance,coinbase:BTC/USDC "
                        f"(default: {','.join(DEFAULT_EXCHANGES)})")
    p.add_argument("--price", default="okx-index", choices=["okx-index", "composite"])
    p.add_argument("--out", default=None, help="CSV path (default: data/aggregated_...csv)")
    p.add_argument("--check", action="store_true", help="only test which exchanges work")
    args = p.parse_args()
    base = args.base.upper()

    if args.check:
        check_exchanges(base, args.exchanges)
        return

    try:
        df, report = build_aggregated(base, args.timeframe, exchanges=args.exchanges,
                                      price=args.price, days=args.days)
    except (RuntimeError, ValueError) as e:
        sys.exit(f"Error: {e}")

    print_report(report, base)
    out = Path(args.out or f"data/aggregated_{base}_{args.timeframe}_{args.days}d_{args.price}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
