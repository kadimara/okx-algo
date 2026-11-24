# OKX Moving-Average Algo

A moving-average crossover strategy with two engines that share it:
a **backtester** (tests on history) and a **live trader** (runs on OKX, demo by default).

By default the strategy reads the **OKX index price** (an average of the price across several
major exchanges), while all orders, fills and the stop-loss use the tradable **OKX spot** price.
Use `--signal-data spot` in either app to make the strategy read spot candles instead.

```
strategies/base.py          template every strategy follows
strategies/ma_crossover.py  the MA crossover strategy
market_data.py              downloads closed candles from OKX (used by both engines)
backtester.py               backtest engine
okx_trader.py               live/demo trading engine
run_backtest.py             app 1: run a backtest
run_live.py                 app 2: run the bot
```

## Setup (VS Code terminal)

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your OKX keys. Use **Read + Trade** permission only, never Withdraw.
Demo keys must be created while OKX is switched to Demo Trading; they are different from live keys.
The backtester needs no keys.

In VS Code you can also press **F5** and pick "Backtest" or "Live bot (DEMO)" (edit the arguments in `.vscode/launch.json`).

## App 1: backtest

```bash
python run_backtest.py
python run_backtest.py --symbol ETH/USDT --timeframe 1h --days 180 --fast 12 --slow 26 --ma-type ema
```

| Option | Default | Meaning |
|---|---|---|
| `--symbol` | BTC/USDT | market to test |
| `--timeframe` | 15m | candle size (1m, 5m, 15m, 1h, 4h, 1d...) |
| `--days` | 90 | how much history |
| `--fast` / `--slow` | 9 / 21 | MA lengths |
| `--ma-type` | sma | `sma` or `ema` |
| `--signal-data` | index | price the strategy reads: `index` or `spot` |
| `--cash` | 1000 | starting balance |
| `--fee` | 0.1 | fee per trade in % |
| `--slippage` | 0.05 | slippage in % |
| `--stop-loss` | 3 | stop-loss in %, `0` = off |
| `--refresh` | | re-download data instead of using today's cache |

Tip: run the same settings with `--signal-data index` and `--signal-data spot` to see which works better.

Results print in the terminal; every trade is saved to `results/trades.csv` and the equity curve to `results/equity.csv`.
Downloaded data is cached in `data/`.

## App 2: live bot

```bash
python run_live.py                                   # demo trading
python run_live.py --symbol ETH/USDT --size 0.01 --timeframe 1h
python run_live.py --live --size 0.0005              # REAL money, asks you to type YES
```

Same strategy options as the backtest, plus:

| Option | Default | Meaning |
|---|---|---|
| `--size` | 0.001 | order size in the base coin (BTC for BTC/USDT) |
| `--stop-loss` | 3 | stop-loss in %, `0` = off |
| `--check-every` | 30 | seconds between checks (stop-loss + new candle) |
| `--live` | off | trade real money |

Stop with **Ctrl+C**. The bot saves its position in `state/`, so after a restart it remembers what it holds
and won't buy twice. If you sell manually on OKX, it notices and resets. Logs go to `logs/`.

Notes: the bot trades spot, long only (buy, then sell). The backtest invests all its cash per trade, while the
live bot uses a fixed `--size`, so compare **percentage** returns. Use the same settings in both apps.

## Adding another strategy

Create a file in `strategies/` with a class that inherits `Strategy`, sets `lookback`
(how many candles it needs) and implements `generate_signal(candles)` returning `BUY`, `SELL` or `None`.
Then use it in `run_backtest.py` and `run_live.py` instead of `MACrossover`. Neither engine needs changes.

## Before using real money

- Backtest several periods and markets. If it doesn't beat buy & hold after fees, it isn't adding value.
- Tune settings on older data, then check them unchanged on recent data (avoid overfitting).
- Run on demo for at least a few weeks and compare the results with the backtest.
- Start live with the smallest size you can afford to lose.

This is educational software, not financial advice. Trading carries risk of loss.

## Aggregated volume (standalone)

`aggregate_volume.py` builds candles with volume summed across several exchanges
(Binance, Bybit, OKX, Coinbase, Bitstamp, Bitfinex by default), paired with the OKX index
price. It uses public data only, no keys. It runs on its own and doesn't need the rest of the project.

```bash
python aggregate_volume.py --check                    # which exchanges work from your connection
python aggregate_volume.py --timeframe 1h --days 30   # build and save to data/
python aggregate_volume.py --exchanges binance,coinbase,okx,coinbase:BTC/USDC
python aggregate_volume.py --price composite          # volume-weighted price across exchanges
```

Exchanges that are blocked or fail are skipped and listed in the report, with each exchange's
coverage and share of the volume. The CSV has the price, total `volume`, `n_exchanges`,
and one `vol_<exchange>` column per exchange.

## MS VWAP strategy (OKX perpetual futures)

The Python version of `ms_vwap_strategy.pine`, with the same rules and defaults:
weekly SMA slope sets the trend, limit entry at the anchored VWAP (ohlc4, anchored at the
last swing low), stop 2x ATR, target 4R, stop to entry +1% after 1R, anti-chop re-arm 2x ATR,
max 2 trades per trend, 1% risk per trade. Longs and shorts, on the `BTC/USDT:USDT` perpetual.

```
structure.py                pivots, HH/HL/LH/LL labels, anchored VWAP
strategies/ms_vwap.py       the strategy + the anti-chop gate (shared by both engines)
futures_backtester.py       backtest engine (fills, break-even, fees, funding)
okx_futures_trader.py       live/demo engine on OKX perpetuals
vwap_data.py                signal data: OKX index + aggregated volume, or perpetual
run_backtest_vwap.py        app: backtest
run_live_vwap.py            app: live bot (demo by default)
```

### Backtest

```bash
python run_backtest_vwap.py
python run_backtest_vwap.py --days 180 --target-r 3 --anchor trend
python run_backtest_vwap.py --signal-data perp --maker-fee 0.05 --slippage 0    # compare with TradingView
```

The option names match the Pine inputs: `--swing-days`, `--sma-days`, `--slope-days`, `--stop-atr`,
`--target-r`, `--be-after-r`, `--be-offset`, `--re-arm-atr`, `--max-per-trend`, `--risk`, `--side`, `--anchor`.
Default data: `--signal-data index` (OKX index price + volume summed across exchanges), 2h candles, 365 days.

Results: the report in the terminal, plus `results/vwap_trades.csv` (every trade with its result in R),
`vwap_equity.csv`, `vwap_structure.csv` (trend, VWAP and setup per candle) and `vwap_pivots.csv`.

To compare with the TradingView Strategy Tester, use `--signal-data perp` (same price and volume as
the OKX:BTCUSDT.P chart) and TradingView's flat 0.05% fee. Small differences remain: TradingView guesses the
order of prices inside a candle, this backtest assumes the stop is hit first when a candle reaches both,
and it charges funding.

### Live bot

Needs **futures** demo keys in `.env` (Read + Trade). It uses isolated margin and one-way mode.

```bash
python run_live_vwap.py                       # demo
python run_live_vwap.py --live                # real money, asks you to type YES
```

- Each entry is a limit order at the VWAP with the stop-loss and take-profit attached on OKX, so an open
  position stays protected if the bot is stopped or offline.
- Leverage is set per trade to what 1% risk needs (stop 0.5% -> about 2x).
- The break-even move happens at the candle close, like in the backtest. The bot must be running for that.
- State (open position, waiting order, anti-chop counters) is saved in `state/`, so a restart continues
  where it left off. Don't open positions on the same market by hand while it runs.

## MS structure strategy (OKX perpetual futures)

The Python version of the "MS strategy" Pine script, with the same rules and the same defaults
as your current TradingView inputs. Longs and shorts on the `BTC/USDT:USDT` perpetual, 4h candles.

- **Structure:** swings of 2 days, HH/HL/LH/LL labels, and breaks: a candle that closes through
  the last swing low/high flips the trend at once. A break in the trend direction is a BOS, one that
  turns the trend is a CHoCH. With internal structure on (default), the extreme candle since the
  broken swing becomes the LH/HL right away.
- **HTF trend:** the same rules with 7-day swings (no internal swings). The HTF filter is off by
  default; `--htf-filter` (or `--long-htf-filter` / `--short-htf-filter`) trades only with it.
- **Setups:** only the HL behind a break up (long) / the LH behind a break down (short) is traded.
  Continuation = that break was a BOS, pullback end = it was a CHoCH; each can be switched off.
- **Long** (bullish trend): buy limit 0.45% above the HL candle's body bottom (lower of open/close),
  stop at the HL low. **Short** is mirrored on the LH, with the sell limit 0.2% below its body top.
- **Target:** the newest swing on the other side (body edge); right after a break, the further
  of the broken swing and the one before it. Used for the R filter (skip longs below 1.25R, shorts below 2R); the
  take-profit order itself is off by default.
- **Exits:** at 2R the stop moves to entry, and a candle trailing stop starts: it follows the lowest
  low of the last 16 candles (long) / the highest high of the last 2 candles (short), -/+ the stop
  offset, only in your favour.
- One trade per HL/LH. Size: 1% of equity at the stop, capped at 2x leverage.
- Every entry/exit setting is separate for longs and shorts: `--X` sets both sides,
  `--long-X` / `--short-X` one side (e.g. `--short-min-rr 3`, `--long-no-choch`).

```
structure.py                analyze_bos_structure(): swings, labels, BOS, body edges
strategies/ms_structure.py  the strategy: entry, stop, target and filters per candle
ms_backtester.py            backtest engine (fills, one trade per pivot, trail, break-even, sizing)
okx_ms_trader.py            live/demo engine on OKX perpetuals
run_backtest_ms.py          app: backtest
run_backtest_ms_multi.py    app: backtest several coins, train / test, portfolio
run_backtest_ms_long.py     app: backtest the "MS strategy long" Pine script (longs only)
run_backtest_ms_short.py    app: backtest the "MS strategy short" Pine script (shorts only)
run_backtest_ms_sides.py    app: both of those side by side, plus the total per year
run_live_ms.py              app: live bot (demo by default)
```

### Backtest

```bash
python run_backtest_ms.py
python run_backtest_ms.py --signal-data perp --maker-fee 0.05 --taker-fee 0.05 --slippage 0   # compare with TradingView
python run_backtest_ms.py --use-target --min-rr 0
python run_backtest_ms.py --no-trail --use-target --be-after-r 2
python run_backtest_ms.py --no-htf-filter --long-no-choch --short-min-rr 3
```

| Option | Default | Pine input |
|---|---|---|
| `--swing-days` | 2 | Swing size (days) |
| `--htf-days` | 7 | HTF swing size (days) |
| `--swing-candles` / `--htf-candles` | (days) | Swing / HTF swing size (candles), long / short scripts: 24 / 84 |
| `--no-internal` | internal on | Internal structure (swing at BOS) |
| `--side` | both | Take longs / Take shorts |
| `--cont` | all | Setup: continuation (BOS): `none`, `first` (BOS 1), `first2` (BOS 1 and 2), `all` |
| `--no-choch` | on | Setup: pullback / rally end (CHoCH) |
| `--no-htf-filter` / `--htf-filter` | off | Only when the HTF is bullish / bearish |
| `--entry-offset` | long 0.45, short 0.2 | Entry offset (%) |
| `--stop-offset` | 0 | Stop offset (%) |
| `--min-rr` | long 1.25, short 2 | Skip trades below (R, 0 = off) |
| `--target-offset` | 0 | Target offset (%) |
| `--risk` | 1 | Risk per trade (% of equity) |
| `--max-leverage` | 2 | Max leverage (x) |
| `--use-target` | off | Use target |
| `--be-after-r` | 2 | Move stop to entry after (R, 0 = off) |
| `--no-trail` | trail on | Candle trailing stop |
| `--trail-after-r` | 2 | Start trailing after (R) |
| `--trail-candles` | long 16, short 2 | Trail behind the last N candles |

All options from `--cont` to `--trail-candles` also exist as `--long-...` / `--short-...`.

The defaults are the Pine inputs; the timeframe is 4h and fees are 0.1% maker / taker. For a
like-for-like TradingView comparison add `--signal-data perp --maker-fee 0.05 --taker-fee 0.05 --slippage 0`.
The best settings from the earlier 2h sweep (2020-2026, train / test) are still available:
`--timeframe 2h --entry-offset 0.35 --long-min-rr 1.5 --short-htf-filter --target-offset 0.1
--be-after-r 0 --trail-after-r 1 --trail-candles 24`.

### Long and short scripts

`ms_strategy_long.md` and `ms_strategy_short.md` are the Pine scripts with one side each. Same
rules, plus the BOS number: continuation breaks are counted since the trend turned (BOS 1, 2, ...)
and `--cont` picks which ones are traded. Swing sizes are in candles (24 / 84 = 2 / 7 days on 2h).

```bash
python run_backtest_ms_long.py                     # longs only, results/ms_long_*.csv
python run_backtest_ms_short.py --cont first2      # shorts only, results/ms_short_*.csv
python run_backtest_ms_sides.py --timeframe 2h     # both on their own, plus the total per year
```

Run separately, a long and a short can be open at the same time (as with the two scripts on
TradingView). The live bot still trades the combined strategy (one position per coin).

### Several coins

```bash
python run_backtest_ms_multi.py                              # BTC,ETH,SOL,XRP,DOGE,BNB
python run_backtest_ms_multi.py --coins BTC,ETH --split 2024-06-01
```

Per coin a train (before `--split`) and a test result; a coin passes if it makes money in both.
The portfolio line adds up the passing coins as one bot per coin on the same account, with the
most positions open at once. Saved in `results/ms_multi_trades.csv` and `ms_multi_summary.csv`.

Results: the report in the terminal, plus `results/ms_trades.csv` (every trade with its result in R,
planned R, stop %, leverage and setup BOS/CHoCH), `ms_equity.csv`, `ms_structure.csv` (trend, HTF
trend, breaks and both plans per candle) and `ms_pivots.csv` (the swings, with `how` = swing or bos).

Your TradingView backtest ran on the `INDEX:BTCUSD` chart. `--signal-data index` (the default) uses
OKX's BTC index, which is close but not identical, so expect small differences in the swings. Fills
always use the OKX perpetual.

### Live bot

```bash
python run_live_ms.py                       # demo
python run_live_ms.py --live                # real money, asks you to type YES
```

- Entry orders carry the stop-loss (and the take-profit with `--use-target`) attached on OKX.
- The trailing stop is moved at each candle close: the new stop is placed first, then the old one
  is cancelled. **It only moves while the bot runs**; when it's off, the last stop stays in place.
- Leverage is set per trade (at most `--max-leverage`). State, including which HL/LH was already
  traded, is saved in `state/BTC-PERP_ms_demo.json`.
- **More coins:** start one bot per coin, each in its own terminal (`--base ETH`, ...). Each has its
  own state file and log (`logs/ms_ETH_<date>.log`) and risks `--risk` % of the whole account.
  `--max-open` (default 3) blocks new entries while that many positions are open on the account.
