"""Backtest short entries at the red supply zones of the chart (supply_zones in build_structure_chart.py), full BTC 1h history.

Only the red zones (a sharp move down within SHARP_K candles after the zone candle), for several SHARP_K windows.
Entries (sell limits at the zone candle): "zone" = its low, "body low" = min(open, close), "body high" = max(open, close).
The order rests once price first trades back into a drawn zone (known live while the bias was bearish). Stop at the
zone top (the candle high), so 1R = top - entry. Zones that end without a
touch (bias no longer bearish, a newer leg high) give no trade. Exit: the stop, or a target at the low of
the drop away from the zone (the lowest low between the zone candle and the fill); trades whose low is less than MIN_RR
R away are skipped. Zones are split by where they sit in the leg ("between" the leg high and low / "after low").

Conservative fills: on the entry candle only the stop counts (the low may have come before the fill); a candle that
hits both stop and target is a loss. MFE = the best move in R before the stop is hit (or until the end of the data).

Usage: python research/backtest_supply_entries.py   (okx-algo venv)
"""
import ast
import bisect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd

from structure import analyze_bos_structure, bos_pivot_list

NL = chr(10)

# Take the structure functions and settings from the chart build script without running the build itself
src = (Path(__file__).parent / "build_structure_chart.py").read_text(encoding="utf-8")
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "np": np, "bisect": bisect}
for node in ast.parse(src).body:
    names = {getattr(e, "id", None) for t in getattr(node, "targets", []) for e in getattr(t, "elts", [t])}
    if isinstance(node, ast.FunctionDef) or names & {"LEG_N", "SHARP_K", "DRAW_GREY", "WIDE_ZONE", "LH_BIAS", "SUPPLY_MIN_PCT", "SUPPLY_MSS_OK", "COUNTER_MSS_ENDS", "STRUCT_MODE"}:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = t_idx = {t: i for i, t in enumerate(T)}
H, L = df["high"].to_numpy(float), df["low"].to_numpy(float)

trend, breaks, start = ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"])
bias = ns["bias_bar"](trend, breaks)

WINDOWS = (6, 8, 12)   # SHARP_K: candles after the zone candle in which the sharp move down must happen
ENTRIES = ("zone", "body low", "body high")   # sell limit at the candle low / min(open, close) / max(open, close)
MIN_RR = 2             # skip trades whose target (the low) is less than this many R away
FEE = 0.05             # % per side
O, C = df["open"].to_numpy(float), df["close"].to_numpy(float)

# Where the zone sits in the leg, without hindsight: the leg swings as known at the close before the first touch
# (rebuilt like live_structure). Last leg swing before the zone candle a high -> the zone formed during the drop,
# "between" the leg high and its low; a low -> the zone formed "after the low" (in the pullback).
EV = ns["swing_events"](df, ns["LEG_N"])
EV_CONF = [t_idx[p[3]] for p in EV]
_legs = {}


def legs_known(i):
    m = bisect.bisect_right(EV_CONF, i)
    if m not in _legs:
        known = EV[:m][-240:]
        known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
        _legs[m] = ns["merge_legs"](known[-120:], ns["MIN_LEG"])
    return _legs[m]


def place(t0, e):
    prev = [p for p in legs_known(e - 1) if p[0] < t0]
    return "between" if prev and prev[-1][1] == "h" else "after low"


def run(sharp_k, entry):
    """Trades at the red (sharp) zones for one SHARP_K and entry level: one row per filled order. The stop is the zone
    top (the candle high). The order rests from the first touch of the zone until it fills, price trades above the
    top (fills and stops), or the zone is cancelled (bias no longer bearish, a newer leg high)."""
    ns["SHARP_K"] = sharp_k
    trades, skipped = [], [0]
    for t0, t1, top, bot, sharp, *_ in ns["supply_zones"](df, ns["LEG_N"], bias, start):
        if not sharp or t1 is None:
            continue
        e, j = t_idx[t1], t_idx[t0]
        if H[e] < bot:            # ended without a touch: no trade
            continue
        x = {"zone": bot, "body low": min(O[j], C[j]), "body high": max(O[j], C[j])}[entry]
        risk = top - x
        if risk <= 0:             # no upper wick: no room for a stop
            continue
        f = None
        for k in range(e, len(df)):
            if H[k] >= x:
                f = k
                break
            if (start[k] is not None and start[k] > t0) or bias[k] > -1:
                break             # cancelled at this close before a fill
        if f is None:
            continue
        stop_i = next((k for k in range(f, len(df)) if H[k] > top), None)
        last = stop_i if stop_i is not None else len(df) - 1
        lo = L[j + 1:f].min()     # the low of the drop away from the zone, known before the fill
        rr = (x - lo) / risk
        if rr < MIN_RR:
            skipped[0] += 1
            continue
        row = {"time": df.index[f], "risk_pct": risk / x * 100, "stop_same": stop_i == f, "place": place(t0, e), "rr": rr}
        hit = next((k for k in range(f + 1, last + 1) if L[k] <= lo and H[k] <= top), None)
        row["r"] = rr if hit is not None else (-1.0 if stop_i is not None else np.nan)
        seg = L[f + 1:last + 1]
        row["mfe"] = max((x - seg.min()) / risk, 0.0) if seg.size else 0.0
        trades.append(row)
    return pd.DataFrame(trades), skipped[0]


print(f"Data {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d}. Red supply zones only. Stop at the zone top, target at "
      f"the low of the drop before the fill, trades with a target under {MIN_RR}R skipped. Fee {FEE}% per side." + NL)
print(f"{'window':>7} {'entry':>10} {'zones':>10} {'trades':>7} {'skipped':>8} {'1R %':>6} {'target R':>9} {'stop 1st':>9} "
      f"{'win %':>6} {'avg R fees':>11} {'total R fees':>13} {'open':>5}")
PLACES = ("all", "between", "after low")
res = {}


def net(d):
    """Results in R after fees (open trades left out)."""
    c = d.dropna(subset=["r"])
    return c["r"] - 2 * FEE / c["risk_pct"]


for k in WINDOWS:
    for en in ENTRIES:
        res[k, en], skip = run(k, en)
        for pl in PLACES:
            d = res[k, en] if pl == "all" else res[k, en][res[k, en]["place"] == pl]
            n = net(d)
            print(f"{k:>6}c {en:>10} {pl:>10} {len(d):>7} {skip if pl == 'all' else '':>8} {d['risk_pct'].median():>5.2f}% "
                  f"{d['rr'].median():>8.1f}R {d['stop_same'].mean() * 100:>8.0f}% {(n > 0).mean() * 100:>5.0f}% "
                  f"{n.mean():>+11.2f} {n.sum():>+12.0f}R {d['r'].isna().sum():>5}")
        print()
print("skipped = target under the minimum; 1R % = median stop distance; target R = median distance to the low in R;"
      + NL + "stop 1st = stopped on the fill candle; open = still running at the end of the data.")

for en in ENTRIES:
    print(NL + f"Per year, entry {en}: total R after fees (trades) for all / between / after low")
    print(f"{'year':>6}" + "".join(f"{str(k) + ' candles':>30}" for k in WINDOWS))
    for y in sorted(set(df.index.year)):
        row = f"{y:>6}"
        for k in WINDOWS:
            d = res[k, en][res[k, en]["time"].dt.year == y]
            row += "   " + " ".join(f"{net(g).sum():>+4.0f} ({len(g):>2})" for g in
                                    (d, d[d["place"] == "between"], d[d["place"] == "after low"]))
        print(row)
