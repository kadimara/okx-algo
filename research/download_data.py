"""Download BTC 1h and 4h candles (OKX index with aggregated volume + the USDT perpetual) from 1 Jan 2020
until now into data/. Run from anywhere with the repo venv:  python research/download_data.py
Takes a few minutes (the index volume is added up across exchanges)."""
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)                      # cached() saves into ./data

import ccxt  # noqa: E402

from vwap_data import cached, fetch_perp, fetch_signal  # noqa: E402

since = ccxt.Exchange.parse8601("2020-01-01T00:00:00Z")
days = int((ccxt.Exchange.milliseconds() - since) / 86_400_000) + 1
for tf in ("4h", "1h"):
    tag = f"BTC_{tf}_{days}d"
    for kind, loader in (("perp", lambda: fetch_perp("BTC", tf, since, verbose=True)),
                         ("index_signal", lambda: fetch_signal("index", "BTC", tf, since, verbose=True))):
        t = time.time()
        df = cached(f"{tag}_{kind}", loader)
        print(f"{tag}_{kind}: {len(df)} candles {df.index[0]} -> {df.index[-1]} ({time.time() - t:.0f}s)", flush=True)
