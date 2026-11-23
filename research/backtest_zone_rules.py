"""Compare supply-zone rules of the chart (supply_zones in build_structure_chart.py) with the same short trades:
"old" = sharp means a low 1 ATR below the zone within 6 candles; "new" = very sharp, 2 ATR within 4 candles (as in the
build script). The zone is the range of the swing-high candle in both.

Red zones only (a sharp move down after the zone). The sell limit rests from the first touch of a drawn zone (known
live while the bias was bearish) until it fills or the zone is cancelled (bias no longer bearish, a newer leg high).
Entries: "zone" = the zone bottom, "mid" = the middle of the zone. Stop just above the zone top, so 1R = top - entry.
Targets: "drop low" = the low of the drop away from the zone before the fill (trades under MIN_RR R skipped, as in
backtest_supply_entries.py), "2R" = a fixed 2R target.
Conservative fills: on the fill candle only the stop counts; a candle that hits both stop and target is a loss.

Usage: python research/backtest_zone_rules.py   (newest BTC 1h index file in data/)
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

# Take the structure functions and settings from the chart build script without running the build itself
src = (Path(__file__).parent / "build_structure_chart.py").read_text(encoding="utf-8")
ns = {"analyze_bos_structure": analyze_bos_structure, "bos_pivot_list": bos_pivot_list, "pd": pd, "np": np, "bisect": bisect}
for node in ast.parse(src).body:
    names = {getattr(e, "id", None) for t in getattr(node, "targets", []) for e in getattr(t, "elts", [t])}
    if isinstance(node, ast.FunctionDef) or names & {"LEG_N", "SHARP_K"}:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = t_idx = {t: i for i, t in enumerate(T)}
O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))

trend, breaks, start = ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"])
bias = ns["bias_bar"](trend, breaks)
RULES = {"old: 1 ATR / 6c": (6, 1.0), "new: 2 ATR / 4c": (ns["SHARP_K"], ns["SHARP_ATR"])}
ZONES = {}
for name, (ns["SHARP_K"], ns["SHARP_ATR"]) in RULES.items():
    ZONES[name] = ns["supply_zones"](df, ns["LEG_N"], bias, start)

MIN_RR, FEE = 2, 0.05     # FEE in % per side


def run(zones, entry, target):
    rows, skipped = [], 0
    for z in zones:
        t0, t1, top, bot, sharp = z[:5]
        if not sharp or t1 is None:
            continue
        j, e = t_idx[t0], t_idx[t1]
        if H[e] < bot:                        # ended without a touch: no trade
            continue
        x = bot if entry == "zone" else (top + bot) / 2
        risk = top - x
        if risk <= 0:
            continue
        f = None
        for k in range(e, len(df)):
            if H[k] >= x:
                f = k
                break
            if (start[k] is not None and start[k] > t0) or bias[k] > -1:
                break                         # cancelled before a fill
        if f is None:
            continue
        lo = L[j + 1:f].min() if target == "drop low" else x - 2 * risk
        rr = (x - lo) / risk
        if rr < MIN_RR - 1e-9:
            skipped += 1
            continue
        stop_i = next((k for k in range(f, len(df)) if H[k] > top), None)
        last = stop_i if stop_i is not None else len(df) - 1
        hit = next((k for k in range(f + 1, last + 1) if L[k] <= lo and H[k] <= top), None)
        r = rr if hit is not None else (-1.0 if stop_i is not None else np.nan)
        rows.append({"time": df.index[f], "r": r, "risk_pct": risk / x * 100, "rr": rr, "zone": z})
    return pd.DataFrame(rows), skipped


def line(name, d, skipped=""):
    c = d.dropna(subset=["r"]) if len(d) else d
    if not len(c):
        return f"{name:<34} {len(d):>6} {skipped:>8}"
    n = c["r"] - 2 * FEE / c["risk_pct"]
    return (f"{name:<34} {len(d):>6} {skipped:>8} {c['risk_pct'].median():>6.2f}% {c['rr'].median():>7.1f}R "
            f"{(n > 0).mean() * 100:>5.0f}% {n.mean():>+8.2f} {n.sum():>+8.1f}R {d['r'].isna().sum():>5}")


red = {k: [z for z in v if z[4]] for k, v in ZONES.items()}
print(f"Data {df.index[0]:%Y-%m-%d} .. {df.index[-1]:%Y-%m-%d} ({len(df)} candles). Drawn red zones: "
      + ", ".join(f"{k} {len(v)}" for k, v in red.items()) + f". Fee {FEE}% per side.\n")
print(f"{'rule / entry / target':<34} {'trades':>6} {'skipped':>8} {'1R %':>7} {'tgt R':>8} {'win':>6} {'avg R':>8} "
      f"{'total R':>9} {'open':>5}")
for target in ("drop low", "2R"):
    for entry in ("zone", "mid"):
        for rule, zs in ZONES.items():
            d, sk = run(zs, entry, target)
            print(line(f"{rule} / {entry} / {target}", d, sk))
        print()
print("1R % = median stop distance; tgt R = median target in R; win / avg R / total R after fees, open trades left out;"
      "\nopen = still running at the end of the data.")
