"""Does BTC fall faster than it rises? Three measurements on the newest BTC 1h index file in data/, for all years and
split by year (bull and bear years separately):

1. Legs: the leg swings of the chart (8-candle swings, legs under MIN_LEG % merged, hindsight is fine for a description):
   size, duration and speed (% per hour) of the down legs vs the up legs.
2. Big candles and skew: how often a 1h candle closes more than 2 / 3 ATR(14) down vs up, and the skew of the 1h, 4h and
   24h returns (negative = a longer tail of big drops).
3. Time for the same move: from every hour, the hours until price first trades X % below vs X % above the close
   (X = 3 and 5); the median and the share reached within 24h.

Usage: python research/backtest_asymmetry.py   (okx-algo venv)
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

src = (Path(__file__).parent / "build_structure_chart.py").read_text(encoding="utf-8")
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "np": np, "bisect": bisect}
for node in ast.parse(src).body:
    names = {getattr(e, "id", None) for t in getattr(node, "targets", []) for e in getattr(t, "elts", [t])}
    if isinstance(node, ast.FunctionDef) or names & {"LEG_N"}:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = t_idx = {t: i for i, t in enumerate(T)}
O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
years = df.index.year.to_numpy()
YEARS = sorted(set(years))
print(f"Data {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d} ({len(df)} candles)")
print("Year change of the price: " + "  ".join(
    f"{y}: {(C[years == y][-1] / C[years == y][0] - 1) * 100:+.0f}%" for y in YEARS))

# 1. Legs
P = ns["merge_legs"](ns["base_swings"](df, ns["LEG_N"]), ns["MIN_LEG"])
legs = []
for a, b in zip(P, P[1:]):
    i, j = t_idx[a[0]], t_idx[b[0]]
    if j <= i:
        continue
    pct = (b[2] / a[2] - 1) * 100
    legs.append({"year": df.index[i].year, "dir": "up" if pct > 0 else "down", "pct": abs(pct), "hours": j - i})
legs = pd.DataFrame(legs)
legs["speed"] = legs["pct"] / legs["hours"]
print(f"\n1. Legs (swings of {ns['LEG_N']} candles, legs under {ns['MIN_LEG']}% merged): {len(legs)} legs")
print(f"{'period':>8} {'dir':>5} {'legs':>5} {'median size':>12} {'median hours':>13} {'median %/h':>11} {'mean %/h':>9}   down vs up speed")
for y in ["all"] + YEARS:
    g = legs if y == "all" else legs[legs["year"] == y]
    med = {}
    for d in ("down", "up"):
        x = g[g["dir"] == d]
        med[d] = x["speed"].median()
        print(f"{str(y):>8} {d:>5} {len(x):>5} {x['pct'].median():>11.1f}% {x['hours'].median():>13.0f} "
              f"{x['speed'].median():>10.3f}% {x['speed'].mean():>8.3f}%" + (f"   {med['down'] / med['up']:.2f}x" if d == "up" else ""))

# 2. Big candles and skew
tr = np.maximum(H, np.r_[C[0], C[:-1]]) - np.minimum(L, np.r_[C[0], C[:-1]])
atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
move = (C - O) / np.r_[atr[0], atr[:-1]]          # candle body in ATR (as of the candle before)
print("\n2. Big 1h candles (body vs ATR(14) of the candle before) and the skew of the returns")
print(f"{'period':>8} {'> 2 ATR down':>13} {'> 2 ATR up':>11} {'> 3 ATR down':>13} {'> 3 ATR up':>11} {'skew 1h':>8} {'skew 4h':>8} {'skew 24h':>9}")
for y in ["all"] + YEARS:
    m = np.ones(len(df), bool) if y == "all" else years == y
    s = pd.Series(C[m])
    sk = [s.pct_change(k).iloc[::k].skew() for k in (1, 4, 24)]
    print(f"{str(y):>8} {np.sum(move[m] < -2):>13} {np.sum(move[m] > 2):>11} {np.sum(move[m] < -3):>13} {np.sum(move[m] > 3):>11} "
          + " ".join(f"{v:>+8.2f}" for v in sk))

# 3. Time for the same move: hours until the low is X % below / the high X % above the close
print("\n3. Hours until price first trades X% below vs X% above the close (from every hour)")
print(f"{'period':>8} {'X':>4} {'median down':>12} {'median up':>10} {'down < 24h':>11} {'up < 24h':>9}")
MAXH = 24 * 60


def first_hit(level_arr, start, cond):
    """Hours from start until cond(level_arr[k]) first holds (np.nan if not within MAXH)."""
    seg = level_arr[start + 1:start + 1 + MAXH]
    k = np.flatnonzero(cond(seg))
    return k[0] + 1 if k.size else np.nan


for X in (3, 5):
    dn = np.array([first_hit(L, i, lambda s: s <= C[i] * (1 - X / 100)) for i in range(0, len(df), 3)])
    up = np.array([first_hit(H, i, lambda s: s >= C[i] * (1 + X / 100)) for i in range(0, len(df), 3)])
    ys = years[::3]
    for y in ["all"] + YEARS:
        m = np.ones(len(ys), bool) if y == "all" else ys == y
        print(f"{str(y):>8} {X:>3}% {np.nanmedian(dn[m]):>12.0f} {np.nanmedian(up[m]):>10.0f} "
              f"{np.mean(dn[m] <= 24) * 100:>10.0f}% {np.mean(up[m] <= 24) * 100:>8.0f}%")
    print()

# The tail: of the fastest legs (top 10 % / 5 % / 1 % by speed), how many are down legs
print("Fastest legs: share of down legs among the fastest")
for q in (0.5, 0.75, 0.9, 0.95, 0.99):
    cut = legs["speed"].quantile(q)
    top = legs[legs["speed"] >= cut]
    print(f"  top {100 - q * 100:>4.0f}% (>= {cut:.2f}%/h): {len(top):>4} legs, {np.mean(top['dir'] == 'down') * 100:.0f}% down, "
          f"median size down {top[top['dir'] == 'down']['pct'].median():.1f}% / up {top[top['dir'] == 'up']['pct'].median():.1f}%")
