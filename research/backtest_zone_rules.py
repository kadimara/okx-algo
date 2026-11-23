"""Compare supply-zone rules of the chart (supply_zones in build_structure_chart.py) with the same short trades:
"old" = sharp means a low 1 ATR below the zone within 6 candles; "new" = very sharp, 2 ATR within 4 candles (as in the
build script). The zone is the range of the swing-high candle in both.

Red zones only (a sharp move down after the zone). The sell limit rests from the first touch of a drawn zone (known
live while the bias was bearish) until it fills or the zone is cancelled (bias no longer bearish, a newer leg high).
Entries: "zone" = the zone bottom, "mid" = the middle of the zone. Stop just above the zone top, so 1R = top - entry.
Targets: "drop low" = the low of the drop away from the zone before the fill (trades under MIN_RR R skipped, as in
backtest_supply_entries.py), "3R" = a fixed FIXED_R (3R) target.
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
    if isinstance(node, ast.FunctionDef) or names & {"LEG_N", "SHARP_K", "DRAW_GREY", "WIDE_ZONE", "LH_BIAS", "SUPPLY_MIN_PCT", "SUPPLY_MSS_OK", "COUNTER_MSS_ENDS", "STRUCT_MODE", "DEMAND_HL_ONLY"}:
        exec(compile(ast.Module([node], []), "build_structure_chart.py", "exec"), ns)

files = sorted(Path(REPO, "data").glob("BTC_1h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
df = pd.read_csv(files[-1], index_col=0, parse_dates=True)
ns["ts"] = ts = lambda t: int(pd.Timestamp(t).timestamp())
ns["T"] = T = [ts(t) for t in df.index]
ns["t_idx"] = t_idx = {t: i for i, t in enumerate(T)}
ns["DEMAND_HL_ONLY"] = ns["DEMAND_AFTER_HH"] = False   # this script compares all demand zones (split by HL itself)
O, H, L, C = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))

trend, breaks, start, start_low = ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"], low_start=True)
bias = np.array(ns["bias_bar"](trend, breaks))
ns["SUPPLY_MSS_OK"] = ns["DEMAND_MSS_OK"] = False      # the earlier sections: confirmed trend only (as before)
RULES = {"old: 1 ATR / 6c": (6, 1.0), "new: 2 ATR / 4c": (ns["SHARP_K"], ns["SHARP_ATR"])}
ZONES = {}
for name, (ns["SHARP_K"], ns["SHARP_ATR"]) in RULES.items():
    ns["LH_BIAS"] = False
    ZONES[name] = ns["supply_zones"](df, ns["LEG_N"], bias, start)
ns["LH_BIAS"] = True
ZONES["new + LH needs bias"] = ns["supply_zones"](df, ns["LEG_N"], bias, start)
# Shorts and longs with the confirmed trend only or also in a pending MSS their way (per side)
# (SUPPLY_MSS_OK, DEMAND_MSS_OK, COUNTER_MSS_ENDS)
VARIANTS = {"Confirmed trend only": (False, False, True), "Confirmed trend or a pending MSS their way": (True, True, True),
            "MSS their way allowed, a counter MSS does not end the zone (only a confirmed opposite trend)": (True, True, False),
            "Chart default: shorts also in a bearish MSS, longs only in a confirmed uptrend": (True, False, True)}
BOTH = {}
for name, (ns["SUPPLY_MSS_OK"], ns["DEMAND_MSS_OK"], ns["COUNTER_MSS_ENDS"]) in VARIANTS.items():
    BOTH[name] = (ns["supply_zones"](df, ns["LEG_N"], bias, start), ns["demand_zones"](df, ns["LEG_N"], bias, start_low))
ns["SUPPLY_MSS_OK"], ns["DEMAND_MSS_OK"], ns["COUNTER_MSS_ENDS"] = False, False, True

# Longs run through the same short logic on mirrored prices: high <-> -low, open / close negated, bias negated, and the
# newer-leg-low start; a demand zone [top, bottom] becomes [-bottom, -top].
SIDES = {"short": (H, L, O, C, bias, start), "long": (-L, -H, -O, -C, -bias, start_low)}

MIN_RR, FEE, FIXED_R = 2, 0.05, 3     # FEE in % per side
# Entry plan "plan": sell at the zone bottom, or at the body high of the zone candle when the zone-bottom stop would be
# wider than WIDE_STOP %; stop at the zone top but at least MIN_STOP % from the entry. Sized to risk RISK % of the
# account per trade (leverage = RISK / stop %).
MIN_STOP, WIDE_STOP, RISK = 0.5, 1.0, 1.0
# Break-even: once a candle's low is BE R in our favour, the stop moves to the entry from the next candle on
# (None = never). The fill candle does not count (its low may have come before the fill).
BE_TRIGGERS = (None, 1.0, 1.5, 2.0)


def run(zones, entry, target, be=None, side="short", lim=1, keep_mss=False):
    H, L, O, C, bias, start = SIDES[side]
    rows, skipped = [], 0
    for z in zones:
        t0, t1, top, bot, sharp = z[:5]
        if side == "long":
            top, bot = -bot, -top
        if not sharp or t1 is None:
            continue
        j, e = t_idx[t0], t_idx[t1]
        if H[e] < bot:                        # ended without a touch: no trade
            continue
        x = bot if entry == "zone" else (top + bot) / 2
        if entry == "plan":
            x = bot if (top - bot) / abs(bot) * 100 <= WIDE_STOP else max(O[j], C[j])
        risk = top - x
        if entry == "plan":
            risk = max(risk, MIN_STOP / 100 * abs(x))
        if risk <= 0:
            continue
        stop = x + risk
        f = None
        for k in range(e, len(df)):
            if H[k] >= x:
                f = k
                break
            if (start[k] is not None and start[k] > t0) or (bias[k] >= 1 if keep_mss else bias[k] > -lim):
                break                         # cancelled before a fill
        if f is None:
            continue
        lo = L[j + 1:f].min() if target == "drop low" else x - FIXED_R * risk
        rr = (x - lo) / risk
        if rr < MIN_RR - 1e-9:
            skipped += 1
            continue
        r, cur_stop, moved = np.nan, stop, False
        for k in range(f, len(df)):
            if H[k] >= cur_stop:              # stop first: a candle that hits both is a loss (or break-even)
                r = (x - cur_stop) / risk
                break
            if k > f and L[k] <= lo:
                r = rr
                break
            if be is not None and k > f and L[k] <= x - be * risk:
                cur_stop, moved = x, True     # from the next candle on
        rows.append({"time": df.index[f], "r": r, "risk_pct": risk / abs(x) * 100, "rr": rr, "zone": z,
                     "be": moved and r == 0})
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
for target in ("drop low", f"{FIXED_R}R"):
    for entry in ("zone", "mid"):
        for rule, zs in ZONES.items():
            d, sk = run(zs, entry, target)
            print(line(f"{rule} / {entry} / {target}", d, sk))
        print()
print("1R % = median stop distance; tgt R = median target in R; win / avg R / total R after fees, open trades left out;"
      "\nopen = still running at the end of the data.")

# Stop distance (entry to stop, % of the entry) of the trades of the current rule: tight stops get hit by noise and
# pay relatively more fees, wide stops need a big move to reach the target.
STOP_BINS = (0, 0.3, 0.5, 1.0, 2.0, np.inf)
cur = list(ZONES)[-1]
print(f"\nBy stop distance, {cur}:")
print(f"{'entry / target / stop':<34} {'trades':>6} {'':>8} {'1R %':>7} {'tgt R':>8} {'win':>6} {'avg R':>8} {'total R':>9} {'open':>5}")
for target in ("drop low", f"{FIXED_R}R"):
    for entry in ("zone", "mid"):
        d, _ = run(ZONES[cur], entry, target)
        for lo_b, hi_b in zip(STOP_BINS, STOP_BINS[1:]):
            g = d[(d["risk_pct"] >= lo_b) & (d["risk_pct"] < hi_b)] if len(d) else d
            lab = f"< {hi_b}%" if lo_b == 0 else f">= {lo_b}%" if hi_b == np.inf else f"{lo_b}-{hi_b}%"
            print(line(f"{entry} / {target} / {lab}", g))
        print()


# The entry plan on the current rule, in % of the account (RISK % per trade, fees included)
for cur in list(ZONES)[-2:]:
    print(f"\nEntry plan on {cur}: entry at the zone bottom, or the body high when that stop is over {WIDE_STOP}%; stop at the "
          f"zone top, at least {MIN_STOP}%; {RISK}% of the account at risk per trade.")
    print(f"{'target':>10} {'break-even':>11} {'trades':>6} {'win':>5} {'at BE':>6} {'avg R':>7} {'return':>8} "
          f"{'compounded':>11} {'max DD':>7} {'worst run':>10}   per year")
    for target in ("drop low", f"{FIXED_R}R"):
        for be in BE_TRIGGERS:
            d, sk = run(ZONES[cur], "plan", target, be)
            c = d.dropna(subset=["r"])
            net = c["r"] - 2 * FEE / c["risk_pct"]
            eq = (1 + RISK / 100 * net).cumprod()
            dd = (1 - eq / eq.cummax()).max() * 100
            losing = (net < 0).astype(int)
            worst = int(losing.groupby((losing == 0).cumsum()).sum().max())
            years = "  ".join(f"{y}: {g.sum() * RISK:+.1f}%" for y, g in net.groupby(c["time"].dt.year))
            print(f"{target:>10} {'off' if be is None else f'at {be}R':>11} {len(c):>6} {(net > 0).mean() * 100:>4.0f}% "
                  f"{int(c['be'].sum()):>6} {net.mean():>+7.2f} {net.sum() * RISK:>+7.1f}% {(eq.iloc[-1] - 1) * 100:>+10.1f}% "
                  f"{dd:>6.1f}% {worst:>10}   {years}")
        print()
    print("at BE = trades closed at the entry after the stop moved (a small loss: the fees); worst run = most losing trades in a row.")

    # Where the losses come from: the entry plan (no break-even) split by zone type. Zones at a LH (the top of a pullback
    # below the previous leg high) are shown whatever the bias; normal zones need a bearish live bias.
    print("\nEntry plan by zone type (no break-even):")
    print(f"{'target':>10} {'zones':>16} {'trades':>6} {'win':>5} {'avg R':>7} {'return':>8} {'max DD':>7} {'worst run':>10}   per year")
    for target in ("drop low", f"{FIXED_R}R"):
        d, _ = run(ZONES[cur], "plan", target)
        d = d.dropna(subset=["r"])
        d["net"] = d["r"] - 2 * FEE / d["risk_pct"]
        d["lh"] = [z[5] for z in d["zone"]]
        for name, g in (("all", d), ("at a LH", d[d["lh"] == 1]), ("normal (bias)", d[d["lh"] == 0])):
            eq = (1 + RISK / 100 * g["net"]).cumprod()
            dd = (1 - eq / eq.cummax()).max() * 100
            losing = (g["net"] < 0).astype(int)
            worst = int(losing.groupby((losing == 0).cumsum()).sum().max())
            years = "  ".join(f"{y}: {x.sum() * RISK:+.1f}%" for y, x in g["net"].groupby(g["time"].dt.year))
            print(f"{target:>10} {name:>16} {len(g):>6} {(g['net'] > 0).mean() * 100:>4.0f}% {g['net'].mean():>+7.2f} "
                  f"{g['net'].sum() * RISK:>+7.1f}% {dd:>6.1f}% {worst:>10}   {years}")
        print()


# Demand zones: longs with the same entry plan (entry at the zone top, stop at the zone bottom, at least MIN_STOP %),
# no break-even, and the shorts and longs together (trades in time order, RISK % each).
def stats(net, times):
    eq = (1 + RISK / 100 * net).cumprod()
    dd = (1 - eq / eq.cummax()).max() * 100
    losing = (net < 0).astype(int)
    worst = int(losing.groupby((losing == 0).cumsum()).sum().max())
    years = "  ".join(f"{y}: {x.sum() * RISK:+.1f}%" for y, x in net.groupby(times.dt.year))
    return (f"{len(net):>6} {(net > 0).mean() * 100:>4.0f}% {net.mean():>+7.2f} {net.sum() * RISK:>+7.1f}% "
            f"{(eq.iloc[-1] - 1) * 100:>+10.1f}% {dd:>6.1f}% {worst:>10}   {years}")


for vname, (SUP, DEMAND) in BOTH.items():
  smss, dmss, counter = VARIANTS[vname]
  lims = {"short": 0.5 if smss else 1, "long": 0.5 if dmss else 1}
  print(f"\n{vname}: {len(SUP)} supply / "
        f"{len(DEMAND)} demand zones (demand rise >= {ns['DEMAND_MIN_PCT']}%), entry plan:")
  print(f"{'target':>10} {'trades':>23} {'win':>5} {'avg R':>7} {'return':>8} {'compounded':>11} {'max DD':>7} {'worst run':>10}   per year")
  for target in ("drop low", f"{FIXED_R}R"):
      parts = {}
      for side, zs in (("short", SUP), ("long", DEMAND)):
          d, _ = run(zs, "plan", target, side=side, lim=lims[side], keep_mss=not counter)
          d = d.dropna(subset=["r"]).assign(net=lambda x: x["r"] - 2 * FEE / x["risk_pct"])
          d["hl"] = [z[5] for z in d["zone"]]
          parts[side] = d
      lo = parts["long"]
      both = pd.concat(parts.values()).sort_values("time")
      for name, g in (("longs", lo), ("  at a HL", lo[lo["hl"] == 1]), ("  normal", lo[lo["hl"] == 0]),
                      ("shorts", parts["short"]), ("shorts + longs", both)):
          print(f"{target:>10} {name:>16} " + stats(g["net"].reset_index(drop=True), g["time"].reset_index(drop=True)))
      print()


# Zone swing size: zones at 8-candle swings (the chart) vs 12-candle swings (12h on 1h candles). The bias, MSS and leg
# structure stay on the LEG_N swings; only the swing highs / lows the zones come from change.
print("\nZone swing size (bias and structure unchanged), entry plan, target drop low:")
print(f"{'swing':>8} {'bias':>12} {'zones':>14} {'trades':>12} {'win':>5} {'avg R':>7} {'return':>8} {'compounded':>11} {'max DD':>7} {'worst run':>10}   per year")
for zn in (ns["LEG_N"], 12):
    for bname, mss in (("confirmed", False), ("MSS too", True)):
        ns["SUPPLY_MSS_OK"] = ns["DEMAND_MSS_OK"] = mss
        SUP, DEM = ns["supply_zones"](df, zn, bias, start), ns["demand_zones"](df, zn, bias, start_low)
        lim = 0.5 if mss else 1
        parts = {}
        for side, zs in (("short", SUP), ("long", DEM)):
            d, _ = run(zs, "plan", "drop low", side=side, lim=lim)
            d = d.dropna(subset=["r"]).assign(net=lambda x: x["r"] - 2 * FEE / x["risk_pct"])
            d["hl"] = [z[5] for z in d["zone"]]
            parts[side] = d
        lo = parts["long"]
        both = pd.concat(parts.values()).sort_values("time")
        for name, g in (("shorts", parts["short"]), ("longs at a HL", lo[lo["hl"] == 1]), ("longs normal", lo[lo["hl"] == 0]),
                        ("all", both)):
            zc = f"{len(SUP)}s/{len(DEM)}d" if name == "all" else ""
            print(f"{zn:>7}c {bname:>12} {zc:>14} {name:>12} " + (stats(g["net"].reset_index(drop=True), g["time"].reset_index(drop=True)) if len(g) else "     0"))
        print()
ns["SUPPLY_MSS_OK"] = ns["DEMAND_MSS_OK"] = False



# Structure mode: the bias, MSS and newer-leg starts from breaks of the structural levels (the chart) vs breaks of the
# most recent swing high / low ("swing"). Zones and trades with the same rules on each.
print("\nStructure mode (bias / MSS from structural levels vs the most recent swings), entry plan, target drop low:")
print(f"{'mode':>11} {'bias':>10} {'zones':>9} {'group':>14} {'trades':>6} {'win':>5} {'avg R':>7} {'return':>8} {'compounded':>11} {'max DD':>7} {'worst run':>10}   per year")
MODES = {"structural": (bias, start, start_low)}
ns["STRUCT_MODE"] = "swing"
tr2, br2, st2, stl2 = ns["live_structure"](df, ns["LEG_N"], ns["MIN_LEG"], low_start=True)
MODES["swing"] = (np.array(ns["bias_bar"](tr2, br2)), st2, stl2)
ns["STRUCT_MODE"] = "structural"
for mode, (b_, s_, sl_) in MODES.items():
    flips = int((np.diff(np.sign(b_)) != 0).sum())
    print(f"{mode:>11}: {flips} bias changes, {np.mean(b_ >= 1) * 100:.0f}% bullish / {np.mean(b_ <= -1) * 100:.0f}% bearish confirmed, "
          f"{np.mean(np.abs(b_) == 0.5) * 100:.0f}% pending MSS")
    SIDES["short"], SIDES["long"] = (H, L, O, C, b_, s_), (-L, -H, -O, -C, -b_, sl_)
    for bname, mss in (("confirmed", False), ("MSS too", True)):
        ns["SUPPLY_MSS_OK"] = ns["DEMAND_MSS_OK"] = mss
        SUP, DEM = ns["supply_zones"](df, ns["LEG_N"], b_, s_), ns["demand_zones"](df, ns["LEG_N"], b_, sl_)
        parts = {}
        for side, zs in (("short", SUP), ("long", DEM)):
            d, _ = run(zs, "plan", "drop low", side=side, lim=0.5 if mss else 1)
            d = d.dropna(subset=["r"]).assign(net=lambda x: x["r"] - 2 * FEE / x["risk_pct"])
            d["hl"] = [z[5] for z in d["zone"]]
            parts[side] = d
        lo = parts["long"]
        for name, g in (("shorts", parts["short"]), ("longs at a HL", lo[lo["hl"] == 1]), ("longs normal", lo[lo["hl"] == 0]),
                        ("all", pd.concat(parts.values()).sort_values("time"))):
            zc = f"{len(SUP)}s/{len(DEM)}d" if name == "all" else ""
            print(f"{mode:>11} {bname:>10} {zc:>9} {name:>14} " + (stats(g["net"].reset_index(drop=True), g["time"].reset_index(drop=True)) if len(g) else "     0"))
        print()
SIDES["short"], SIDES["long"] = (H, L, O, C, bias, start), (-L, -H, -O, -C, -bias, start_low)
ns["SUPPLY_MSS_OK"] = ns["DEMAND_MSS_OK"] = False


# Pullback zones: a supply zone at a local HH with no LL in between (swings L0 < L1 and H_prev < H at the zone: the
# pullback up inside a bearish structure); demand mirrored (a local LL with no HH: H0 > H1 and L_prev > L). Checked on the
# 8-candle swings known when the zone's swing is confirmed (a swing replaced by a more extreme one dropped).
EVS = ns["swing_events"](df, ns["LEG_N"])
EV_C = [t_idx[p[3]] for p in EVS]


def pattern(z, side):
    """The swings before the zone's swing, as known at its confirmation: (x0, prev, x1, cur) or None. Short: L0, H_prev,
    L1, H (the zone); long: H0, L_prev, H1, L."""
    t = z[0]
    j = next((k for k, p in enumerate(EVS) if p[0] == t and p[1] == ("h" if side == "short" else "l")), None)
    if j is None:
        return False
    known = EVS[:bisect.bisect_right(EV_C, EV_C[j])][-40:]
    known = [p for k, p in enumerate(known) if k == len(known) - 1 or known[k + 1][1] != p[1]]
    pos = next((k for k, p in enumerate(known) if p[0] == t), None)
    if pos is None or pos < 3:
        return None
    return known[pos - 3:pos + 1]


def pullback(z, side):
    """Short: the zone is a local HH with no LL in between (L1 > L0 and H > H_prev); long mirrored."""
    q = pattern(z, side)
    if q is None:
        return False
    x0, prev, x1, cur = q
    return cur[2] > prev[2] and x1[2] > x0[2] if side == "short" else cur[2] < prev[2] and x1[2] < x0[2]


def after_break(z, side):
    """Short: a LL came before the zone (the swing low between the previous high and the zone is below the low before
    that: L1 < L0); long: a HH came before it (H1 > H0)."""
    q = pattern(z, side)
    if q is None:
        return False
    x0, prev, x1, cur = q
    return x1[2] < x0[2] if side == "short" else x1[2] > x0[2]


print("\nPullback zones (short: a local HH with no LL in between; long: a local LL with no HH), entry plan, target drop low:")
print(f"{'bias':>10} {'zones':>18} {'group':>14} {'trades':>6} {'win':>5} {'avg R':>7} {'return':>8} {'compounded':>11} {'max DD':>7} {'worst run':>10}   per year")
for bname, mss in (("confirmed", False), ("MSS too", True)):
    ns["SUPPLY_MSS_OK"] = ns["DEMAND_MSS_OK"] = mss
    SUP, DEM = ns["supply_zones"](df, ns["LEG_N"], bias, start), ns["demand_zones"](df, ns["LEG_N"], bias, start_low)
    parts = {}
    for side, zs in (("short", SUP), ("long", DEM)):
        d, _ = run(zs, "plan", "drop low", side=side, lim=0.5 if mss else 1)
        d = d.dropna(subset=["r"]).assign(net=lambda x: x["r"] - 2 * FEE / x["risk_pct"])
        d["hl"] = [z[5] for z in d["zone"]]
        d["pb"] = [pullback(z, side) for z in d["zone"]]
        d["ab"] = [after_break(z, side) for z in d["zone"]]
        parts[side] = d
    for fname, keep in (("all", lambda d: d), ("only pullback", lambda d: d[d["pb"]]), ("no pullback", lambda d: d[~d["pb"]]),
                          ("only after LL / HH", lambda d: d[d["ab"]])):
        sh, lo = keep(parts["short"]), keep(parts["long"])
        for name, g in (("shorts", sh), ("longs at a HL", lo[lo["hl"] == 1]), ("longs normal", lo[lo["hl"] == 0]),
                        ("all", pd.concat([sh, lo]).sort_values("time"))):
            print(f"{bname:>10} {fname:>18} {name:>14} " + (stats(g["net"].reset_index(drop=True), g["time"].reset_index(drop=True)) if len(g) else "     0"))
        print()
ns["SUPPLY_MSS_OK"] = ns["DEMAND_MSS_OK"] = False
