"""
App 1: run a backtest.

Examples:
  python run_backtest.py
  python run_backtest.py --symbol ETH/USDT --timeframe 1h --days 180 --fast 12 --slow 26
  python run_backtest.py --ma-type ema --stop-loss 0      (EMA, no stop-loss)
  python run_backtest.py --signal-data spot               (signals from spot instead of index)
"""
import argparse
from datetime import date
from pathlib import Path

import ccxt
import pandas as pd

from backtester import Backtester, print_report, save_results
from market_data import fetch_candles
from strategies import MACrossover


def load_history(symbol, timeframe, days, price="spot", refresh=False):
    """Download once per day and cache, so re-runs are instant."""
    Path("data").mkdir(exist_ok=True)
    name = f"{symbol.replace('/', '-')}_{price}_{timeframe}_{days}d_{date.today()}.csv"
    cache = Path("data") / name
    if cache.exists() and not refresh:
        print(f"Using cached data: {cache}")
        return pd.read_csv(cache, index_col="time", parse_dates=True)

    okx = ccxt.okx({"enableRateLimit": True})  # public data, no keys needed
    print(f"Downloading {days} days of {symbol} {timeframe} {price} candles from OKX...")
    since = okx.milliseconds() - days * 24 * 60 * 60 * 1000
    df = fetch_candles(okx, symbol, timeframe, since, price=price, verbose=True)
    if df.empty:
        raise SystemExit("No data returned. Check the symbol (e.g. BTC/USDT) and timeframe.")
    df.to_csv(cache)
    return df


def main():
    p = argparse.ArgumentParser(description="Backtest the MA crossover strategy on OKX data")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="15m", help="1m 5m 15m 30m 1h 4h 1d ...")
    p.add_argument("--days", type=int, default=90, help="how much history to test")
    p.add_argument("--fast", type=int, default=9, help="fast MA length")
    p.add_argument("--slow", type=int, default=21, help="slow MA length")
    p.add_argument("--ma-type", default="sma", choices=["sma", "ema"])
    p.add_argument("--signal-data", default="index", choices=["index", "spot"],
                   help="price the strategy looks at (orders always use spot)")
    p.add_argument("--cash", type=float, default=1000.0, help="starting cash in quote currency")
    p.add_argument("--fee", type=float, default=0.1, help="fee per trade in %% (default 0.1)")
    p.add_argument("--slippage", type=float, default=0.05, help="slippage in %% (default 0.05)")
    p.add_argument("--stop-loss", type=float, default=3.0, help="stop-loss in %%, 0 = off")
    p.add_argument("--refresh", action="store_true", help="re-download instead of using cache")
    args = p.parse_args()

    try:
        strategy = MACrossover(args.fast, args.slow, args.ma_type)
    except ValueError as e:
        p.error(str(e))

    candles = load_history(args.symbol, args.timeframe, args.days, "spot", args.refresh)
    signal_candles = None
    if args.signal_data == "index":
        signal_candles = load_history(args.symbol, args.timeframe, args.days, "index", args.refresh)
    bt = Backtester(strategy, candles, starting_cash=args.cash, fee_pct=args.fee,
                    slippage_pct=args.slippage, stop_loss_pct=args.stop_loss or None,
                    signal_candles=signal_candles, signal_data=args.signal_data)
    print("Running backtest...")
    result = bt.run()
    print_report(result, quote=args.symbol.split("/")[1])
    folder = save_results(result)
    print(f"\nTrade list and equity curve saved in ./{folder}/")


if __name__ == "__main__":
    main()
