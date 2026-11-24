"""
App 2: run the trading bot. Uses OKX DEMO trading unless you add --live.

Examples:
  python run_live.py
  python run_live.py --symbol ETH/USDT --timeframe 1h --fast 12 --slow 26 --size 0.01
  python run_live.py --live --size 0.0005          (real money, asks for confirmation)
  python run_live.py --signal-data spot            (signals from spot instead of index)

Stop with Ctrl+C. An open position is kept and remembered for the next start.
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from okx_trader import OKXTrader
from strategies import MACrossover


def setup_logging(name: str = "trader", tag: str = ""):
    """Log to the terminal and logs/<name>_<date>.log; tag (e.g. the coin) goes in every line."""
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  " + (f"[{tag}] " if tag else "") + "%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(Path("logs") / f"{name}_{date.today()}.log")],
    )


def main():
    p = argparse.ArgumentParser(description="Run the MA crossover bot on OKX")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="15m", help="1m 5m 15m 30m 1h 4h 1d ...")
    p.add_argument("--fast", type=int, default=9, help="fast MA length")
    p.add_argument("--slow", type=int, default=21, help="slow MA length")
    p.add_argument("--ma-type", default="sma", choices=["sma", "ema"])
    p.add_argument("--signal-data", default="index", choices=["index", "spot"],
                   help="price the strategy looks at (orders always use spot)")
    p.add_argument("--size", type=float, default=0.001, help="order size in the base coin (e.g. BTC)")
    p.add_argument("--stop-loss", type=float, default=3.0, help="stop-loss in %%, 0 = off")
    p.add_argument("--check-every", type=int, default=30, help="seconds between checks")
    p.add_argument("--live", action="store_true", help="trade with REAL money")
    args = p.parse_args()

    try:
        strategy = MACrossover(args.fast, args.slow, args.ma_type)
    except ValueError as e:
        p.error(str(e))

    if args.live:
        print("\n  WARNING: LIVE MODE. This bot will place REAL orders with REAL money.")
        print(f"  {strategy.describe()} on {args.symbol} {args.timeframe} "
              f"({args.signal_data} signals), "
              f"size {args.size}, stop-loss {args.stop_loss or 'off'}%\n")
        if input("  Type YES to continue: ").strip() != "YES":
            print("Cancelled.")
            return

    load_dotenv()
    setup_logging()
    trader = OKXTrader(strategy, args.symbol, args.timeframe, args.size,
                       stop_loss_pct=args.stop_loss or None, live=args.live,
                       check_every=args.check_every, signal_data=args.signal_data)
    try:
        trader.run()
    except KeyboardInterrupt:
        logging.info("Stopped by you. State saved; any open position is remembered.")


if __name__ == "__main__":
    main()
