"""Build the BTC Market Structure page: 4h candles, LTF swings, LTF trend and entry blocks.

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
TF, TFH, SWING = "4h", 4, 12
files = sorted(Path(REPO, "data").glob("BTC_4h_*_index_signal_*.csv"), key=lambda p: p.stat().st_mtime)
if not files:
    raise SystemExit("No BTC 4h index data in data/ - run research/download_data.py first")
sig = pd.read_csv(files[-1], index_col=0, parse_dates=True)   # newest download
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

# Entry blocks: an HL becomes active when it is confirmed in a bullish trend or a break up validates it
# (LH mirrored). The box runs until price trades back into it or a newer swing on that side replaces it.
boxes = []
for d, brk, vcol, lcol, conf, tag, want, tr in (
        (1, "bos_up", "valid_low_idx", "last_low_idx", "conf_low", "low_tag", "HL", "bullish"),
        (-1, "bos_down", "valid_high_idx", "last_high_idx", "conf_high", "high_tag", "LH", "bearish")):
    b, v, last = plan[brk].to_numpy(), plan[vcol].to_numpy(), plan[lcol].to_numpy()
    cf, tg = plan[conf].to_numpy(), plan[tag].to_numpy()
    act = {}   # swing candle -> (activation candle, how)
    for i in range(len(plan)):
        if cf[i] and tg[i] == want and trend[i] == tr and last[i] >= 0:
            act.setdefault(int(last[i]), (i, "confirmed"))
        if b[i] and v[i] >= 0:
            act.setdefault(int(v[i]), (i, "break"))
    for p, (i, src) in act.items():
        top, bot = h[p], l[p]
        end, touched = len(plan) - 1, False
        for j in range(i + 1, len(plan)):
            if (l[j] <= top) if d == 1 else (h[j] >= bot):
                end, touched = j, True
                break
            if last[j] != p:
                end = j
                break
        boxes.append([T[p], T[end], round(top, 1), round(bot, 1), d, touched, T[i], src])
# Order blocks: the origin of an impulse that leaves a fair value gap. Impulse = a bullish candle k whose
# neighbours leave a gap (low[k+1] - high[k-1]) of at least OB_GAP_ATR x ATR. The block is the full range of
# the lowest candle in the OB_LOOKBACK candles before the impulse (bearish mirrored). It is active once the
# gap is complete (candle k+1) and runs until price trades back into it or a new swing on that side confirms.
from structure import wilder_atr  # noqa: E402

OB_GAP_ATR, OB_LOOKBACK = 1.0, strat.n
atr = wilder_atr(sig, 14)
swing_boxes = {(b[4], b[0]) for b in boxes}
conf_l, conf_h = plan["conf_low"].to_numpy(), plan["conf_high"].to_numpy()
obs = {}
for k in range(OB_LOOKBACK, len(sig) - 1):
    if not atr[k] > 0:
        continue
    for d in (1, -1):
        gap = (l[k + 1] - h[k - 1]) if d == 1 else (l[k - 1] - h[k + 1])
        if (c[k] > o[k]) != (d == 1) or gap < OB_GAP_ATR * atr[k]:
            continue
        if trend[k + 1] != ("bullish" if d == 1 else "bearish"):   # only with the LTF trend
            continue
        win = range(k - OB_LOOKBACK, k)
        p = min(win, key=lambda i: l[i]) if d == 1 else max(win, key=lambda i: h[i])
        if (d, T[p]) in swing_boxes or (d, p) in obs:
            continue
        obs[(d, p)] = k + 1
for (d, p), a in obs.items():
    top, bot = h[p], l[p]
    new_swing = conf_l if d == 1 else conf_h
    end, touched = len(sig) - 1, False
    for j in range(a + 1, len(sig)):
        if (l[j] <= top) if d == 1 else (h[j] >= bot):
            end, touched = j, True
            break
        if new_swing[j] or trend[j] != ("bullish" if d == 1 else "bearish"):   # replaced, or trend turned
            end = j
            break
    boxes.append([T[p], T[end], round(top, 1), round(bot, 1), d, touched, T[a], "ob"])
boxes.sort()

# POI lines after each break up (and mirrored after each break down): prev high (the broken level), impulse start (latest 3-candle pivot low after
# the HL from which price never went lower until the break) and the HL. Each runs until the next break up
# (success) or break down (failed). pick = the POI nearest to the deepest pullback before a new high.
POI_K = 3
up_b, dn_b = plan["bos_up"].to_numpy(), plan["bos_down"].to_numpy()
lvl, hfrom = plan["bos_level"].to_numpy(), plan["bos_from_idx"].to_numpy()
vlow, vhigh = plan["valid_low_idx"].to_numpy(), plan["valid_high_idx"].to_numpy()
pois = []
for d in (1, -1):
    brk, other, vsw = (up_b, dn_b, vlow) if d == 1 else (dn_b, up_b, vhigh)
    ext = l if d == 1 else h                                                   # wick extreme of a swing
    body = (lambda x: min(o[x], c[x])) if d == 1 else (lambda x: max(o[x], c[x]))   # lines sit on the body
    deeper = (lambda a, b: a <= b) if d == 1 else (lambda a, b: a >= b)        # a is at or beyond b
    for i in range(len(sig)):
        if not brk[i] or vsw[i] < 0:
            continue
        P, Pi, Si = lvl[i], int(hfrom[i]), int(vsw[i])
        Ii = Si   # impulse start: latest K-pivot after the swing from which price never went beyond it until the break
        for x in range(i - 1, Si, -1):
            if x - POI_K < 0:
                break
            seg = ext[x - POI_K:min(x + POI_K + 1, i + 1)]
            if ext[x] == (seg.min() if d == 1 else seg.max()) and all(deeper(ext[x], v) for v in ext[x:i + 1]):
                Ii = x
                break
        end, ok = len(sig) - 1, None
        for j in range(i + 1, len(sig)):
            if brk[j] or other[j]:
                end, ok = j, bool(brk[j])
                break
        seg = (l if d == 1 else h)[i + 1:end + 1]
        pb = (seg.min() if d == 1 else seg.max()) if end > i else (l[i] if d == 1 else h[i])
        if ok is None:
            pick = "open"
        elif not ok:
            pick = "failed"
        elif (pb > P) if d == 1 else (pb < P):
            pick = "none"
        else:
            lv = {"ph": P, "hl": body(Si)} | ({"is": body(Ii)} if Ii != Si else {})
            pick = min(lv, key=lambda k: abs(pb - lv[k]))
        reached = bool(deeper(pb, body(Si)))   # pullback got to the HL / LH body line
        pois.append([T[i], T[end], round(P, 1), T[Pi], round(body(Ii), 1), T[Ii], round(body(Si), 1), T[Si], pick, d, reached])

# HL / LH entry trades (see hl_trades.py): entry ENTRY_ATR above the line, stop STOP_ATR under the HL wick,
# target = the high of the highest candle since the break (short: the low of the lowest), stop to entry after BE_AFTER_R
sys.path.insert(0, str(HERE))
from hl_trades import hl_trades  # noqa: E402

ENTRY_ATR, STOP_ATR = 0.0, 0.0
BE_AFTER_R = 2.0
from hl_trades import impulse_sweep_trades, sweep_trades  # noqa: E402

_tr, _lv = sweep_trades(sig, plan, atr)
trades = [[t["d"], T[t["fill"]], T[t["exit"]], round(t["entry"], 1), round(t["stop"], 1), round(t["target"], 1), t["r"],
           [[T[k], round(v, 1)] for k, v in t["steps"]], t.get("kind", "HL")] for t in _tr]
levels = [[v["d"], T[v["swing"]], T[v["end"]], round(v["entry"], 1), v["status"], v.get("kind", "HL")] for v in _lv]

data = {"tfh": TFH, "n": strat.n, "candles": candles, "ltf": ltf, "swings": swings, "boxes": boxes, "pois": pois,
        "trades": trades, "tradeCfg": [ENTRY_ATR, STOP_ATR], "levels": levels}
page = (HERE / "template.html").read_text(encoding="utf-8").replace(
    "__DATA__", json.dumps(data, separators=(",", ":")))
(HERE / "ms_full_chart.html").write_text(page, encoding="utf-8")
n_ob = sum(b[7] == "ob" for b in boxes)
print(f"{len(trades)} trades; {len(candles)} candles, {len(swings)} swings, {len(boxes) - n_ob} swing boxes, {n_ob} order blocks "
      f"({sum(b[5] for b in boxes if b[7] == 'ob')} touched), {len(page) / 1e6:.2f} MB")
