"""
Backtest the MS structure strategy on OKX perpetual futures (same rules as the
"MS strategy" Pine script). Defaults = the Pine inputs (BTC 4h, no HTF filter, long
entry 0.45% / min 1.25R / trail 16 candles, short entry 0.2% / min 2R / trail 2 candles,
break-even and trailing from 2R), with 0.1% fees.
The earlier 2h sweep settings: --timeframe 2h --entry-offset 0.35 --long-min-rr 1.5
  --short-htf-filter --target-offset 0.1 --be-after-r 0 --trail-after-r 1 --trail-candles 24

Examples:
  python run_backtest_ms.py
  python run_backtest_ms.py --signal-data perp --maker-fee 0.05 --taker-fee 0.05 --slippage 0   (compare with TradingView)
  python run_backtest_ms.py --use-target --min-rr 0
  python run_backtest_ms.py --no-trail --use-target --be-after-r 2
  python run_backtest_ms.py --no-htf-filter --long-no-choch --short-min-rr 3
  python run_backtest_ms.py --cont first2 --swing-candles 24 --htf-candles 84
Long only / short only (the "MS strategy long / short" Pine scripts): run_backtest_ms_long.py,
run_backtest_ms_short.py, and run_backtest_ms_sides.py for both side by side.
"""
import argparse

import ccxt

from futures_backtester import print_report
from ms_backtester import ExitParams, MSBacktester, save_ms_results
from structure import bos_pivot_list
from strategies.ms_structure import MSStructure, SideParams
from vwap_data import SIGNAL_KINDS, cached, fetch_perp, fetch_signal


# per-side numeric options: (name, default, help); --<name> sets both sides,
# --long-<name> / --short-<name> override one side
SIDE_NUM = [
    ("entry-offset", 0.35, "%% above the HL body bottom (long) / below the LH body top (short)"),
    ("stop-offset", 0.0, "%% beyond the HL low / LH high; also used for the trailing stop"),
    ("min-rr", 2.0, "skip trades below this R, 0 = off"),
    ("target-offset", 0.0, "%% before the target (swing body edge)"),
    ("min-stop", 0.0, "skip setups with a stop closer than this %% of the entry, 0 = off"),
    ("be-after-r", 2.0, "move stop to entry after R, 0 = off"),
    ("be-offset", 0.0, "break-even stop this %% beyond the entry (in profit), e.g. to cover the fees"),
    ("trail-after-r", 2.0, "start trailing after R"),
    ("trail-candles", 16, "trail behind the last N candles"),
]
# per-side switches: --<name> for both sides, --long-<name> / --short-<name> for one
SIDE_FLAGS = [
    ("use-target", "place the take-profit (off by default)"),
    ("no-trail", "switch the candle trailing stop off"),
    ("no-choch", "skip pullback-end setups (CHoCH)"),
    ("no-htf-filter", "also trade against the HTF trend"),
    ("no-rr-filter", "no min R; the target only has to be beyond the entry with --use-target"),
    ("htf-target", "with the HTF filter: the target (and planned R) is the last HTF swing"),
]
CONT_MODES = ("none", "first", "first2", "all")
INVERSE_FLAGS = [
    ("htf-filter", "only trade with the HTF trend (undoes --no-htf-filter)"),
    ("choch", "take pullback-end setups (CHoCH) (undoes --no-choch)"),
    ("rr-filter", "use the min R filter (undoes --no-rr-filter)"),
    ("trail", "use the candle trailing stop (undoes --no-trail)"),
]
# defaults for one side that differ from the shared default (used unless --X or --side-X is given)
SIDE_DEFAULTS = {("long", "entry-offset"): 0.45, ("short", "entry-offset"): 0.2,
                 ("long", "min-rr"): 1.25, ("long", "trail-candles"): 16, ("short", "trail-candles"): 2,
                 ("long", "no-htf-filter"): True, ("short", "no-htf-filter"): True}


def add_strategy_args(p):
    g = p.add_argument_group("market and data")
    g.add_argument("--base", default="BTC", help="coin, traded as <base>/USDT perpetual")
    g.add_argument("--timeframe", default="4h", help="15m 30m 1h 2h 4h 1d ...")
    g.add_argument("--signal-data", default="index", choices=SIGNAL_KINDS,
                   help="index = OKX index + aggregated volume, perp = perpetual price + own volume, "
                        "usdc = OKX <coin>/USDC spot (TradingView OKX:BTCUSDC)")
    g.add_argument("--exchanges", default=None, help="exchanges for aggregated volume (see aggregate_volume.py)")
    g = p.add_argument_group("structure (same names as the Pine inputs)")
    g.add_argument("--swing-days", type=float, default=2.0, help="swing size in days")
    g.add_argument("--htf-days", type=float, default=7.0, help="HTF swing size in days")
    g.add_argument("--swing-candles", type=int, default=None, help="swing size in candles (replaces --swing-days)")
    g.add_argument("--htf-candles", type=int, default=None, help="HTF swing size in candles (replaces --htf-days)")
    g.add_argument("--no-internal", action="store_true",
                   help="only real swings (no LH / HL from the extreme candle at a break)")
    g.add_argument("--side", default="both", choices=["both", "long", "short"], help="take longs / shorts")
    g.add_argument("--risk", type=float, default=1.0, help="risk per trade in %% of equity")
    g.add_argument("--max-leverage", type=float, default=2.0, help="cap on position value / equity")
    g = p.add_argument_group("entries and exits (both sides; --long-X / --short-X override one side)")
    for name, default, text in SIDE_NUM:
        typ = type(default)
        lo, sh = (SIDE_DEFAULTS.get((s, name), default) for s in ("long", "short"))
        shown = f"{lo}" if lo == sh else f"long {lo}, short {sh}"
        g.add_argument(f"--{name}", type=typ, default=None, help=f"{text} (default {shown})")
        for side in ("long", "short"):
            g.add_argument(f"--{side}-{name}", type=typ, default=None, help=argparse.SUPPRESS)
    for name, text in SIDE_FLAGS:
        g.add_argument(f"--{name}", action="store_const", const=True, default=None, help=text)
        for side in ("long", "short"):
            g.add_argument(f"--{side}-{name}", action="store_const", const=True, default=None,
                           help=argparse.SUPPRESS)
    g.add_argument("--cont", choices=CONT_MODES, default=None,
                   help="continuation setups: none, first (BOS 1 only), first2 (BOS 1 and 2), all (default)")
    for side in ("long", "short"):
        g.add_argument(f"--{side}-cont", choices=CONT_MODES, default=None, help=argparse.SUPPRESS)
    # switch a "no-..." option back on (when a script's default has it off)
    for name, text in INVERSE_FLAGS:
        key = f"no_{name.replace('-', '_')}"
        g.add_argument(f"--{name}", dest=key, action="store_const", const=False, help=text)
        for side in ("long", "short"):
            g.add_argument(f"--{side}-{name}", dest=f"{side}_{key}", action="store_const",
                           const=False, help=argparse.SUPPRESS)


DEFAULTS = {name: default for name, default, _ in SIDE_NUM} | {name: False for name, _ in SIDE_FLAGS} | \
    {"cont": "all"}


def side_value(args, side: str, name: str):
    """--long-X / --short-X if given, else --X, else the script's default for that side
    (run_backtest_ms_long.py etc.), else the side's default, else the shared default."""
    key = name.replace("-", "_")
    for value in (getattr(args, f"{side}_{key}"), getattr(args, key)):
        if value is not None:
            return value
    script = getattr(args, "side_defaults", None) or {}
    if (side, name) in script:
        return script[(side, name)]
    return SIDE_DEFAULTS.get((side, name), DEFAULTS[name])


def make_strategy(args) -> MSStructure:
    def side_params(side):
        v = lambda name: side_value(args, side, name)
        return SideParams(enabled=args.side in ("both", side), entry_pct=v("entry-offset"),
                          stop_pct=v("stop-offset"), target_pct=v("target-offset"), min_rr=v("min-rr"),
                          cont=v("cont"), choch=not v("no-choch"), htf=not v("no-htf-filter"),
                          rr_filter=not v("no-rr-filter"), use_target=v("use-target"),
                          htf_target=v("htf-target"), min_stop_pct=v("min-stop"))
    return MSStructure(ccxt.Exchange.parse_timeframe(args.timeframe), swing_days=args.swing_days,
                       htf_days=args.htf_days, swing_candles=args.swing_candles,
                       htf_candles=args.htf_candles, use_internal=not args.no_internal,
                       long=side_params("long"), short=side_params("short"))


def make_exits(args) -> dict:
    def exit_params(side):
        v = lambda name: side_value(args, side, name)
        return ExitParams(use_target=v("use-target"), be_after_r=v("be-after-r"),
                          be_offset_pct=v("be-offset"), trail=not v("no-trail"),
                          trail_after_r=v("trail-after-r"), trail_candles=v("trail-candles"),
                          stop_pct=v("stop-offset"))
    return {"long": exit_params("long"), "short": exit_params("short")}


def exit_settings(args) -> str:
    ex = make_exits(args)
    lo, sh = ex["long"].describe(), ex["short"].describe()
    if args.side != "both":
        exits = ex[args.side].describe()
    else:
        exits = lo if lo == sh else f"long {lo} | short {sh}"
    return exits + f", risk {args.risk}%, max {args.max_leverage}x"


def build_parser(description: str = "Backtest the MS structure strategy on OKX perpetuals",
                 **defaults) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    add_strategy_args(p)
    g = p.add_argument_group("backtest")
    g.add_argument("--days", type=int, default=365, help="days to trade in the test")
    g.add_argument("--warmup-days", type=int, default=60,
                   help="extra history before the test (the 7-day HTF swings need time to settle)")
    g.add_argument("--cash", type=float, default=10_000)
    g.add_argument("--maker-fee", type=float, default=0.1, help="%% on limit entries and targets")
    g.add_argument("--taker-fee", type=float, default=0.1, help="%% on stops")
    g.add_argument("--slippage", type=float, default=0.02, help="%% on stops")
    g.add_argument("--funding", type=float, default=0.01, help="%% per 8h; longs pay, shorts receive")
    g.add_argument("--refresh", action="store_true", help="re-download instead of using today's cache")
    p.set_defaults(**defaults)
    return p


def load_data(args):
    """Signal candles and traded (perp) candles, cached per day."""
    total_days = args.days + args.warmup_days
    since = ccxt.Exchange.milliseconds() - total_days * 86_400_000
    tag = f"{args.base}_{args.timeframe}_{total_days}d"

    signal = cached(f"{tag}_{args.signal_data}_signal",
                    lambda: fetch_signal(args.signal_data, args.base, args.timeframe, since,
                                         args.exchanges, verbose=True), args.refresh)
    trade = signal if args.signal_data == "perp" else cached(
        f"{tag}_perp", lambda: fetch_perp(args.base, args.timeframe, since, verbose=True), args.refresh)
    return signal, trade


def backtest(args, signal, trade):
    """Run the strategy with these args; returns (result, plan)."""
    strategy, exits = make_strategy(args), make_exits(args)
    tf_sec = ccxt.Exchange.parse_timeframe(args.timeframe)
    name = f"{strategy.describe()} | {exit_settings(args)}"
    print(f"Analyzing ({name})...")
    plan = strategy.analyze(signal)
    warmup = max(int(args.warmup_days * 86400 / tf_sec), strategy.warmup_candles)
    if warmup >= len(plan):
        raise SystemExit(f"Not enough data: {len(plan)} candles, warm-up needs {warmup}. Use more --days.")

    bt = MSBacktester(plan, trade, starting_cash=args.cash, risk_pct=args.risk,
                      max_leverage=args.max_leverage, maker_fee_pct=args.maker_fee,
                      taker_fee_pct=args.taker_fee, slippage_pct=args.slippage,
                      funding_pct_8h=args.funding, long_exit=exits["long"], short_exit=exits["short"],
                      warmup_candles=warmup, strategy_name=name)
    return bt.run(), plan


def save(result, plan, signal, prefix: str = "ms"):
    folder = save_ms_results(result, plan, prefix=prefix)
    bos_pivot_list(signal, plan).to_csv(folder / f"{prefix}_pivots.csv", index=False)
    print(f"\nSaved in ./{folder}/: {prefix}_trades.csv, {prefix}_equity.csv, "
          f"{prefix}_structure.csv, {prefix}_pivots.csv")


def main(description: str = None, prefix: str = "ms", **defaults):
    args = build_parser(description or "Backtest the MS structure strategy on OKX perpetuals",
                        **defaults).parse_args()
    signal, trade = load_data(args)
    result, plan = backtest(args, signal, trade)
    print_report(result)
    save(result, plan, signal, prefix)


if __name__ == "__main__":
    main()
