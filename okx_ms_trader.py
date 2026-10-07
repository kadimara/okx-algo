"""
Live/demo engine for the MS structure strategy on OKX perpetual futures.

Builds on OKXFuturesTrader (account setup, fills, protection, state file) and
replaces the decisions. Every cycle it syncs the position; when a new candle
has closed:
  1. Updates the signal candles and recalculates the strategy (same code as the
     backtest), giving the long and short plan for the next candle.
  2. In a position, from the candle after the fill: break-even and the candle
     trailing stop on the perpetual candles that just closed (same rules as the
     backtest). A better stop is placed on OKX first, then the old one is cancelled.
  3. Flat: cancels the previous entry order and, if there is a setup whose HL /
     LH hasn't been traded yet, places a limit order with the stop-loss (and
     the take-profit if enabled) ATTACHED, so a position is protected even if
     the bot is offline. Size = risk % of equity at the stop, capped at
     max_leverage x equity; leverage on OKX is set per trade.
"""
import logging
import math

import ccxt

from market_data import fetch_recent
from okx_futures_trader import MARGIN, OKXFuturesTrader
from pathlib import Path

log = logging.getLogger("futures")


class OKXStructureTrader(OKXFuturesTrader):
    def __init__(self, strategy, base: str, timeframe: str, signal_kind: str = "index",
                 exchanges=None, risk_pct: float = 1.0, max_leverage: float = 2.0,
                 use_target: bool = False, be_after_r: float = 0.0, trail: bool = True,
                 trail_after_r: float = 0.0, trail_candles: int = 12, stop_pct: float = 0.15,
                 live: bool = False, check_every: int = 30, history_days: int = 60):
        super().__init__(strategy, base, timeframe, signal_kind=signal_kind, exchanges=exchanges,
                         risk_pct=risk_pct, be_after_r=be_after_r, be_offset_pct=0.0,
                         re_arm_atr=0.0, max_per_trend=0, live=live,
                         check_every=check_every, history_days=history_days)
        self.max_lev, self.use_target = max_leverage, use_target
        self.trail, self.trail_r, self.trail_n = trail, trail_after_r, max(1, int(trail_candles))
        self.stop_off = stop_pct / 100
        mode = "live" if live else "demo"
        self.state_file = Path("state") / f"{self.base}-PERP_ms_{mode}.json"
        self.state.update({"used_long": None, "used_short": None})

    # ------------------------------------------------------------- main loop
    def run(self):
        log.info("Exits: %s | risk %.2f%% | max %.1fx", self.exit_settings(), self.risk * 100, self.max_lev)
        super().run()

    def exit_settings(self) -> str:
        parts = ["target on" if self.use_target else "no target"]
        if self.be_r > 0:
            parts.append(f"break-even after {self.be_r}R")
        if self.trail:
            parts.append(f"trail {self.trail_n} candles from {self.trail_r}R")
        return ", ".join(parts)

    def on_candle(self):
        plan = self.strategy.analyze(self.signal).iloc[-1]
        when = self.signal.index[-1].strftime("%Y-%m-%d %H:%M")
        log.info("Candle %s | trend %s | last high %s %.2f | last low %s %.2f", when, plan["trend"],
                 plan["high_tag"], plan["last_high"], plan["low_tag"], plan["last_low"])

        if self.state.get("position"):
            self.manage_stop()
            return
        if not self.cancel_entry():
            return  # the order filled meanwhile; sync_position takes over

        for side in ("long", "short"):
            if not plan[f"{side}_ok"]:
                continue
            pivot = self.pivot_time(int(plan[f"{side[0]}_id"]))
            if pivot == self.state.get(f"used_{side}"):
                log.info("%s setup on the swing %s of %s was already traded.", side.capitalize(),
                         "low" if side == "long" else "high", pivot)
                return
            self.place_order(side, float(plan[f"{side[0]}_entry"]), float(plan[f"{side[0]}_stop"]),
                             float(plan[f"{side[0]}_target"]), float(plan[f"{side[0]}_rr"]), pivot)
            return
        log.info("No setup (trend %s).", plan["trend"])

    def pivot_time(self, idx: int) -> str:
        return self.signal.index[idx].isoformat() if 0 <= idx < len(self.signal) else str(idx)

    # ------------------------------------------------------------- orders
    def place_order(self, side: str, entry: float, stop: float, target: float, rr: float, pivot: str):
        equity = self.equity()
        dist = abs(entry - stop)
        qty = min(equity * self.risk / dist, equity * self.max_lev / entry)
        contracts = float(self.ex.amount_to_precision(self.symbol, qty / self.contract_size))
        if contracts < self.min_contracts or contracts <= 0:
            log.warning("Position size %.6f %s is below OKX's minimum (%s contracts). Skipping.",
                        qty, self.base, self.min_contracts)
            return
        needed = contracts * self.contract_size * entry / equity
        leverage = min(self.max_leverage, max(1, math.ceil(needed * 1.1)))  # headroom for fees
        try:
            self.ex.set_leverage(leverage, self.symbol, {"marginMode": MARGIN})
        except ccxt.BaseError as e:
            log.warning("Could not set leverage %sx (%s).", leverage, str(e)[:100])
        params = {"marginMode": MARGIN, "stopLoss": {"triggerPrice": stop, "type": "market"}}
        if self.use_target:
            params["takeProfit"] = {"triggerPrice": target, "type": "limit", "price": target}
        order = self.ex.create_order(self.symbol, "limit", "buy" if side == "long" else "sell",
                                     contracts, entry, params)
        self.state["pending"] = {"id": order["id"], "side": side, "entry": entry, "stop": stop,
                                 "target": target if self.use_target else None, "risk": dist,
                                 "contracts": contracts, "pivot": pivot}
        self.save_state()
        log.info("Placed %s limit: %s contracts (%.4f %s) @ %.2f | stop %.2f (%.2f%%) | %s | "
                 "%.2fR potential | risk %.2f USDT | %.2fx", side.upper(), contracts,
                 contracts * self.contract_size, self.base, entry, stop, dist / entry * 100,
                 f"target {target:.2f}" if self.use_target else "no target", rr,
                 contracts * self.contract_size * dist, needed)

    # ------------------------------------------------------------- fills: one trade per HL / LH
    def mark_used(self, pend: dict):
        if pend and pend.get("pivot"):
            self.state[f"used_{pend['side']}"] = pend["pivot"]
            self.save_state()

    def sync_position(self):
        pend, had = self.state.get("pending"), self.state.get("position")
        super().sync_position()
        p = self.state.get("position")
        if pend and p and not had:
            p["stop0"], p["best"], p["trailed"] = pend["stop"], None, False
            self.mark_used(pend)

    def check_missed_trade(self):
        pend = self.state.get("pending")
        super().check_missed_trade()
        if pend and self.state.get("pending") is None:   # it had filled
            self.mark_used(pend)

    # ------------------------------------------------------------- stop management
    def check_break_even(self):
        pass   # replaced by manage_stop()

    def manage_stop(self):
        """Break-even and candle trail at the candle close, from the candle after the fill."""
        p = self.state["position"]
        if not p.get("stop0") or (self.be_r <= 0 and not self.trail):
            return
        candles = fetch_recent(self.ex, self.symbol, self.timeframe, self.trail_n)
        if candles.empty:
            return
        last_start = int(candles.index[-1].timestamp() * 1000)
        if last_start <= p["fill_ms"] // self.bar_ms * self.bar_ms:
            return  # only from the candle after the fill
        long = p["side"] == "long"
        ep = float(p["entry"])
        hi, lo = float(candles["high"].iloc[-1]), float(candles["low"].iloc[-1])
        best = hi if long else lo
        if p.get("best") is not None:
            best = max(p["best"], best) if long else min(p["best"], best)
        p["best"] = best
        r0 = (ep - p["stop0"]) if long else (p["stop0"] - ep)
        move_r = ((best - ep) if long else (ep - best)) / r0 if r0 > 0 else 0.0

        new, tag = p["stop"], None
        if self.be_r > 0 and not p.get("be") and move_r >= self.be_r:
            p["be"] = True
            if (ep > new) if long else (ep < new):
                new, tag = ep, "Break-even"
        if self.trail and move_r >= self.trail_r:
            ts = float(candles["low"].min()) * (1 - self.stop_off) if long \
                else float(candles["high"].max()) * (1 + self.stop_off)
            if (ts > new) if long else (ts < new):
                new, tag = ts, "Trailing stop"
                p["trailed"] = True
        self.save_state()
        if tag:
            self.replace_stop(new, tag)

    def replace_stop(self, new_stop: float, tag: str):
        """Place the new stop first, then cancel the old one(s): never unprotected."""
        p = self.state["position"]
        close_side = "sell" if p["side"] == "long" else "buy"
        new_stop = float(self.ex.price_to_precision(self.symbol, new_stop))
        old = [o for o in self.algo_orders() if self.order_field(o, "stopLossPrice", "slTriggerPx")]
        try:
            self.ex.create_order(self.symbol, "market", close_side, p["contracts"], None,
                                 {"stopLossPrice": new_stop, "reduceOnly": True, "marginMode": MARGIN})
        except ccxt.InvalidOrder as e:
            # e.g. price already beyond the new stop: close now rather than leave it worse
            log.warning("Could not place the new stop at %.2f (%s). Closing at market.", new_stop, str(e)[:100])
            self.close_position(f"{tag.lower()} level already passed")
            return
        for o in old:
            try:
                self.ex.cancel_order(o["id"], self.symbol, {"trigger": True})
            except ccxt.BaseError as e:
                log.warning("Could not cancel the old stop %s: %s", o["id"], str(e)[:100])
        p["stop"] = new_stop
        self.save_state()
        log.info("%s: stop moved to %.2f.", tag, new_stop)
        self.ensure_target()

    def ensure_target(self):
        """The attached stop and take-profit can be one OCO order on OKX; cancelling
        the old stop then also removes the target. Put it back if needed."""
        p = self.state["position"]
        if not (self.use_target and p.get("target")):
            return
        has_tp = any(self.order_field(o, "takeProfitPrice", "tpTriggerPx") for o in self.algo_orders()) or \
            any(o.get("reduceOnly") for o in self.ex.fetch_open_orders(self.symbol))
        if not has_tp:
            try:
                self.ex.create_order(self.symbol, "limit", "sell" if p["side"] == "long" else "buy",
                                     p["contracts"], p["target"], {"reduceOnly": True, "marginMode": MARGIN})
                log.info("Take-profit placed again at %.2f.", p["target"])
            except ccxt.BaseError as e:
                log.error("Could not place the take-profit (%s). The stop-loss stays active.", str(e)[:120])
