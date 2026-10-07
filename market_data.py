"""
Downloads candles from OKX. Shared by both engines so they get data the same way.
Only CLOSED candles are returned; the candle still forming is dropped.

price="spot"  -> the tradable OKX market (e.g. BTC/USDT spot)
price="index" -> the OKX index for that pair: an average of the price across
                 several major exchanges. Smoother, harder to manipulate, but
                 not tradable and has no volume.
"""
import pandas as pd

COLUMNS = ["time", "open", "high", "low", "close", "volume"]
PAGE = 100  # OKX returns at most 100 historical candles per request (spot and index)
PRICE_TYPES = ("spot", "index")


def timeframe_ms(exchange, timeframe: str) -> int:
    return exchange.parse_timeframe(timeframe) * 1000


def fetch_candles(exchange, symbol: str, timeframe: str, since_ms: int,
                  price: str = "spot", verbose: bool = False) -> pd.DataFrame:
    """All closed candles from since_ms until now, downloaded page by page."""
    if price not in PRICE_TYPES:
        raise ValueError(f"price must be one of {PRICE_TYPES}")
    bar = timeframe_ms(exchange, timeframe)
    now = exchange.milliseconds()
    rows, cursor = [], since_ms

    while cursor < now - bar:
        if price == "index":
            batch = _fetch_index_page(exchange, symbol, timeframe, cursor)
        else:
            batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=PAGE)
        if not batch:
            cursor += bar * PAGE  # gap in the data (e.g. maintenance): skip ahead
            continue
        rows.extend(batch)
        nxt = batch[-1][0] + bar
        cursor = nxt if nxt > cursor else cursor + bar * PAGE
        if verbose:
            print(f"\r  downloaded {len(rows)} {price} candles", end="", flush=True)
    if verbose:
        print()

    return _to_frame(rows, bar, now)


def fetch_recent(exchange, symbol: str, timeframe: str, count: int,
                 price: str = "spot") -> pd.DataFrame:
    """The most recent `count` closed candles."""
    bar = timeframe_ms(exchange, timeframe)
    since = exchange.milliseconds() - (count + 3) * bar
    return fetch_candles(exchange, symbol, timeframe, since, price=price).tail(count)


def _fetch_index_page(exchange, symbol: str, timeframe: str, since: int) -> list:
    """One page of OKX index candles starting at `since`.

    Calls OKX's index-candle endpoints directly: ccxt's built-in route sends an
    empty instrument id for spot pairs. The index id equals the spot id ("BTC-USDT").
    """
    if not exchange.markets:
        exchange.load_markets()
    seconds = exchange.parse_timeframe(timeframe)
    bar_ms = seconds * 1000
    bar = exchange.timeframes.get(timeframe, timeframe)
    if seconds >= 6 * 3600:
        bar += "utc"  # daily/6h+ candles aligned to UTC, same as ccxt does for spot
    request = {
        "instId": exchange.market(symbol)["id"],
        "bar": bar,
        "limit": PAGE,
        "before": max(since - 1, 0),
        "after": since + bar_ms * PAGE,
    }
    recent_cutoff = exchange.milliseconds() - 1439 * bar_ms
    if since < recent_cutoff:
        response = exchange.publicGetMarketHistoryIndexCandles(request)
    else:
        response = exchange.publicGetMarketIndexCandles(request)
    # rows: [ts, open, high, low, close, confirm], newest first -> sort oldest first.
    # An index has no volume, so volume is 0.
    rows = [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), 0.0]
            for r in response.get("data", [])]
    return sorted(rows, key=lambda r: r[0])


def align(signal: pd.DataFrame, trade: pd.DataFrame):
    """Keep only the timestamps that exist in both (index and spot) data sets."""
    common = signal.index.intersection(trade.index)
    return signal.loc[common], trade.loc[common]


def _to_frame(rows, bar_ms: int, now_ms: int) -> pd.DataFrame:
    df = pd.DataFrame([r[:6] for r in rows], columns=COLUMNS)
    if df.empty:
        return df.set_index("time")
    df = df.drop_duplicates("time").sort_values("time")
    df = df[df["time"] + bar_ms <= now_ms]  # drop the candle that is still forming
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    df = df.set_index("time").apply(pd.to_numeric, errors="coerce")
    df["volume"] = df["volume"].fillna(0.0)  # index candles have no volume
    return df.astype(float)
