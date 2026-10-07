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
as your final TradingView settings. Longs and shorts on the `BTC/USDT:USDT` perpetual, 2h candles.

- **Structure:** swings of 2 days, HH/HL/LH/LL labels, and break of structure (BOS): a candle that
  closes through the last swing low/high flips the trend at once.
- **Long** (bullish trend): buy limit 0.35% above the HL candle's body bottom (lower of open/close),
  stop 0.15% below the HL low. **Short** is mirrored on the LH.
- **Target:** the newest swing on the other side (body edge). Used for the R filter (skip trades
  below 3R); the take-profit order itself is off by default.
- **Exits:** a candle trailing stop: from the candle after the fill, the stop follows the lowest
  low (long) / highest high (short) of the last 12 candles, -/+ the stop offset, only in your favour.
- One trade per HL/LH. Size: 1% of equity at the stop, capped at 2x leverage.

```
structure.py                analyze_bos_structure(): swings, labels, BOS, body edges
strategies/ms_structure.py  the strategy: entry, stop, target and filters per candle
ms_backtester.py            backtest engine (fills, one trade per pivot, trail, break-even, sizing)
okx_ms_trader.py            live/demo engine on OKX perpetuals
run_backtest_ms.py          app: backtest
run_live_ms.py              app: live bot (demo by default)
```

### Backtest

```bash
python run_backtest_ms.py
python run_backtest_ms.py --signal-data perp --maker-fee 0.05 --taker-fee 0.05 --slippage 0   # compare with TradingView
python run_backtest_ms.py --use-target --min-rr 0
python run_backtest_ms.py --no-trail --use-target --be-after-r 2
```

| Option | Default | Pine input |
|---|---|---|
| `--swing-days` | 2 | Swing size (days) |
| `--side` | both | Take longs / Take shorts |
| `--entry-offset` | 0.35 | Entry offset (%) |
| `--stop-offset` | 0.15 | Stop offset (%) |
| `--min-rr` | 3 | Skip trades below (R, 0 = off) |
| `--target-offset` | 0 | Target offset (%) |
| `--risk` | 1 | Risk per trade (% of equity) |
| `--max-leverage` | 2 | Max leverage (x) |
| `--use-target` | off | Use target |
| `--be-after-r` | 0 | Move stop to entry after (R, 0 = off) |
| `--no-trail` | trail on | Candle trailing stop |
| `--trail-after-r` | 0 | Start trailing after (R) |
| `--trail-candles` | 12 | Trail behind the last N candles |

Results: the report in the terminal, plus `results/ms_trades.csv` (every trade with its result in R,
planned R, stop % and leverage), `ms_equity.csv`, `ms_structure.csv` (trend and both plans per
candle) and `ms_pivots.csv` (the swings, with `how` = swing or bos).

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
