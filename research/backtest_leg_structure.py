"""Backtest the leg structure of the chart (leg_swings in build_structure_chart.py) on the full BTC 1h history.

Counts swings, trends, reversals (MSS: the trend turns) and how many continuation breaks (MSB 1, MSB 2, ...)
each trend made before it reversed. By default the trend is the live replay (live_structure: at each close only the
swings confirmed by then), so there is no hindsight; --hindsight uses the structure as drawn now instead.

Usage: python research/backtest_leg_structure.py [--hindsight]   (okx-algo venv)
"""
import ast
import bisect
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd

from structure import analyze_bos_structure, bos_pivot_list

# Take the structure functions and settings from the chart build script without running the build itself
src = (Path(__file__).parent / "build_structure_chart.py").read_text(encoding="utf-8")
tree = ast.parse(src)
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "bisect": bisect, "heapq": __import__("heapq"), "_MEMO": {}}
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)
    if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "LEG_N" for t in getattr(node.targets[0], "elts", [])):
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = {t: i for i, t in enumerate(T)}
out, levels, bias, breaks = ns["leg_swings"](df, ns["LEG_N"], ns["MIN_LEG"])
HINDSIGHT = "--hindsight" in sys.argv
if not HINDSIGHT:
    bias, lbreaks, _ = ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"])
    # events: a change of the live trend is a reversal ([i, d, None, trend before]), a close through the level in
    # the trend direction a MSB ([i, d, level, d])
    msb = {b[0]: b for b in lbreaks if b[1] == b[3]}
    breaks = []
    for i in range(1, len(bias)):
        if bias[i] != bias[i - 1] and bias[i] != 0:
            breaks.append([i, bias[i], None, bias[i - 1]])
        elif i in msb:
            breaks.append(msb[i])

close, high, low = (df[k].to_numpy(float) for k in ("close", "high", "low"))
day = lambda i: df.index[i].strftime("%Y-%m-%d")
print(f"Data {day(0)} .. {day(len(df) - 1)}  ({len(df)} 1h candles, legs {ns['LEG_N']} candles / min leg {ns['MIN_LEG']}%, "
      f"{'HINDSIGHT structure' if HINDSIGHT else 'live replay, no hindsight'})\n")

# --- swings
struct = [s for s in out if not s[5]]
print(f"Swings: {len(out)} leg swings, {len(struct)} structural, {len(out) - len(struct)} internal")
print("  structural labels:", dict(Counter(s[2] for s in struct)))
by_year = Counter(pd.Timestamp(s[0], unit="s").year for s in out)
print("  swings per year:", dict(sorted(by_year.items())))

# --- breaks -> trends. A break against the trend (or the first one) is a reversal and starts a trend; a break
# with the trend is a MSB of that trend. The last trend is still running and is reported separately.
trends = []
for i, d, lvl, before in breaks:
    if d != before:
        trends.append({"dir": d, "start": i, "level": lvl, "msb": []})
    elif trends:
        trends[-1]["msb"].append(i)
for k, t in enumerate(trends):
    end = trends[k + 1]["start"] if k + 1 < len(trends) else len(df) - 1
    t["end"], t["done"] = end, k + 1 < len(trends)
    seg = slice(t["start"], end + 1)
    c0 = close[t["start"]]
    ext = high[seg].max() if t["dir"] > 0 else low[seg].min()
    t["mfe"] = (ext / c0 - 1) * 100 * t["dir"]                 # best move from the reversal close, in trend direction
    t["pnl"] = (close[end] / c0 - 1) * 100 * t["dir"]         # reversal close to the next reversal close
    t["days"] = (df.index[end] - df.index[t["start"]]).total_seconds() / 86400

done = [t for t in trends if t["done"]]
n_msb = sum(len(t["msb"]) for t in trends)
print(f"\nBreaks: {len(breaks)} total = {len(trends)} reversals (MSS) + {n_msb} continuations (MSB)")
print(f"Trends: {len(trends)} ({sum(t['dir'] > 0 for t in trends)} bullish, {sum(t['dir'] < 0 for t in trends)} bearish), "
      f"{len(done)} finished, last one {'bullish' if trends[-1]['dir'] > 0 else 'bearish'} since {day(trends[-1]['start'])} "
      f"with {len(trends[-1]['msb'])} MSB so far")
print("  trends per year:", dict(sorted(Counter(df.index[t["start"]].year for t in trends).items())))


def table(rows, title):
    print(f"\n{title}")
    print(f"{'MSB before reversal':>20} {'trends':>7} {'share':>6} {'reach >=':>8} {'P(next MSB)':>12} {'med days':>9} "
          f"{'med best %':>11} {'med close->close %':>18}")
    cnt = Counter(len(t["msb"]) for t in rows)
    top = max(cnt) if cnt else 0
    for k in range(top + 1):
        grp = [t for t in rows if len(t["msb"]) == k]
        if not grp:
            continue
        reach = sum(len(t["msb"]) >= k for t in rows)
        nxt = sum(len(t["msb"]) >= k + 1 for t in rows)
        med = lambda key: np.median([t[key] for t in grp]) if grp else float("nan")
        print(f"{k:>20} {len(grp):>7} {len(grp) / len(rows) * 100:>5.0f}% {reach:>8} "
              f"{(nxt / reach * 100 if reach else float('nan')):>11.0f}% {med('days'):>9.1f} {med('mfe'):>11.1f} "
              f"{med('pnl'):>18.1f}")


table(done, "Finished trends by the number of MSB they made (0 = reversed again before any MSB):")
table([t for t in done if t["dir"] > 0], "Bullish trends:")
table([t for t in done if t["dir"] < 0], "Bearish trends:")
print("\n'reach >=' = trends that made at least that many MSB; P(next MSB) = of those, the share that made one more"
      "\nbefore reversing. best % = largest move from the reversal close in the trend direction; close->close % = "
      "\nreversal close to the close of the break that ended the trend.")


# --- confirmed trends (the bias bar): a MSS (break against the trend) only starts a new trend once the new
# direction makes its first MSB; a MSS that fails (price breaks back in the old direction first) is invalidated
# and the old trend continues. Walked candle by candle, so pos_hold / pos_flat only use what was known at each close.
conf, pend = 0, None
ctrends, msss = [], Counter()
pos_hold, pos_flat = np.zeros(len(df)), np.zeros(len(df))
at = {i: (d, before) for i, d, _, before in breaks}
for i in range(len(df)):
    if i in at:
        d, before = at[i]
        if pend is not None and d == conf:          # failed MSS: the old trend continues, this break is its MSB
            msss["failed"] += 1
            pend = None
            ctrends[-1]["msb"].append(i)
        elif pend is not None and d == pend:        # first MSB of the new direction confirms the MSS
            msss["confirmed"] += 1
            pend, conf = None, d
            ctrends.append({"dir": d, "start": i, "msb": [i]})
        elif d != before:                           # a new MSS
            msss["all"] += 1
            pend = d
        elif ctrends and d == conf:
            ctrends[-1]["msb"].append(i)
    pos_hold[i] = conf
    pos_flat[i] = 0 if pend is not None else conf
for k, t in enumerate(ctrends):
    end = ctrends[k + 1]["start"] if k + 1 < len(ctrends) else len(df) - 1
    t["end"], t["done"] = end, k + 1 < len(ctrends)
    seg = slice(t["start"], end + 1)
    c0 = close[t["start"]]
    ext = high[seg].max() if t["dir"] > 0 else low[seg].min()
    t["mfe"] = (ext / c0 - 1) * 100 * t["dir"]
    t["pnl"] = (close[end] / c0 - 1) * 100 * t["dir"]
    t["days"] = (df.index[end] - df.index[t["start"]]).total_seconds() / 86400
cdone = [t for t in ctrends if t["done"]]
print(f"\n=== Confirmed trends (MSS needs a MSB, failed MSS = old trend continues) ===")
print(f"MSSs: {msss['all']} = {msss['confirmed']} confirmed + {msss['failed']} failed (invalidated)"
      f" + {msss['all'] - msss['confirmed'] - msss['failed']} open / before the first trend"
      f"  (the other {len(trends) - msss['all']} old reversals are the breaks back that ended a failed MSS)")
print(f"Trends: {len(ctrends)} ({sum(t['dir'] > 0 for t in ctrends)} bullish, {sum(t['dir'] < 0 for t in ctrends)} bearish),"
      f" last one {'bullish' if ctrends[-1]['dir'] > 0 else 'bearish'} since {day(ctrends[-1]['start'])}"
      f" with {len(ctrends[-1]['msb'])} MSB so far")
print("  trends per year:", dict(sorted(Counter(df.index[t["start"]].year for t in ctrends).items())))
table(cdone, "Finished confirmed trends by MSB count (MSB 1 = the break that confirmed it; measured from that close):")

# --- following the bias: long when bullish, short when bearish (flat when 0), from each 1h close to the next
r = np.diff(np.log(close))
print(f"\n=== Following the bias, 1h close to close (fees per side, a flip = 2 sides) ===")
print(f"{'':>34} {'no fee':>8} {'0.05%':>8} {'flips':>7} {'long':>5} {'short':>6} {'flat':>5}")
variants = (("old: flip at every MSS", np.array(bias, float)),
            ("new, hold old trend while grey", pos_hold),
            ("new, flat while grey", pos_flat))
for name, p in variants:
    p = p[:-1]
    units = np.abs(np.diff(p)).sum()
    res = [np.expm1((p * r).sum() - units * f / 100) * 100 for f in (0, 0.05)]
    print(f"{name:>34} {res[0]:>+7.0f}% {res[1]:>+7.0f}% {units / 2:>7.0f} {np.mean(p > 0) * 100:>4.0f}% "
          f"{np.mean(p < 0) * 100:>5.0f}% {np.mean(p == 0) * 100:>4.0f}%")
print(f"{'buy and hold':>34} {np.expm1(r.sum()) * 100:>+7.0f}%")
print(f"\n{'year':>6}{'old':>12}{'new hold':>12}{'new flat':>12}{'buy+hold':>10}")
for y in sorted(set(df.index.year)):
    m = df.index[1:].year == y
    print(f"{y:>6}" + "".join(f"{np.expm1((p[:-1] * r)[m].sum()) * 100:>+11.0f}%" for _, p in variants)
          + f"{np.expm1(r[m].sum()) * 100:>+9.0f}%")
