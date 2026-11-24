"""
Backtest the "MS strategy short" Pine script: the MS structure strategy with SHORTS ONLY.
Same options as run_backtest_ms.py; the defaults follow the short Pine script:
swing 24 / HTF 84 candles, all BOS + CHoCH setups, no HTF filter, entry 0.2%,
min 2R, break-even and trailing from 2R behind 2 candles.

Examples:
  python run_backtest_ms_short.py
  python run_backtest_ms_short.py --cont first2 --htf-filter
  python run_backtest_ms_short.py --signal-data perp --maker-fee 0.05 --taker-fee 0.05 --slippage 0   (compare with TradingView)

Results: results/ms_short_trades.csv, ms_short_equity.csv, ms_short_structure.csv, ms_short_pivots.csv
"""
from run_backtest_ms import main

if __name__ == "__main__":
    main("Backtest the MS strategy, shorts only", prefix="ms_short",
         side="short", swing_candles=24, htf_candles=84)
