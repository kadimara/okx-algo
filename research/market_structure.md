# Market structure: definitions and rules

How the BTC leg structure on the chart (`research/build_structure_chart.py`, `structure.py`) defines swings,
trends and breaks. Everything below is what the code does. Where the chart has a known inconsistency it is noted.

## Settings

| Setting | Value | Meaning |
|---|---|---|
| Data | OKX BTC-USD index, 1h candles | `data/BTC_1h_*_index_signal_*.csv` (download: `research/download_data.py`) |
| Swing size `LEG_N` | 8 candles | a pivot must be the extreme of 8 candles on each side |
| Minimum leg `LEG_ATR`, `LEG_ATR_LEN` | 4 × ATR(720) | since 2026-11-24 a leg must be at least 4 × the 30-day ATR (1h, Wilder) in % of the close, as of the swing the leg starts from (about 2–4.5 %; see §1e). `LEG_ATR = 0` gives the fixed `MIN_LEG` (2.5 %) |
| Break rule | **close** | only a candle close beyond a level breaks it; a wick through it does not |
| Naming | MSB / MSS | **MSB** = market structure break (continuation; elsewhere BoS), **MSS** = market structure shift (reversal; elsewhere CHoCH) |

## 1. Swing high and swing low

### 1a. Pivot (raw swing)
A candle `p` is a **swing high** when:
- its high is **strictly higher** than the highs of the 8 candles before it, **and**
- its high is **higher than or equal to** the highs of the 8 candles after it.

A **swing low** mirrors this: its low is strictly lower than the 8 lows before it and lower than or equal to the 8 lows after it.

A pivot is **confirmed** 8 candles after the pivot candle, at the close of candle `p + 8`. Before that it is unknown.

### 1b. Alternating swings
The swings must alternate high, low, high, low:
- A new swing high is only accepted when the last swing was a low, **or** when it is higher than the current swing high. In that case it replaces it as the same leg's high.
- A swing low works the same way: accepted after a high, or when lower than the current swing low (replacing it).

### 1c. Swings created at a break (internal fill)
Sometimes a close breaks the last swing low (see §3) while the last swing is still a low, so no swing high has been confirmed since. The highest high between that low and the breaking candle is then inserted as a swing high, confirmed at the break. The bullish case mirrors this: the lowest low is inserted as a swing low. This keeps the alternation intact when price breaks before a normal pivot forms.

### 1d. Leg swings (minimum 2.5 %)
The base swings from 1a–1c are reduced to **leg swings**:
1. Measure every leg (swing to next swing) in %, except the first and the last leg.
2. Take the smallest leg under 2.5 % and remove both of its swings.
3. Where two swings of the same type now follow each other, keep the more extreme one: the higher high or the lower low.
4. Repeat until every leg is at least 2.5 %.

The first and last swing always stay; the last leg is still forming.

**Keep-extreme merge (tested 2026-11-24; for the whole structure removed from the code, kept only for the supply-zone check, see §7).** Step 2 removes both swings of the smallest leg, so for one
rebuild a lower high can stand in for a higher one (25–26 Sep 2026: 84,341 replaced 85,256). With `MERGE_EXTREME` the
most extreme swings stay: for l, a, b, r with a -> b the smallest leg, drop b and r when a is more extreme than r,
else drop l and a when b is more extreme than l, else a and b as before. Over 4 years the trend, breaks and MSSes stay
the same (411 trend changes, 6.0 MSS / month); fewer temporary swings (live labels 60 -> 50 / month). Backtests
(with ZONE_REAL_LEG): shorts confirmed trend +18.0 % -> +18.7 %, shorts chart rules +12.3 % -> +14.1 %, but demand
at a HL +31.8 % -> +25.1 % and all demand zones +36.4 % -> +30.8 %. Mixed, so it stays off.

### 1e. ATR-based minimum leg (2026-11-24, default)
The fixed 2.5 % treats quiet and wild periods the same. With `LEG_ATR > 0` the minimum leg is `LEG_ATR` × ATR(`LEG_ATR_LEN`)
in % of the close at the leg's first swing (that swing's pivot candle, so it is known at its confirmation: no hindsight).
`merge_legs` then removes the leg that is smallest **relative to its own minimum**; `leg_big_enough` (real legs, zones at a
LH / HL) uses the same minimum. With a fixed minimum both give exactly the old result.

The 30-day ATR is 0.5–1.1 % of price (median 0.72 %), so 4 × gives a minimum leg of about 2.0 % (2023, 2026) to 4.5 % (2021).

Test (BTC 1h index, Jan 2020 → Nov 2026, all live; `backtest_leg_structure.py`, `backtest_zone_rules.py`, `backtest_demand.py`):

| min leg | leg swings | MSS | follow the bias (0.05 %) | shorts, confirmed trend | demand at a HL (chart rule) | all demand zones |
|---|---|---|---|---|---|---|
| **2.5 % fixed (old)** | 2,276 | 737 | +576 % | 46 trades, +22.9 %, DD 8.9 % | 77, +19.9 %, DD 12.8 % | 91, +20.8 %, DD 14.4 % |
| 2.8 % fixed | 1,988 | 727 | +835 % | 35, +4.3 % | 68, +28.1 % | 80, +31.3 % |
| 3.0 % fixed | 1,794 | 689 | +406 % | 32, −7.3 % | 62, +30.5 % | 72, +25.7 % |
| 3.5 % fixed | 1,472 | 661 | +1,659 % | 29, −3.8 % | 49, +15.9 % | 54, +10.3 % |
| 3.0 × ATR 168 / 336 / 720 | ~2,560 | ~780 | +1,133 … +2,268 % | −10.6 … −1.0 % | −9.0 … +5.3 % | −15.6 … −1.2 % |
| 3.5 × ATR 168 / 336 / 720 | ~2,100 | ~760 | +1,367 … +2,644 % | +9.0 … +18.2 % | +3.6 … +15.2 % | −2.1 … +7.5 % |
| 4.0 × ATR 168 | 1,694 | 715 | +3,698 % | 33, +12.7 % | 54, +18.1 % | 61, +10.1 % |
| 4.0 × ATR 336 | 1,710 | 721 | +2,417 % | 35, +18.2 % | 59, +18.9 % | 66, +10.9 % |
| **4.0 × ATR 720 (default)** | 1,746 | 702 | +1,264 % | 40, **+25.9 %**, DD 8.7 % | 56, **+33.8 %**, DD 10.6 %, 1 losing year | 66, **+35.1 %**, DD 12.5 % |
| 4.0 × ATR 1440 | 1,774 | 686 | +1,970 % | 38, +25.0 %, DD 7.8 % | 60, +21.4 % | 69, +23.8 % |
| 4.0 × ATR 2160 | 1,776 | 668 | +3,528 % | 39, +23.9 %, DD 6.8 % | 61, +22.1 % | 68, +20.1 % |
| 4.5 × ATR 336 / 720 / 1440 | ~1,460 | ~660 | +713 … +2,201 % | +24.0 … +24.8 % | +10.2 … +39.9 % | +10.4 … +44.5 % |
| 5.0 × ATR 720 | 1,226 | 603 | +1,583 % | – | 35, +17.2 % | 41, +23.0 % |

(1 % risk per trade, after fees; "follow the bias" = the leg-structure script's "new, hold old trend while grey" with 0.05 % fee per side.)

- Every ATR setting follows the trend better than the fixed 2.5 % (+576 %), and 4 × ATR(720) is better in every year (2020 +176 vs +147 %, 2024 −26 vs −37 %).
- A larger *fixed* leg helps the demand zones but costs the shorts. 4 × ATR keeps the shorts and improves demand: the volatility scaling does something a fixed % does not.
- 3–3.5 × ATR (about as many swings as 2.5 %) is worse for the zones. From 4 × with a 30–90 day ATR the zones are as good or better with a lower drawdown.
- **Caveat:** the zone results jump between neighbouring settings (4.5 × ATR(720) +39.9 % vs 4.5 × ATR(1440) +10.2 % for demand). 4 × ATR(720) is the best of the 4 × row, so expect the lower end of that row (+20–25 %) going forward. Zone rules were tuned on the 2.5 % structure and were not re-tuned.
- In the chart (last 2 years only) the ATR starts at the first candle of that window, so the first weeks of the view use a less settled ATR.

## 2. Structural swings, ranges and levels

At any time the structure has **one structural high** and **one structural low**. Together they form the current range. All other swings inside that range are **internal**: they break nothing and are drawn faded.

The two levels have names depending on the trend:

| | Uptrend | Downtrend |
|---|---|---|
| **MSB level** (market structure break, continuation) | the structural high = the last **HH** | the structural low = the last **LL** |
| **MSS level** (market structure shift, turns the trend) | the structural low = the last **HL** | the structural high = the last **LH** |

While the leg after a break has no swing yet, the MSB line on the chart is drawn at the extreme since the break: the highest high in an uptrend, the lowest low in a downtrend.

## 3. Breaks: MSB and MSS

A **break** is a candle **close** beyond a structural level. Closing exactly on the level, or only wicking through it, is not a break.

| Event | Condition | Effect |
|---|---|---|
| **MSB up** (market structure break, continuation) | uptrend, close **above** the structural high (HH) | trend stays up, a new leg starts |
| **MSB down** | downtrend, close **below** the structural low (LL) | trend stays down |
| **MSS up** (market structure shift; elsewhere called CHoCH) | downtrend, close **above** the structural high (the LH = MSS level) | trend turns **up** |
| **MSS down** | uptrend, close **below** the structural low (the HL = MSS level) | trend turns **down** |

An MSS is a close through the **MSS level**, against the trend. A close through the **MSB level** continues the trend.

### What happens to the levels after a break

**Bullish break** (MSB up or MSS up) at close `i`:
1. **New structural low:** the lowest swing low since the broken high was set. This is the HL (or LL) the move started from, and it is the MSS level of the uptrend from now on. If a lower low formed in the same pullback, it replaces the earlier low.
2. **Structural high:** none for now. The trend waits for the leg's next swing high.
3. The next swing high becomes the new structural high: the HH and the next MSB level.
4. The first swing low after that HH immediately becomes the new structural low: the HL and the new MSS level. The range to break is then **[HL, HH]**.
5. Swings inside the range that break neither level are internal.

**Bearish break** (MSB down or MSS down) is mirrored:
1. **New structural high:** the highest swing high since the broken low was set. This is the LH the move started from and the MSS level of the downtrend.
2. **Structural low:** none until the leg's next swing low, which becomes the LL (MSB level).
3. The first swing high after that LL immediately becomes the LH (new MSS level). The range is **[LL, LH]**.

## 4. Uptrend and downtrend

- **Uptrend** (bias green): from the close of a bullish break (MSB up or MSS up) until the next close **below the structural low** (MSS down).
- **Downtrend** (bias red): from the close of a bearish break until the next close **above the structural high** (MSS up).
- **No trend** (0): only at the very start of the data, before the first break.

So the trend only changes on a close through the MSS level, never on a swing label alone.

The trend shown is always the state **at the close** of each candle; a break counts from the candle that made it.

## 5. Swing labels HH / HL / LH / LL

Each leg swing is labelled by comparing its price (the **wick**: high or low) with the **last structural swing of the same type**:
- **HH** if a high is above the last structural high, otherwise **LH**.
- **LL** if a low is below the last structural low, otherwise **HL**.

Internal swings are drawn faded.

> **Known inconsistency (open, 2026-11-20):** labels compare wicks, breaks need closes. A high that only wicks above the LH, without any close above it, is labelled **HH**. The trend stays bearish, correctly by §3. Example: 9 Aug 2026, the high of 65,472 wicked 59 points above the LH of 65,413, but no candle closed above it until 19 Aug. Proposed fix: in a downtrend, label a high that is not closed above as LH (a liquidity sweep); mirrored for lows in an uptrend.

## 6. Hindsight vs live

- **Hindsight structure** (chart labels, levels and the bias bar). The leg swings are built from all swings in the data, and each swing is used from its **pivot candle** on. A candle's colour can change later, when new swings are confirmed or legs are merged. This shows the cleanest trend but cannot be traded as shown.
- **Live structure** (`live_structure`; used by the supply zones, the Pine indicator and the backtests). At every candle where a swing is confirmed, the leg swings are rebuilt from the last 120 swings confirmed **by then**, and §2–§4 run on until the next confirmation. Swings that a more extreme swing of the same type replaced are dropped, as in 1b. No future data is used. The live trend changes about 3× as often (39 vs 13 changes in 120 days).
- **Live bias with confirmation** (`bias_bar`; the Pine indicator's bias strip):
  - After an MSS the bias is **grey** (±0.5) until the new trend makes its first MSB; that MSB confirms the new trend.
  - If price instead breaks back in the old direction first, the MSS **failed** and the bias returns to the old trend's colour from that close.

### Settled legs (tested 2026-11-24, removed from the code)
At each rebuild only the newest 1–2 swings can still change (measured over 1,400 rebuilds: 54 % nothing changes,
15 % the newest swing, 31 % the newest two, never older). A swing that can still be merged away can briefly become
the MSS level (e.g. the LH 64,246 on 4 Aug 2026: its leg in was 3.2 %, the pullback after it only 1.2 %).
The option: a swing only counts for the structure once the leg after it is a real leg (>= 2.5 %, ended by a confirmed swing).
The newest swing never counts yet.

| 4 years | current | settled legs |
|---|---|---|
| trend changes | 411 | 191 |
| median trend length | 46 h | 140 h |
| MSS / month | 6.0 | 3.5 |
| shorts, confirmed bearish trend | +21.6 % (DD 6.8 %) | −7.6 % (DD 18.1 %) |
| demand chart rule at a HL | +27.1 % (DD 5.6 %) | +24.7 % (DD 5.6 %) |

The structure is much calmer, but the bias reacts later, and the shorts lose their edge. The default stays `False`.

## 6b. Body-break structure (tested 2026-11-24, removed from the code)

An alternative structure, built causally in one pass (`body_structure`), so live = hindsight:
- **Swings:** pure 8-candle swings (§1a, §1b), with no 2.5 % leg merge.
- **Levels:** the structural high / low as in §2–§3, but the level is the **body edge** of the swing candle: max(open, close) for a high, min(open, close) for a low.
- **Break (MSB / MSS):** the first candle whose **open and close** are both beyond the level. A wick, or a candle that opens inside and closes beyond, is not a break.
- **Swing at a break:** the extreme of the leg into the break (the lowest low since the broken high, or the highest high since the broken low) becomes the new HL / LH. It is inserted as a swing if it isn't one.
  - "break": at the break candle.
  - "wait": once that extreme is 8 candles old, so a pullback shows first. Until then that side has no level.
- **Sweep (×, chart only):** the first wick beyond a structural swing's wick without a body break, and with no break within 3 candles.

Test (2026-11-24, 4 years of data, 1 % risk per trade):

| | current (2.5 %, close) | body, break | body, wait |
|---|---|---|---|
| swings / month | 60 | 58 | 57 |
| breaks / month (MSS) | 12.4 (6.0) | 24.8 (13.0) | 24.3 (12.4) |
| MSS reversed within 24h | 4 % | 30 % | 28 % |
| shorts, confirmed bearish trend | +21.6 % (DD 6.8 %) | +1.7 % (DD 14.1 %) | +6.4 % (DD 11.1 %) |
| longs at a HL (zone-rules script) | +8.2 % | +15.6 % | +15.6 % |
| demand chart rule at a HL | +27.1 % (DD 5.6 %) | +6.2 % | +10.9 % |
| demand chart rule, all zones | +5.6 % | +13.8 % | +16.6 % (DD 10.1 %) |

The zone rules were tuned on the current structure. The default stays `"structural"`.

**12-candle swings** (`LEG_N = 12`; the wait mode then also waits 12 candles), same test:

| | 12c current | 12c body, break | 12c body, wait |
|---|---|---|---|
| swings / month | 41 | 40 | 39 |
| MSS / month | 4.7 | 9.1 | 8.8 |
| MSS reversed within 24h | 3 % | 22 % | 20 % |
| shorts, confirmed bearish trend | +9.7 % (DD 13.5 %) | +2.1 % (DD 17.8 %) | +2.1 % (DD 18.8 %) |
| longs at a HL (zone-rules script) | +12.3 % (12 trades) | +18.7 % (12, DD 2.2 %) | +18.4 % (9, DD 2.2 %) |
| demand chart rule, all zones | +8.6 % | +15.0 % | +18.4 % (DD 8.8 %) |
| demand chart rule at a HL | +16.9 % | +16.4 % | +15.0 % |

12 candles cut the false MSSes by about a third compared with 8 candles. Longs on the body structure stay good. Shorts are best on the current 8-candle structure (+21.6 %).

## 7. Supply zones (chart)

- **The zone:** each 8-candle swing high, as confirmed live, spanning the full high-to-low range of the candle that made the high. A candle taller than 1 % (`WIDE_ZONE`) gives only its upper wick: from the high down to the body high (the higher of open and close), at least 0.5 % tall (`MIN_ZONE`). The sharp-drop test below still uses the candle low.
- **Very sharp move down:** only zones price left very sharply: within 6 candles after the zone candle, a low at least 1.5 × ATR(14) (Wilder, as of the swing-high candle) below the zone bottom (`SHARP_K`, `SHARP_ATR`; 1 × ATR within 6 candles until 23 Nov 2026, 2 × ATR within 4 candles until the retune of 24 Nov, §7c). A red zone only counts as known from the later of the swing-high confirmation and the candle that completes the drop, so it adds no hindsight. Zones without it are not drawn for now (`DRAW_GREY`).
- **When it is drawn:** the box starts at that candle. It is only drawn if the zone became known while the live bias was bearish (red, not grey), and before price traded back into it.
- **Zones at a LH:** a swing high that is the top of a pullback of at least `MIN_LEG` % up from the leg low, and below the previous leg high (leg swings as known at its confirmation). Touches before the confirmation do not count. Since 23 Nov 2026 they also need the bearish live bias like the other zones (`LH_BIAS = True`): without it (the earlier looser rule: drawn whatever the bias, not ended by a bias change) they gave the long losing runs in the backtest.
- **When it ends:** at the first of these:
  - the first candle that trades back into it after price left it;
  - a higher high;
  - the live bias stops being bearish;
  - a newer leg high settles (zones only count for the current swing).

**Only at real legs (`ZONE_REAL_LEG = True`, 2026-11-24).** A supply or demand zone only counts while its swing is a real
leg swing of the live structure: it must be in the legs of the latest rebuild, and the newest swing only once the leg
into it is at least 2.5 %. Before that the zone is not shown; it ends as soon as a rebuild merges its swing away. On the
chart (right-edge view), a zone whose swing is no longer a leg at that moment is hidden, like its label.
Example: the 8-candle high of 29 Sep 2026 (84,560, a leg of only 2.4 % into it, merged at the next rebuild) no longer gets a zone.

| 4 years | before | only real legs |
|---|---|---|
| shorts, confirmed bearish trend | 45 trades, +21.6 %, +0.48R, DD 6.8 % | 24 trades, +18.0 %, **+0.75R**, DD 6.8 % |
| shorts, chart rules (also in a bearish MSS) | 64 trades, +13.8 %, DD 11.3 % | 35 trades, +12.3 %, DD 13.3 % |
| demand chart rule at a HL | 38 trades, +27.1 %, DD 5.6 % | 34 trades, **+31.8 %**, DD 4.6 % |
| demand chart rule, all zones | 77 trades, +5.6 %, DD 16.6 % | 41 trades, **+36.4 %**, DD 5.7 %, no losing year |

Known quirk: when `merge_legs` removes a small leg it removes both of its swings, so for one rebuild a lower high can
stand in for a higher one (25–26 Sep 2026: 84,341 replaced 85,256 until the next rebuild); such a zone then ends early.
Fixed for supply zones (`ZONE_MERGE_EXTREME = "supply"`, 2026-11-24): the real-leg check of supply zones uses legs
merged with the keep-extreme merge (`merge_legs(extreme=True)`, the most extreme swings stay); the structure, bias and demand zones keep the normal merge.
The 25 Sep zone now lives until the fill (27 Sep 05:00) and wins +1.9R. Shorts confirmed trend +18.0 % -> +18.7 %
(26 trades), chart rules +12.3 % -> +14.1 % (36 trades); demand unchanged (+31.8 % at a HL). With the fix for demand
too (True), demand drops to +25.1 %.

## 7b. Demand zones (chart)

The supply-zone rules mirrored for longs (`demand_zones`, green on the chart):

- **Demand rules (since 23 Nov 2026, from the grid in `backtest_demand.py`):** only at a HL (`DEMAND_HL_ONLY`; the stricter "after a HH" filter `DEMAND_AFTER_HH` is off: higher win rate but half the trades); a rise of 1.5 ATR (`DEMAND_SHARP_ATR`) and at least 1 % (`DEMAND_MIN_PCT`) within 4 candles; a bearish MSS does not end the zone, only a confirmed downtrend (`DEMAND_COUNTER_MSS_ENDS = False`). 4 years: 38 longs, +27 % at 1 % risk, 5.6 % max drawdown. Supply zones keep their own rules.

- **The zone:** each 8-candle swing low, as confirmed live, spanning the full range of the candle that made the low. A candle taller than 1 % gives only its lower wick: from the low up to the body low (the lower of open and close), at least 0.5 % tall.
- **Very sharp move up:** within 4 candles a high at least 2 × ATR(14) above the candle high. Zones without it are not drawn.
- **When it is drawn:** from the first close at which it is known (swing low confirmed, rise made) while the live bias is bullish (green, not a grey MSS), and before price traded back into it.
- **Zones at a HL:** the bottom of a pullback of at least `MIN_LEG` % down from the leg high, above the previous leg low. Touches before the confirmation do not count; they need the bullish bias like the others (`LH_BIAS`).
- **When it ends:** at the first of: a candle that trades back into it after price left it (a low above it first); a lower low; the live bias stops being bullish; a newer leg low settles.

**Bias for the zones (per side):** supply zones are shown only in a confirmed bearish trend (bias bar solid red, `SUPPLY_MSS_OK = False` since the retune of 24 Nov 2026, §7c; from 23 Nov also in a pending bearish MSS); demand zones in a bullish trend or a pending bullish MSS (green or faded green, `DEMAND_MSS_OK = True`). A zone ends when the bias leaves that set (`COUNTER_MSS_ENDS = True`). The chart's bias bar shows this live bias (it was the hindsight trend).

## 7c. Zone rules retuned on the ATR legs (2026-11-24)

`research/backtest_retune.py`: a grid of each side's zone rules on the ATR-leg structure, with the chart's own trades
(`zone_trades`, 1 % risk, fees included). Ranked on **2020–2023 only (train)**, then checked on **2024 → Nov 2026 (test)**,
which the ranking never saw. Supply: 96 combinations, demand: 432.

| | rule | train 2020–23 | test 2024–26 | all | losing years |
|---|---|---|---|---|---|
| supply, before | 2 ATR in 4 candles, also in a bearish MSS | 29 trades, +3.5 % | 30, +7.9 % | +11.4 % | 3 |
| **supply, now** | **1.5 ATR in 6 candles, confirmed downtrend only** | 40, **+36.4 %**, DD 11.9 % | 45, **+16.2 %**, DD 10.1 % | **+52.5 %** | **0** |
| demand (unchanged) | 1.5 ATR + 1 % in 4 candles, at a HL, bullish MSS too, a bearish MSS keeps the zone | 35, +14.2 % | 21, +19.5 %, DD 5.4 % | +33.8 % | 1 |

- The best single train score does not carry over (the rank correlation of train and test over all combinations is only
  +0.14 for supply, +0.21 for demand), so the choice was made on what is **consistent in both halves**, averaged over the
  rest of the grid, not on the top row.
- **Supply:** the clear one is `SUPPLY_MSS_OK = False` (shorts only in a confirmed downtrend): +23.1 / +12.0 % (train / test)
  on average vs +0.8 / +2.1 % with a pending MSS allowed. A 1.5 ATR drop beats 1, 2 and 2.5 in both halves. All 12
  neighbours with a confirmed trend and 1.5–2 ATR within 4–8 candles are positive in both halves (test +7.3 … +24.4 %).
  `SUPPLY_MIN_PCT` and `COUNTER_MSS_ENDS` make no consistent difference, so they stay (0 %, True).
- **Demand:** the current rule already has every choice that is consistent in both halves (4 candles, 1.5 ATR, a bullish
  MSS counts, a bearish MSS does not end the zone), and its test result (+19.5 %) beats almost all of the train top 15.
  Unchanged. 12 candles, 1 ATR and "after a HH" looked good on train and failed on test.
- The supply change makes more zones (72 instead of 39 on the 2-year chart): the softer drop adds zones, the
  confirmed-trend rule removes the ones at the start of a reversal.

## 8. Research notes

- **Continuation:** a trend makes one more MSB about 50% of the time. SMA filters and other swing sizes don't change that (see `ms_research_summary.md`).
- **Sideways** (`backtest_sideways.py`): "last 3 legs < X %" does pick out quieter periods (next-24h range 2.4–2.8 % vs 3.7–4.2 %). But following the live trend earns the same in and out of those periods (≈ +0.02 % per 24h, so practically no edge). Going flat while sideways therefore only adds trades and fees. Range vs ATR does not find quieter periods.

## 9. Cleanup (2026-11-24)
Removed from the code after testing (results above and in the commit history): the body-break structure with its sweep
markers and early-swing setting, the 10 % retrace leg rule, settled legs, the keep-extreme merge for the whole structure,
the reversal study chart (4-candle reversals with a power filter) and the long-setups sketch. The fast-MSS, reversal-long
and sweep tests ran in scratch scripts and were never part of the code.
