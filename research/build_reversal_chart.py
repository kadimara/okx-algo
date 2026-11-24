"""Reversal study chart (clean slate): BTC 1h candles with the 4-candle swing structure and every reversal (MSS) on it.

- Swings: 4-candle pivots (highest / lowest of 4 candles on each side), legs under REV_MIN_LEG % merged (the leg
  structure of build_structure_chart.py with LEG_N = 4); labels HH / HL / LH / LL with hindsight (final legs).
- Reversals: every MSS of the live 4-candle structure (a close through the structural level against the trend on a
  powerful candle: body >= REV_POWER x ATR; no hindsight). Area: from the extreme of the move before it (the lowest low / highest high since the previous opposite
  MSS) to the MSS candle, between that extreme and the broken level.
- After each reversal: the move in its direction until the next opposite MSS (max high / min low vs the MSS close).
Writes research/reversal_chart.html from research/template_reversal.html.

Usage: python research/build_reversal_chart.py
"""
import ast
import bisect
import heapq
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
import numpy as np
import pandas as pd
from structure import analyze_bos_structure, bos_pivot_list

REV_N, REV_MIN_LEG, DAYS = 4, 1.5, 730
# REV_POWER: a MSS (break against the trend) only counts on a powerful candle: its body in the break direction is at
# least REV_POWER x ATR(14) (Wilder, as of the candle before). 0 = any close beyond the level. MSBs are unchanged.
REV_POWER = 1.5

src = (HERE / "build_structure_chart.py").read_text(encoding="utf-8")
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "np": np,
      "bisect": bisect, "heapq": heapq, "_MEMO": {}}
for node in ast.parse(src).body:
    names = {getattr(e, "id", None) for t in getattr(node, "targets", []) for e in getattr(t, "elts", [t])}
    if isinstance(node, ast.FunctionDef) or names & {"LEG_N", "STRUCT_MODE", "SUPPLY_MIN_PCT"}:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
df = df[df.index >= df.index[-1] - pd.Timedelta(days=DAYS)]
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = {t: i for i, t in enumerate(T)}
H, L, C = (df[k].to_numpy(float) for k in ("high", "low", "close"))
O = df["open"].to_numpy(float)
if REV_POWER:
    atr = ns["wilder_atr"](df)
    a_prev = np.r_[atr[0], atr[:-1]]
    ns["BREAK_POW"] = ((C - O) >= REV_POWER * a_prev, (O - C) >= REV_POWER * a_prev)

trend, breaks, _ = ns["live_structure"](df, REV_N, REV_MIN_LEG)
bias = ns["bias_bar"](trend, breaks)
legs, _, _, _ = ns["leg_swings"](df, REV_N, REV_MIN_LEG)

mss = [b for b in breaks if b[3] != 0 and b[1] != b[3]]           # [candle, direction, level, trend before]
rev, prev = [], None
for q, (i, d, level, _) in enumerate(mss):
    a = prev if prev is not None else max(0, i - 200)
    e = a + int(np.argmin(L[a:i + 1]) if d > 0 else np.argmax(H[a:i + 1]))
    nxt = mss[q + 1][0] if q + 1 < len(mss) else len(df) - 1
    seg_end = max(nxt, i + 1)
    follow = (H[i:seg_end + 1].max() / C[i] - 1) * 100 if d > 0 else (1 - L[i:seg_end + 1].min() / C[i]) * 100
    rev.append([T[e], T[i], round(float(L[e] if d > 0 else H[e]), 1), round(float(level), 1), d, round(float(follow), 1),
                round(float(abs(C[i] / (L[e] if d > 0 else H[e]) - 1) * 100), 1)])
    prev = i

data = {"candles": [[T[k], *(round(float(v), 1) for v in r)] for k, r in enumerate(df[["open", "high", "low", "close"]].to_numpy())],
        "legs": legs, "bias": [float(b) for b in bias], "rev": rev, "n": REV_N, "minLeg": REV_MIN_LEG, "power": REV_POWER}
page = (HERE / "template_reversal.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, separators=(",", ":")))
(HERE / "reversal_chart.html").write_text(page, encoding="utf-8")
up = [r for r in rev if r[4] > 0]; dn = [r for r in rev if r[4] < 0]
print(f"{len(df)} candles, {len(legs)} 4-candle leg swings, {len(up)} reversals up / {len(dn)} down "
      f"(median follow-through {np.median([r[5] for r in up]):.1f}% / {np.median([r[5] for r in dn]):.1f}%), {len(page) / 1e6:.2f} MB")
