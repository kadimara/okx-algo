# Market structure research: summary (19 Nov 2026)

Research on the MS structure strategy on **BTC 4h**, done visually on the chart artifact and
measured with small backtests. All results are in **R** (1R = the risk per trade), after 0.1% fees
round trip, unless stated otherwise.

## Data

| File (`data/`) | Range | Candles |
|---|---|---|
| `BTC_4h_2515d_index_signal_2026-11-19.csv` | 2020-01-02 → 2026-11-19 | 15,077 |
| `BTC_4h_2515d_perp_2026-11-19.csv` | 2020-01-01 → 2026-11-19 | 15,087 |
| `BTC_1h_2515d_index_signal_2026-11-19.csv` | 2020-01-02 → 2026-11-19 | 60,306 |
| `BTC_1h_2515d_perp_2026-11-19.csv` | 2020-01-01 → 2026-11-19 | 60,346 |
| `*_2h_2400d_*` for BTC, ETH, SOL, XRP, DOGE, BNB | 2020-03 → 2026-10-08 | used for the 6-coin checks |

The index file carries volume added up across exchanges; Bitfinex was skipped in the 1h download.

## Market structure (unchanged, `structure.py`)

- **Swing size 12 candles on 4h (2 days).** Swing high: high strictly above the 12 candles before it and
  ≥ the 12 after it (lows mirrored). Confirmed 12 candles (48h) later.
- Labels HH / LH / HL / LL. Trend bullish on HH + HL, bearish on LH + LL.
- A close beyond the last swing is a break: **BOS** with the trend, **CHoCH** against it (trend flips).
- **Internal swings on:** at a break the extreme candle since the broken swing becomes the HL / LH.
- Swing size 11 and 14 were tried. 11 was briefly used and reverted; **12 stays**.

## What the statistics showed

### Chance that a trend makes one more break

| Break | 1→2 | 2→3 | 3→4 | 4→5 | 5→6 |
|---|---|---|---|---|---|
| BTC 4h uptrend | 56% (77) | 53% (43) | 57% (23) | 62% (13) | 38% (8) |
| BTC 4h downtrend | 54% (78) | 36% (42) | 47% (15) | 14% (7) | 1/1 |
| 6 coins 2h uptrend | 50% (473) | 53% (236) | 52% (124) | 44% (64) | 36% (28) |
| 6 coins 2h downtrend | 49% (474) | 37% (233) | 46% (87) | 38% (40) | 33% (15) |

(n) = trends that reached that break. Below ~15 the number is anecdotal.

- Each next swing is roughly a **coin flip**. Downtrends are weak at break 2→3 (~37%).
- Longest BTC trend: 12 breaks (Sep–Nov 2020). Second: 9 breaks (Oct 2023 – Jan 2024).
- **No improvement** from: 2-week SMA, 50d SMA, 200d SMA (price side or slope), or swing size 8 / 12 / 14.
  Mature downtrends below a falling 50d / 200d SMA continue *less* often (mean reversion).
- Conclusion: the edge is not in predicting the next swing but in **entry location and payoff**.

### Points of interest after a break up (shorts mirrored)

Where the pullback turned (180 breaks up): prev high ~35%, HL ~30%, impulse start ~22%,
no retest ~13% of the successful ones; ~43% of breaks failed (low broken before a new high).

Plain limit orders per POI (stop under the HL wick − 0.25 ATR, target the new high):

| POI | Trades | Win | Total R |
|---|---|---|---|
| **HL** | 83 | 25% | **+27.7** |
| Impulse start (IS) | 73 | 41% | +8.0 |
| Prev high (PH) | 170 | 78% | +2.2 (tiny R:R) |

Shorts were negative at every POI. **HL (LH for shorts) is the level to trade.**

### Zones (dropped later)

- Entry blocks / order blocks: zones where the next 6 candles averaged ≥ 1× ATR won 58% vs 43%.
- Fair value gap order blocks needed the trend filter (against-trend blocks removed).

## Entry tests at the HL line (long; shorts mirrored at the LH)

Line = **HL body low**. "Early" = the order goes live when the HL is confirmed in a bullish trend
(bias bar green), not only after a break up — doubles the number of setups.

| Entry / exit | Long trades | Win | Total R | Max DD (R) |
|---|---|---|---|---|
| Limit on line, stop at HL wick, target = HH / highest high since HL, early | 152 | 24% | +97.4 | 16.7 |
| Same + break-even after 2R | 152 | 16% | +53.8 | 23.5 |
| Same + level removed after a 0.25 ATR near-touch | 136 | 23% | +64.4 | 14.3 |
| Target capped at 1R, stop 1 ATR beyond wick (≈50% win) | 152 | 51% | −5.3 | 10.0 |
| Reaction: reclaim (close back above within 6 candles) | 116 | 39% | +46.8 | 6.4 |
| Reaction: engulf (close above touch candle high) | 51 | 69% | +28.0 | 2.2 |
| Reaction: displacement (close ≥ 0.5 ATR above line) | 86 | 53% | +43.9 | 4.3 |
| **Sweep: first touch wicks through and closes back above** | **94** | **39%** | **+43.8** | **5.6** |

Rejected along the way: hammer / doji trigger with stop under that candle (−47R on PH), body-high targets
(−33R vs wick target), 6-candle trailing stop, stop only under the sweep candle (28% win, DD 24.9R),
impulse-start sweeps (too noisy, +1.5 to +13R), sweeps of the broken high / low (−41 to +10R),
targeting the current (internal) high instead of the leg high (+30.7R vs +43.8R).

## Chosen setup: HL sweep (longs)

1. Level: HL body low. Live when the HL is confirmed in a bullish trend or a break validates it
   (internal HLs included). Ends at the next break either way.
2. Entry: the **first candle that reaches the line wicks through it and closes back above** → buy at that close.
   A touch that closes below the line cancels the level.
3. Stop: under the **lower of the sweep wick and the HL wick** (no buffer).
4. Target: the **HH the pullback came from** (early entry) or the **highest high since the HL**. No break-even.

BTC 4h 2020 → Nov 2026: **94 trades (~14/year), 39% win, +43.8R, max drawdown 5.6R, median R:R 3.5,
no losing year** (per year +2 / +2 / +1 / +19 / +7 / +5 / +6).

Shorts (mirrored at the LH): 66 trades, 29% win, +14.6R, max DD 14.0R, 3 losing years — not reliable.

## Caveats and open points

- One coin, one timeframe; results move ±10–15R with one candle of swing size. Test on ETH / SOL / others.
- R ignores leverage caps, slippage and funding; tight wick stops can need >2× leverage at 1% risk.
  The setup has not been run through `ms_backtester.py` yet.
- 2023 delivers a large part of the profit (+19R of +43.8R).
- Goal of ~1 trade per week is not met (~14 long trades / year).

## Where we are now (20 Nov 2026)

Started fresh after this summary: the chart shows **only the market structure** on the **last 2 years of BTC 1h**
(2-day swings = 48 candles, swing labels, LTF trend strip) plus **CHoCH areas of a lower structure**
(6h swings by default; 12h / 24h selectable), **only in the direction of the 2-day trend**, each with a
CHoCH line from the broken lower swing to the close through it. Chart: https://claude.ai/artifact/WtdEDEBvAkHQ375i3vD3xS
(private, opens on any device signed in to the same claude.ai account).

## Files

- `research/download_data.py` — downloads BTC 1h + 4h (index and perp) from 2020 until now into `data/`.
- `research/build_structure_chart.py` + `template_structure.html` — the current chart (structure + lower CHoCH areas)
  → writes `research/ms_structure_chart.html`. Takes ~3–4 minutes.
- `research/build_chart.py` + `template.html` — the earlier full chart (blocks, POIs, sweep trades)
  → writes `research/ms_full_chart.html`.
- `research/hl_trades.py` — trade simulations (`sweep_trades` = chosen setup, plus the tested variants).

The build scripts read the newest matching file in `data/`, so they work after a fresh download.

## How to start on another device

```
git clone https://github.com/kadimara/okx-algo.git
cd okx-algo
git checkout ms-research
python -m venv venv
venv\Scripts\activate          (macOS / Linux: source venv/bin/activate)
pip install -r requirements.txt
python research/download_data.py
python research/build_structure_chart.py
```

Then open Claude Code in the repo and ask it to read `research/ms_research_summary.md` and continue.
To update the existing chart page, give Claude the artifact link above.
