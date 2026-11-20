"""HL / LH entry trades.

After each break up (long; break down = short, mirrored):
  line   = body low of the HL behind the break (LH: body high)
  entry  = line + ENTRY_ATR x ATR      (limit, short: line - ...)
  stop   = HL wick low - STOP_ATR x ATR (short: LH wick high + ...)
  target = the LOW of the highest candle made since the break, before the fill
           (short: the HIGH of the lowest candle)
The order is live from the candle after the break until the next break (either way).
Stop and target in the same candle count as a stop. Fees FEE (round trip) are charged in R.
"""
import numpy as np

FEE = 0.001


def hl_trades(sig, st, atr, entry_atr=0.0, stop_atr=0.0, trail=False, trail_n=0, trail_after_r=0.0,
              target_at_extreme=False, be_after_r=0.0, early_activation=False, near_atr=0.0, return_levels=False,
              target_body=False, target_r=0.0):
    """trail=True: no fixed target. Every new break in the trade direction moves the stop to the wick
    of the swing behind that break (HL for longs, LH for shorts) - stop_atr, if that is tighter.
    trail_n > 0: candle trail instead - after each closed candle the stop moves to the lowest low of the
    last trail_n candles (shorts: highest high), once price has gone trail_after_r R in profit.
    target_at_extreme: target = the HIGH of the highest candle since the break (short: the LOW of the lowest)
    instead of that candle's low (high). be_after_r > 0: stop to the entry once price went that many R in profit.
    early_activation: the order also goes live when the swing is confirmed as HL in a bullish trend
    (LH in a bearish one), not only after a break validates it.
    near_atr > 0: a level is removed when price came within near_atr x ATR of the entry without filling
    and then responded (a later candle made a higher high than that near-touch candle; shorts mirrored).
    target_body: target = the BODY high of the swing-high candle the pullback came from (the HH / LH; for an
    early entry the last swing high, otherwise the highest candle since the HL), shorts: body low of the LL / HL.
    target_r > 0: cap the target at target_r x the risk (take profit sooner).
    return_levels: also return every live order as a level: dict(d, swing, start, end, entry, status)
    with status filled / responded / expired.
    The trade only ends at the stop. Each trade gets 'steps': [(candle, stop)] for drawing."""
    o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    up, dn = st["bos_up"].to_numpy(), st["bos_down"].to_numpy()
    vlo, vhi = st["valid_low_idx"].to_numpy(), st["valid_high_idx"].to_numpy()
    trend = st["trend"].to_numpy()
    out, levels = [], []
    for d in (1, -1):
        brk, oth, vsw = (up, dn, vlo) if d == 1 else (dn, up, vhi)
        conf, tag, last = ((st["conf_low"], st["low_tag"], st["last_low_idx"]) if d == 1
                           else (st["conf_high"], st["high_tag"], st["last_high_idx"]))
        conf, tag, last = conf.to_numpy(), tag.to_numpy(), last.to_numpy()
        want, tr = ("HL", "bullish") if d == 1 else ("LH", "bearish")
        # activation: the swing is confirmed as HL in a bullish trend (LH / bearish), or a break validates it
        act = {}
        for i in range(len(c)):
            if early_activation and conf[i] and tag[i] == want and trend[i] == tr and last[i] >= 0:
                act.setdefault(int(last[i]), (i, "confirmed"))
            if brk[i] and vsw[i] >= 0:
                act.setdefault(int(vsw[i]), (i, "break"))
        for s, (a, how) in act.items():
            if not atr[a] > 0:
                continue
            line = min(o[s], c[s]) if d == 1 else max(o[s], c[s])
            wick = l[s] if d == 1 else h[s]
            entry, stop = line + d * entry_atr * atr[a], wick - d * stop_atr * atr[a]
            # live until the structure breaks against it, or the next break after the one that validates it
            vb = next((j for j in range(a, len(c)) if brk[j] and vsw[j] == s), None)
            end_oth = next((j for j in range(a + 1, len(c)) if oth[j]), len(c) - 1)
            end_brk = next((j for j in range(vb + 1, len(c)) if brk[j] or oth[j]), len(c) - 1) if vb is not None else len(c) - 1
            end = min(end_oth, end_brk)
            ext, fill = a, None
            for j in range(s + 1, a + 1):              # extreme made since the swing (the high it pulls back from)
                if (h[j] > h[ext]) if d == 1 else (l[j] < l[ext]):
                    ext = j
            near, gone = None, None
            for j in range(a + 1, end + 1):
                if (l[j] <= entry) if d == 1 else (h[j] >= entry):
                    fill = j
                    break
                if near is not None and ((h[j] > h[near]) if d == 1 else (l[j] < l[near])):
                    gone = j                        # price came close and responded: level used up
                    break
                if near_atr and near is None and d * ((l[j] if d == 1 else h[j]) - entry) <= near_atr * atr[a]:
                    near = j
                if (h[j] > h[ext]) if d == 1 else (l[j] < l[ext]):
                    ext = j
            levels.append(dict(d=d, swing=s, start=a, end=fill if fill is not None else (gone if gone is not None else end),
                               entry=entry, status="filled" if fill is not None else ("responded" if gone is not None else "expired")))
            if fill is None:
                continue
            target = (h[ext] if d == 1 else l[ext]) if target_at_extreme else (l[ext] if d == 1 else h[ext])
            if how == "confirmed":   # early entry: aim for the swing high (HH) the pullback came from (short: the LL)
                swing_px = st["last_high"].iat[a] if d == 1 else st["last_low"].iat[a]
                if swing_px == swing_px:
                    target = max(target, swing_px) if d == 1 else min(target, swing_px)
            if target_body:
                cand = ext
                if how == "confirmed":
                    li = int((st["last_high_idx"] if d == 1 else st["last_low_idx"]).iat[a])
                    if li >= 0 and ((h[li] > h[cand]) if d == 1 else (l[li] < l[cand])):
                        cand = li
                target = max(o[cand], c[cand]) if d == 1 else min(o[cand], c[cand])
            if target_r and d * (target - entry) > target_r * abs(entry - stop):
                target = entry + d * target_r * abs(entry - stop)
            if not trail and d * (target - entry) <= 0:
                continue
            risk = abs(entry - stop)
            res, xi, cur, steps, best = None, None, stop, [(fill, stop)], entry
            for k in range(fill, len(c)):
                if (l[k] <= cur) if d == 1 else (h[k] >= cur):
                    res, xi = d * (cur - entry) / risk, k
                    break
                if not trail and k > fill and ((h[k] >= target) if d == 1 else (l[k] <= target)):
                    res, xi = abs(target - entry) / risk, k
                    break
                if be_after_r and d * (cur - entry) < 0 and d * ((h[k] if d == 1 else l[k]) - entry) >= be_after_r * risk:
                    cur = entry                     # break-even from the next candle on
                    steps.append((k, cur))
                if trail and trail_n:
                    best = max(best, h[k]) if d == 1 else min(best, l[k])
                    if d * (best - entry) >= trail_after_r * risk:
                        lo = k - trail_n + 1
                        new = l[max(lo, 0):k + 1].min() if d == 1 else h[max(lo, 0):k + 1].max()
                        if d * (new - cur) > 0:
                            cur = new
                            steps.append((k, cur))
                    continue
                if trail and k > fill and brk[k] and vsw[k] >= 0:
                    sw = int(vsw[k])
                    new = (l[sw] if d == 1 else h[sw]) - d * stop_atr * atr[k]
                    if d * (new - cur) > 0:
                        cur = new
                        steps.append((k, cur))
            if res is None:
                res, xi = None, len(c) - 1          # still open
            r = None if res is None else round(res - FEE * entry / risk, 2)
            exit_px = cur if (trail or (res is not None and res <= 0)) else target
            out.append(dict(how=how, d=d, fill=fill, exit=xi, entry=entry, stop=stop, target=None if trail else target,
                            exit_px=exit_px, r=r, swing=s, steps=steps))
    out = sorted(out, key=lambda t: t["fill"])
    return (out, levels) if return_levels else out


def sweep_trades(sig, st, atr, stop_mode="lower", stop_buf=0.0, target_mode="leg"):
    """Sweep entries at the HL / LH line.

    The order level goes live as in hl_trades(early_activation=True): when the HL is confirmed in a bullish
    trend or a break validates it (shorts: LH, bearish). The first candle that reaches the line must sweep it:
    wick through the line and close back above it (short: back below). Entry at that candle's close, stop under
    the lower of the sweep wick and the HL wick, target = the HH the pullback came from (early entry) or the
    highest high since the HL. A touch that closes through the line cancels the level.
    stop_mode "lower": stop beyond the lower of the sweep wick and the HL wick; "sweep": beyond the sweep
    candle only. stop_buf: extra distance in ATR.
    target_mode "leg": the HH the pullback came from / highest high since the HL; "current": the current
    (most recent) swing high at entry, internal swings included, if it is above the entry - else "leg".
    Returns (trades, levels) in the same format as hl_trades(return_levels=True)."""
    o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    up, dn, trend = st["bos_up"].to_numpy(), st["bos_down"].to_numpy(), st["trend"].to_numpy()
    out, levels = [], []
    for d in (1, -1):
        brk, oth = (up, dn) if d == 1 else (dn, up)
        vsw = (st["valid_low_idx"] if d == 1 else st["valid_high_idx"]).to_numpy()
        conf = (st["conf_low"] if d == 1 else st["conf_high"]).to_numpy()
        tag = (st["low_tag"] if d == 1 else st["high_tag"]).to_numpy()
        last = (st["last_low_idx"] if d == 1 else st["last_high_idx"]).to_numpy()
        lastx = (st["last_high"] if d == 1 else st["last_low"]).to_numpy()
        want, tr = ("HL", "bullish") if d == 1 else ("LH", "bearish")
        act = {}
        for i in range(len(c)):
            if conf[i] and tag[i] == want and trend[i] == tr and last[i] >= 0:
                act.setdefault(int(last[i]), (i, "confirmed"))
            if brk[i] and vsw[i] >= 0:
                act.setdefault(int(vsw[i]), (i, "break"))
        for s, (a, how) in act.items():
            if not atr[a] > 0:
                continue
            line = min(o[s], c[s]) if d == 1 else max(o[s], c[s])
            wick = l[s] if d == 1 else h[s]
            vb = next((j for j in range(a, len(c)) if brk[j] and vsw[j] == s), None)
            end = min(next((j for j in range(a + 1, len(c)) if oth[j]), len(c) - 1),
                      next((j for j in range(vb + 1, len(c)) if brk[j] or oth[j]), len(c) - 1) if vb is not None else len(c) - 1)
            ext = a
            for j in range(s + 1, a + 1):
                if (h[j] > h[ext]) if d == 1 else (l[j] < l[ext]):
                    ext = j
            touch = None
            for j in range(a + 1, end + 1):
                if (l[j] <= line) if d == 1 else (h[j] >= line):
                    touch = j
                    break
                if (h[j] > h[ext]) if d == 1 else (l[j] < l[ext]):
                    ext = j
            swept = touch is not None and d * (c[touch] - line) > 0
            levels.append(dict(d=d, swing=s, start=a, end=touch if touch is not None else end, entry=line,
                               status="filled" if swept else ("closed through" if touch is not None else "expired")))
            if not swept:
                continue
            entry = c[touch]
            sw_ext = l[touch] if d == 1 else h[touch]
            base = sw_ext if stop_mode == "sweep" else (min(sw_ext, wick) if d == 1 else max(sw_ext, wick))
            stop = base - d * stop_buf * atr[touch]
            target = h[ext] if d == 1 else l[ext]
            if how == "confirmed" and lastx[a] == lastx[a]:
                target = max(target, lastx[a]) if d == 1 else min(target, lastx[a])
            if target_mode == "current":
                cur_x = (st["last_high"] if d == 1 else st["last_low"]).iat[touch]
                if cur_x == cur_x and d * (cur_x - entry) > 0:
                    target = cur_x
            risk = abs(entry - stop)
            if d * (target - entry) <= 0 or risk <= 0:
                continue
            res, xi = None, len(c) - 1
            for k in range(touch + 1, len(c)):
                if (l[k] <= stop) if d == 1 else (h[k] >= stop):
                    res, xi = -1.0, k
                    break
                if (h[k] >= target) if d == 1 else (l[k] <= target):
                    res, xi = abs(target - entry) / risk, k
                    break
            r = None if res is None else round(res - FEE * entry / risk, 2)
            out.append(dict(how=how, d=d, fill=touch, exit=xi, entry=entry, stop=stop, target=target,
                            r=r, swing=s, steps=[(touch, stop)]))
    return sorted(out, key=lambda t: t["fill"]), levels


def impulse_sweep_trades(sig, st, atr, stop_at="impulse", k_piv=3, wait=False):
    """Sweep entries at the start of the impulse (IS) that broke the high.

    After each break up: IS = the latest k_piv-candle pivot low between the HL and the break from which price
    never went lower until the break (skipped when it is the HL candle itself). Line = its body low, live from
    the candle after the break until the next break either way. The first candle that reaches the line must
    wick through and close back above -> entry at that close. Stop: under the lower of the sweep wick and the
    IS wick (stop_at="impulse") or the HL wick (stop_at="hl"); stop_at="is_candle": exactly under the IS candle low. Target = the highest high since the break.
    wait=True: do not require the first touch to sweep - wait for any candle that wicks through the line and
    closes back above it, as long as no candle has closed below the IS candle low (short: above its high).
    Shorts mirrored. Returns (trades, levels) like sweep_trades; each trade has kind="IS"."""
    o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    up, dn = st["bos_up"].to_numpy(), st["bos_down"].to_numpy()
    out, levels = [], []
    for d in (1, -1):
        brk, oth = (up, dn) if d == 1 else (dn, up)
        vsw = (st["valid_low_idx"] if d == 1 else st["valid_high_idx"]).to_numpy()
        ext_arr = l if d == 1 else h
        for i in np.where(brk)[0]:
            s = int(vsw[i])
            if s < 0 or not atr[i] > 0:
                continue
            ii = s
            for x in range(i - 1, s, -1):
                if x - k_piv < 0:
                    break
                seg = ext_arr[x - k_piv:min(x + k_piv + 1, i + 1)]
                if ext_arr[x] == (seg.min() if d == 1 else seg.max()) and \
                        all((ext_arr[x] <= v) if d == 1 else (ext_arr[x] >= v) for v in ext_arr[x:i + 1]):
                    ii = x
                    break
            if ii == s:
                continue
            line = min(o[ii], c[ii]) if d == 1 else max(o[ii], c[ii])
            end = next((j for j in range(i + 1, len(c)) if brk[j] or oth[j]), len(c) - 1)
            ext, touch, first = i, None, None
            for j in range(i + 1, end + 1):
                reach = (l[j] <= line) if d == 1 else (h[j] >= line)
                if reach and first is None:
                    first = j
                if reach and (not wait or d * (c[j] - line) > 0):
                    touch = j
                    break
                if wait and first is not None and ((c[j] < l[ii]) if d == 1 else (c[j] > h[ii])):
                    break                                   # closed beyond the IS candle: level gone
                if (h[j] > h[ext]) if d == 1 else (l[j] < l[ext]):
                    ext = j
            if wait and touch is None and first is not None:
                touch = first                               # for the level line: reached but never swept
            swept = touch is not None and d * (c[touch] - line) > 0 and ((l[touch] <= line) if d == 1 else (h[touch] >= line))
            levels.append(dict(d=d, swing=ii, start=i, end=touch if touch is not None else end, entry=line, kind="IS",
                               status="filled" if swept else ("closed through" if touch is not None else "expired")))
            if not swept:
                continue
            entry = c[touch]
            anchor = (l[ii] if d == 1 else h[ii]) if stop_at == "impulse" else (l[s] if d == 1 else h[s])
            if stop_at == "is_candle":     # exactly under the IS candle low (short: above its high)
                stop = l[ii] if d == 1 else h[ii]
                if d * (entry - stop) <= 0:
                    continue
            else:
                stop = min(l[touch], anchor) if d == 1 else max(h[touch], anchor)
            target = h[ext] if d == 1 else l[ext]
            risk = abs(entry - stop)
            if d * (target - entry) <= 0 or risk <= 0:
                continue
            res, xi = None, len(c) - 1
            for k in range(touch + 1, len(c)):
                if (l[k] <= stop) if d == 1 else (h[k] >= stop):
                    res, xi = -1.0, k
                    break
                if (h[k] >= target) if d == 1 else (l[k] <= target):
                    res, xi = abs(target - entry) / risk, k
                    break
            r = None if res is None else round(res - FEE * entry / risk, 2)
            out.append(dict(kind="IS", how="break", d=d, fill=touch, exit=xi, entry=entry, stop=stop, target=target,
                            r=r, swing=ii, steps=[(touch, stop)]))
    return sorted(out, key=lambda t: t["fill"]), levels


def broken_level_sweep_trades(sig, st, atr, line_at="wick", stop_at="sweep", wait=False):
    """Sweep entries at the broken level: in an uptrend the previous high that was broken (long),
    in a downtrend the previous low that was broken (short).

    After each break up the level is the broken swing high (line_at="wick": its high, "body": its body high),
    live from the candle after the break until the next break either way. A candle that wicks below the line
    and closes back above it is the entry (at its close). wait=False: only the first touch counts;
    wait=True: later sweeps count too, until a candle closes below the HL wick. Stop: under the sweep candle
    low (stop_at="sweep") or under the lower of that and the HL wick ("hl"). Target = the highest high since
    the break. Shorts mirrored. Returns (trades, levels); kind="PH" (short: "PL")."""
    o, h, l, c = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    up, dn = st["bos_up"].to_numpy(), st["bos_down"].to_numpy()
    lvl, hfrom = st["bos_level"].to_numpy(), st["bos_from_idx"].to_numpy()
    out, levels = [], []
    for d in (1, -1):
        brk, oth = (up, dn) if d == 1 else (dn, up)
        vsw = (st["valid_low_idx"] if d == 1 else st["valid_high_idx"]).to_numpy()
        kind = "PH" if d == 1 else "PL"
        for i in np.where(brk)[0]:
            s, p = int(vsw[i]), int(hfrom[i])
            if s < 0 or p < 0:
                continue
            line = lvl[i] if line_at == "wick" else (max(o[p], c[p]) if d == 1 else min(o[p], c[p]))
            sw_wick = l[s] if d == 1 else h[s]
            end = next((j for j in range(i + 1, len(c)) if brk[j] or oth[j]), len(c) - 1)
            ext, touch, first = i, None, None
            for j in range(i + 1, end + 1):
                reach = (l[j] <= line) if d == 1 else (h[j] >= line)
                if reach and first is None:
                    first = j
                if reach and d * (c[j] - line) > 0:
                    touch = j
                    break
                if reach and not wait:
                    break
                if (c[j] < sw_wick) if d == 1 else (c[j] > sw_wick):
                    break
                if (h[j] > h[ext]) if d == 1 else (l[j] < l[ext]):
                    ext = j
            levels.append(dict(d=d, swing=p, start=i, end=touch if touch is not None else (first if first is not None else end),
                               entry=line, kind=kind, status="filled" if touch is not None else ("closed through" if first is not None else "expired")))
            if touch is None:
                continue
            entry = c[touch]
            sw = l[touch] if d == 1 else h[touch]
            stop = sw if stop_at == "sweep" else (min(sw, sw_wick) if d == 1 else max(sw, sw_wick))
            target = h[ext] if d == 1 else l[ext]
            risk = abs(entry - stop)
            if d * (target - entry) <= 0 or risk <= 0:
                continue
            res, xi = None, len(c) - 1
            for k in range(touch + 1, len(c)):
                if (l[k] <= stop) if d == 1 else (h[k] >= stop):
                    res, xi = -1.0, k
                    break
                if (h[k] >= target) if d == 1 else (l[k] <= target):
                    res, xi = abs(target - entry) / risk, k
                    break
            r = None if res is None else round(res - FEE * entry / risk, 2)
            out.append(dict(kind=kind, how="break", d=d, fill=touch, exit=xi, entry=entry, stop=stop, target=target,
                            r=r, swing=p, steps=[(touch, stop)]))
    return sorted(out, key=lambda t: t["fill"]), levels
