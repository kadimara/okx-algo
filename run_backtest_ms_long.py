"""
Backtest the "MS strategy long" Pine script (ms_strategy_long.md): the MS structure
strategy with LONGS ONLY. Same options as run_backtest_ms.py; the defaults are the
long Pine script's inputs:
  swing 16 / HTF 96 candles, internal structure on
  setups: first and second BOS only, no CHoCH, only while the HTF is bullish
  entry 0.2% above the HL body, stop 0.15% below the HL low, skip stops under 0.4%
  no R filter (target = last HTF swing high, only shown as the potential R)
  exits: no take-profit, break-even at 2R (stop to entry + 0.3%, covers the fees),
         candle trail from 0R behind the last 8 candles

Examples:
  python run_backtest_ms_long.py --timeframe 15m
  python run_backtest_ms_long.py --timeframe 15m --cont all --choch --no-htf-filter
  python run_backtest_ms_long.py --signal-data perp --maker-fee 0.05 --taker-fee 0.05 --slippage 0   (compare with TradingView)

Results: results/ms_long_trades.csv, ms_long_equity.csv, ms_long_structure.csv, ms_long_pivots.csv
"""
from run_backtest_ms import main

# the long Pine script's inputs (a --X or --long-X on the command line wins)
LONG_SCRIPT = dict(swing_candles=16, htf_candles=96, side_defaults={
    ("long", "cont"): "first2", ("long", "no-choch"): True, ("long", "no-htf-filter"): False,
    ("long", "entry-offset"): 0.2, ("long", "stop-offset"): 0.15, ("long", "min-stop"): 0.4,
    ("long", "target-offset"): 0.0, ("long", "no-rr-filter"): True, ("long", "htf-target"): True,
    ("long", "use-target"): False, ("long", "be-after-r"): 2.0, ("long", "be-offset"): 0.3,
    ("long", "no-trail"): False, ("long", "trail-after-r"): 0.0, ("long", "trail-candles"): 8,
})

if __name__ == "__main__":
    main("Backtest the MS strategy, longs only", prefix="ms_long", side="long", **LONG_SCRIPT)
