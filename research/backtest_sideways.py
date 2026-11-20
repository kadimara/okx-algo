"""Test a sideways (consolidation) state for the leg structure, defined by compression (option 2). No hindsight:
every measure only uses candles and swings known at that close.

Two compression definitions, each over a grid of settings:
  range   sideways when the high-low range of the last W candles is < K x ATR(14) (1h)
  legs    sideways when the last 3 legs between the confirmed 8-candle swings are all < X % and the close is
          still inside the high / low of those swings

For each setting:
  share       % of candles marked sideways
  fwd range   median high-low range of the next 24 candles in % (sideways vs the rest): lower = it really is chop
  trend fwd   mean 24h return in the live trend direction (sideways vs the rest): lower/negative in sideways =
              trading the trend there loses
  strategy    follow the live trend (long / short, close to close) but flat while sideways, 0.05% fee per side;
              compared with always following the trend (baseline)

Usage: python research/backtest_sideways.py   (okx-algo venv)
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

# the structure functions and settings from the chart build script, without running the build
src = (Path(__file__).parent / "build_structure_chart.py").read_text(encoding="utf-8")
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "bisect": bisect}
for node in ast.parse(src).body:
    if isinstance(node, ast.FunctionDef) or (isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "LEG_N" for t in getattr(node.targets[0], "elts", []))):
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["T"] = T = [int(pd.Timestamp(t).timestamp()) for t in df.index]
ns["t_idx"] = t_idx = {t: i for i, t in enumerate(T)}
N = len(df)
o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
print(f"Data {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d} ({N} 1h candles), live trend replay ...", flush=True)
trend = np.array(ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"])[0], float)

# ATR(14), Wilder
pc = np.r_[c[0], c[:-1]]
tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()

# forward measures (next 24 candles)
F = 24
fr = np.full(N, np.nan)
fret = np.full(N, np.nan)
hh = pd.Series(h[::-1]).rolling(F).max().to_numpy()[::-1]     # max of h[i..i+F-1]
ll = pd.Series(l[::-1]).rolling(F).min().to_numpy()[::-1]
fr[:N - F] = (hh[1:N - F + 1] - ll[1:N - F + 1]) / c[:N - F] * 100
fret[:N - F] = (c[F:] / c[:N - F] - 1) * 100 * trend[:N - F]

r = np.diff(np.log(c))


def strat(pos, fee=0.05):
    p = pos[:-1]
    units = np.abs(np.diff(np.r_[0, p])).sum()
    return np.expm1((p * r).sum() - units * fee / 100) * 100, units / 2


def report(name, side):
    ok = ~np.isnan(fr) & (trend != 0)
    s, t = ok & side, ok & ~side
    pnl, flips = strat(np.where(side, 0, trend))
    print(f"{name:<22} {side[trend != 0].mean() * 100:>5.0f}%  {np.median(fr[s]):>6.2f} vs {np.median(fr[t]):>5.2f}"
          f"  {fret[s].mean():>+6.2f} vs {fret[t].mean():>+6.2f}  {pnl:>+8.0f}% {flips:>6.0f}")


print(f"\n{'setting':<22} {'share':>6}  {'fwd range %':>15}  {'trend fwd 24h %':>16}  {'strategy':>9} {'flips':>6}")
print(f"{'':<22} {'':>6}  {'side vs rest':>15}  {'side vs rest':>16}")
base, bflips = strat(trend)
print(f"{'baseline (no sideways)':<22} {0:>5.0f}%  {'':>15}  {np.nanmean(fret[trend != 0]):>+16.2f}  {base:>+8.0f}% {bflips:>6.0f}")

# --- range compression
for W in (24, 48, 96):
    rng = pd.Series(h).rolling(W).max().to_numpy() - pd.Series(l).rolling(W).min().to_numpy()
    ratio = rng / atr
    for K in ((4, 6, 8) if W == 24 else (6, 8, 10) if W == 48 else (8, 11, 14)):
        report(f"range W{W} K<{K}", np.nan_to_num(ratio, nan=99) < K)

# --- leg compression: last 3 legs between confirmed base swings (8-candle pivots, alternating, known at the close)
ev = ns["swing_events"](df, ns["LEG_N"])        # (t, type, price, conf)
ev = sorted(ev, key=lambda e: t_idx[e[3]])
legs_at = [None] * N
known, k = [], 0
for i in range(N):
    while k < len(ev) and t_idx[ev[k][3]] <= i:
        t, ty, p, _ = ev[k]
        if known and known[-1][1] == ty:            # same type twice: keep the more extreme one
            if (p > known[-1][2]) == (ty == "h"):
                known[-1] = (t, ty, p)
        else:
            known.append((t, ty, p))
        k += 1
    if len(known) >= 4:
        P = [x[2] for x in known[-4:]]
        legs_at[i] = (max(abs(P[j + 1] / P[j] - 1) * 100 for j in range(3)), max(P), min(P))
for X in (2, 3, 4, 5):
    side = np.array([a is not None and a[0] < X and a[2] <= c[i] <= a[1] for i, a in enumerate(legs_at)])
    report(f"legs last3 < {X}%", side)

print("\nshare = % of trending candles marked sideways; fwd range = median high-low of the next 24h; trend fwd = mean"
      "\n24h return in the live trend direction; strategy = follow the live trend, flat while sideways, 0.05% fee/side.")
