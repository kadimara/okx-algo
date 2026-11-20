"""Build the fresh BTC Market Structure page: 4h candles, 2-day swings and the LTF trend strip only.

Usage: python build_chart.py   (run with the okx-algo venv; writes btc-market-structure.html next to this file)
"""
import json
import sys
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)   # the okx-algo repo root
sys.path.insert(0, REPO)

import pandas as pd

from run_backtest_ms import build_parser, make_strategy
from structure import bos_pivot_list

HERE = Path(__file__).parent
TF, TFH, SWING = "1h", 1, 48   # 2-day swings
files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
if not files:
    raise SystemExit("No BTC 1h index data in data/ - run research/download_data.py first")
sig = pd.read_csv(files[-1], index_col=0, parse_dates=True)   # newest download
DAYS = 730   # show the last 2 years only (keeps the page small and the build fast)
sig = sig[sig.index >= sig.index[-1] - pd.Timedelta(days=DAYS)]
strat = make_strategy(build_parser().parse_args(["--timeframe", TF, "--swing-candles", str(SWING)]))
plan = strat.analyze(sig)

ts = lambda t: int(pd.Timestamp(t).timestamp())
T = [ts(t) for t in sig.index]
o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
trend = plan["trend"].to_numpy()

candles = [[T[i], round(o[i], 1), round(h[i], 1), round(l[i], 1), round(c[i], 1)] for i in range(len(sig))]
ltf = [{"bullish": 1, "bearish": -1}.get(x, 0) for x in trend]
swings = [[ts(r.pivot_time), r.type[0], r.label, round(float(r.price), 1), ts(r.confirmed_at), r.how == "bos"]
          for r in bos_pivot_list(sig, plan).itertuples()]

# CHoCH areas of a lower structure: for each CHoCH of a smaller swing size, the area from the extreme the
# move started at (lowest low since the broken swing high for a bullish CHoCH) to the broken level,
# spanning from that extreme candle to the candle that closed through. Several lower swing sizes in hours.
from structure import analyze_bos_structure  # noqa: E402

LOWER_HOURS = (6, 12, 24)
lower = {}
for hrs in LOWER_HOURS:
    nl = max(1, round(hrs / TFH))
    sl = analyze_bos_structure(sig, nl)
    areas = []
    for i in range(len(sig)):
        for d, col in ((1, "choch_up"), (-1, "choch_down")):
            if not sl[col].iat[i]:
                continue
            if ltf[i] != d:          # only CHoCHs in the direction of the 2-day trend (end of a pullback)
                continue
            p = int(sl["bos_from_idx"].iat[i])
            seg = slice(p, i + 1)
            e = p + int(l[seg].argmin() if d == 1 else h[seg].argmax())
            areas.append([T[e], T[i], round(float(sl["bos_level"].iat[i]), 1), round(float(l[e] if d == 1 else h[e]), 1), d, T[p]])
    lower[str(hrs)] = areas

data = {"tfh": TFH, "n": strat.n, "candles": candles, "ltf": ltf, "swings": swings, "lower": lower}
page = (HERE / "template_structure.html").read_text(encoding="utf-8").replace(
    "__DATA__", json.dumps(data, separators=(",", ":")))
(HERE / "ms_structure_chart.html").write_text(page, encoding="utf-8")
print({k: len(v) for k, v in lower.items()}, "lower CHoCH areas;", f"{len(candles)} candles, {len(swings)} swings, swing {strat.n} candles, {len(page) / 1e6:.2f} MB")
