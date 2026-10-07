"""
Live/demo engine for the MS VWAP strategy on OKX perpetual futures (USDT-margined).

Every cycle (default 30 s) it syncs the position; when a new candle has closed:
  1. Updates the signal candles and recalculates the strategy (same code as the
     backtest), giving the plan for the next candle.
  2. In a position: break-even check on the perpetual candle that just closed
     (from the candle after the fill); if reached, the stop on OKX is moved.
  3. Anti-chop gate update (same TradeGate as the backtest, saved in the state file).
  4. Flat and allowed: cancels the previous entry order and places a new limit
     order at the VWAP with the stop-loss and take-profit ATTACHED. OKX places
     them the moment the entry fills, so a position is protected even if this
     bot is offline. Leverage is set per trade to what the risk % needs.
When a fill is detected, the stop-loss is verified; if it's missing one is
placed, and if that fails the position is closed. A position is never kept
without a stop.

Account setup: isolated margin, one-way (net) position mode.
"""
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import ccxt

from market_data import fetch_recent
from okx_trader import create_exchange
from strategies.ms_vwap import TradeGate
from vwap_data import extend, fetch_signal, perp_symbol

log = logging.getLogger("futures")
MARGIN = "isolated"


class OKXFuturesTrader:
    def __init__(self, strategy, base: str, timeframe: str, signal_kind: str = "index",
                 exchanges: Optional[str] = None, risk_pct: float = 1.0,
                 be_after_r: float = 1.0, be_offset_pct: float = 1.0,
                 re_arm_atr: float = 2.0, max_per_trend: int = 2, live: bool = False,
                 check_every: int = 30, history_days: int = 90):
        self.strategy, self.base, self.timeframe = strategy, base.upper(), timeframe
        self.signal_kind, self.exchanges = signal_kind, exchanges
        self.risk = risk_pct / 100
        self.be_r, self.be_off = be_after_r, be_offset_pct / 100
        self.gate = TradeGate(re_arm_atr, max_per_trend)
        self.live, self.check_every = live, check_every
        self.history_days = history_days

        self.ex = create_exchange(live)
        self.ex.options["defaultType"] = "swap"
        self.ex.load_markets()
        self.symbol = perp_symbol(self.base)
        if self.symbol not in self.ex.markets:
            raise SystemExit(f"{self.symbol} is not available on OKX.")
        m = self.ex.market(self.symbol)
        limits = m.get("limits", {}) or {}
        self.contract_size = float(m.get("contractSize") or 1)
        self.min_contracts = float((limits.get("amount") or {}).get("min") or 0)
        self.max_leverage = float((limits.get("leverage") or {}).get("max") or 100)
        self.bar_ms = self.ex.parse_timeframe(timeframe) * 1000

        mode = "live" if live else "demo"
        self.state_file = Path("state") / f"{self.base}-PERP_vwap_{mode}.json"
        self.state = {"pending": None, "position": None, "gate": {}, "anchor_time": None,
                      "closed_since_candle": False, "last_candle": None}
        self.signal = None
        self.keep = None
        self.last_candle: Optional[int] = None

    # ------------------------------------------------------------- startup
    def setup_account(self):
        for what, call in (
            ("one-way position mode", lambda: self.ex.set_position_mode(False, self.symbol)),
            ("isolated margin", lambda: self.ex.set_margin_mode(MARGIN, self.symbol, {"lever": 1})),
        ):
            try:
                call()
                log.info("Account: %s set.", what)
            except ccxt.BaseError as e:
                log.warning("Could not set %s (%s). If it's already set this is fine.", what, str(e)[:120])

    def load_state(self):
        if self.state_file.exists():
            self.state.update(json.loads(self.state_file.read_text()))
        self.gate.load(self.state.get("gate") or {})
        pos = self.exchange_position()
        if pos and not self.state.get("position"):
            raise SystemExit(f"There is an open {self.symbol} position this bot didn't open "
                             f"({pos['side']} {pos['contracts']} contracts). Close it on OKX or "
                             "manage it manually, then start the bot again.")
        if not pos and self.state.get("position"):
            log.info("Saved position is no longer open (closed while the bot was off).")
            self.state["position"] = None
            self.state["closed_since_candle"] = True
        self.last_candle = self.state.get("last_candle")
        if self.state.get("pending"):
            current = (self.last_candle is not None and
                       self.ex.milliseconds() < self.last_candle + 2 * self.bar_ms)
            if current:
                log.info("Keeping the waiting %s order from the last candle.", self.state["pending"]["side"])
            else:
                log.info("Cancelling an entry order from an older candle.")
                self.cancel_entry()
        if self.state.get("position"):
            p = self.state["position"]
            log.info("Resuming %s position: entry %.2f, stop %.2f, target %.2f.",
                     p["side"], p["entry"], p["stop"], p["target"])
            self.ensure_protection()
        self.save_state()

    def save_state(self):
        self.state["gate"] = self.gate.to_dict()
        self.state_file.parent.mkdir(exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2))

    # ------------------------------------------------------------- main loop
    def run(self):
        log.info("Starting %s on %s %s | signals: %s | risk %.2f%% | break-even %sR +%.2f%% | mode: %s",
                 self.strategy.describe(), self.symbol, self.timeframe, self.signal_kind,
                 self.risk * 100, self.be_r, self.be_off * 100, "LIVE (real money)" if self.live else "DEMO")
        self.setup_account()
        self.load_state()
        log.info("Equity: %.2f USDT", self.equity())
        self.load_history()

        while True:
            try:
                self.tick()
            except ccxt.NetworkError as e:
                log.warning("Network problem: %s. Retrying in 15s.", str(e)[:200])
                time.sleep(15)
                continue
            except ccxt.ExchangeError as e:
                log.error("Exchange error: %s. Retrying in 60s.", str(e)[:200])
                time.sleep(60)
                continue
            except Exception:
                log.exception("Unexpected error. Retrying in 60s.")
                time.sleep(60)
                continue
            time.sleep(self.check_every)

    def tick(self):
        self.sync_position()
        now = self.ex.milliseconds()
        delay = 20_000 if self.signal_kind == "index" else 5_000  # let all exchanges publish the candle
        if self.last_candle and now < self.last_candle + 2 * self.bar_ms + delay:
            return
        self.update_history()
        newest = int(self.signal.index[-1].timestamp() * 1000)
        if newest == self.last_candle:
            return
        self.last_candle = newest
        self.state["last_candle"] = newest
        self.on_candle()

    # ------------------------------------------------------------- data
    def load_history(self):
        since = self.ex.milliseconds() - self.history_days * 86_400_000
        log.info("Loading %d days of %s signal candles...", self.history_days, self.signal_kind)
        self.signal = fetch_signal(self.signal_kind, self.base, self.timeframe, since, self.exchanges)
        self.keep = len(self.signal) + 10
        if len(self.signal) < self.strategy.warmup_candles:
            log.warning("Only %d candles loaded; the strategy needs %d. Use more --history-days.",
                        len(self.signal), self.strategy.warmup_candles)
        log.info("Loaded %d candles.", len(self.signal))

    def update_history(self):
        since = int(self.signal.index[-1].timestamp() * 1000) - 3 * self.bar_ms
        fresh = fetch_signal(self.signal_kind, self.base, self.timeframe, since, self.exchanges)
        if not fresh.empty:
            self.signal = extend(self.signal, fresh, keep=self.keep)

    # ------------------------------------------------------------- decisions
    def on_candle(self):
        plan = self.strategy.analyze(self.signal).iloc[-1]
        when = self.signal.index[-1].strftime("%Y-%m-%d %H:%M")
        anchor_time = None
        if plan["anchor_idx"] == plan["anchor_idx"]:  # not NaN
            anchor_time = self.signal.index[int(plan["anchor_idx"])].isoformat()
        log.info("Candle %s | trend %s | VWAP %.2f (anchor %s) | ATR %.2f",
                 when, plan["trend"], plan["avwap"], anchor_time or "none", plan["atr"])

        if self.state.get("position"):
            self.check_break_even()

        # anti-chop gate: a new anchor resets it, a closed trade disarms it
        reanchor = anchor_time is not None and anchor_time != self.state.get("anchor_time")
        closed = bool(self.state.get("closed_since_candle"))
        self.state["anchor_time"], self.state["closed_since_candle"] = anchor_time, False
        can_trade = self.gate.update(reanchor, closed, plan["trend"], float(plan["close"]),
                                     float(plan["avwap"]), float(plan["atr"]))
        self.save_state()

        if self.state.get("position"):
            return
        if not self.cancel_entry():
            return  # the order filled meanwhile; sync_position takes over
        if plan["side"] in ("long", "short") and can_trade:
            self.place_entry(plan["side"], float(plan["entry"]), float(plan["stop"]),
                             float(plan["target"]), float(plan["risk"]))
        elif plan["side"] in ("long", "short"):
            log.info("Setup ignored by anti-chop (%s).", "waiting to re-arm" if not self.gate.armed
                     else f"{self.gate.trades_this_trend} trades this trend")
        else:
            log.info("No setup (trend %s).", plan["trend"])

    def check_break_even(self):
        p = self.state["position"]
        if self.be_r <= 0 or p.get("be") or not p.get("risk"):
            return
        candle = fetch_recent(self.ex, self.symbol, self.timeframe, 1)
        if candle.empty:
            return
        start = int(candle.index[-1].timestamp() * 1000)
        if start <= p["fill_ms"] // self.bar_ms * self.bar_ms:
            return  # only from the candle after the fill
        high, low = float(candle["high"].iloc[-1]), float(candle["low"].iloc[-1])
        if p["side"] == "long" and high >= p["entry"] + self.be_r * p["risk"]:
            self.move_stop(p["entry"] * (1 + self.be_off))
        elif p["side"] == "short" and low <= p["entry"] - self.be_r * p["risk"]:
            self.move_stop(p["entry"] * (1 - self.be_off))

    def move_stop(self, new_stop: float):
        """Place the new stop first, then cancel the old one(s): never unprotected."""
        p = self.state["position"]
        close_side = "sell" if p["side"] == "long" else "buy"
        old = [o for o in self.algo_orders() if self.order_field(o, "stopLossPrice", "slTriggerPx")]
        self.ex.create_order(self.symbol, "market", close_side, p["contracts"], None,
                             {"stopLossPrice": new_stop, "reduceOnly": True, "marginMode": MARGIN})
        for o in old:
            try:
                self.ex.cancel_order(o["id"], self.symbol, {"trigger": True})
            except ccxt.BaseError as e:
                log.warning("Could not cancel the old stop %s: %s", o["id"], str(e)[:100])
        p["stop"], p["be"] = new_stop, True
        self.save_state()
        log.info("Break-even: stop moved to %.2f.", new_stop)

    def place_entry(self, side: str, entry: float, stop: float, target: float, risk: float):
        equity = self.equity()
        qty = equity * self.risk / risk                     # loses exactly risk % at the stop
        contracts = float(self.ex.amount_to_precision(self.symbol, qty / self.contract_size))
        if contracts < self.min_contracts or contracts <= 0:
            log.warning("Position size %.6f %s is below OKX's minimum (%s contracts). Skipping.",
                        qty, self.base, self.min_contracts)
            return
        needed = contracts * self.contract_size * entry / equity
        leverage = max(1, math.ceil(needed * 1.1))         # 10% headroom for fees and price moves
        if leverage > self.max_leverage:
            log.warning("This trade needs %.1fx leverage, above OKX's max of %sx. Skipping.",
                        needed, self.max_leverage)
            return
        try:
            self.ex.set_leverage(leverage, self.symbol, {"marginMode": MARGIN})
        except ccxt.BaseError as e:
            log.warning("Could not set leverage %sx (%s).", leverage, str(e)[:100])
        params = {
            "marginMode": MARGIN,
            "stopLoss": {"triggerPrice": stop, "type": "market"},
            "takeProfit": {"triggerPrice": target, "type": "limit", "price": target},
        }
        order = self.ex.create_order(self.symbol, "limit", "buy" if side == "long" else "sell",
                                     contracts, entry, params)
        self.state["pending"] = {"id": order["id"], "side": side, "entry": entry, "stop": stop,
                                 "target": target, "risk": risk, "contracts": contracts}
        self.save_state()
        log.info("Placed %s limit: %s contracts (%.4f %s) @ %.2f | stop %.2f | target %.2f | "
                 "risk %.2f USDT | %.2fx (set %sx)", side.upper(), contracts,
                 contracts * self.contract_size, self.base, entry, stop, target,
                 contracts * self.contract_size * risk, needed, leverage)

    def cancel_entry(self) -> bool:
        """Cancel the pending entry. Returns False if it had (partly) filled instead."""
        pend = self.state.get("pending")
        if not pend:
            return True
        try:
            self.ex.cancel_order(pend["id"], self.symbol)
        except ccxt.OrderNotFound:
            pass
        except ccxt.InvalidOrder as e:
            log.info("Entry order can't be cancelled (%s), probably filled.", str(e)[:80])
        if self.exchange_position():
            self.sync_position()
            return False
        self.check_missed_trade()
        self.state["pending"] = None
        self.save_state()
        return True

    # ------------------------------------------------------------- position tracking
    def exchange_position(self) -> Optional[dict]:
        for p in self.ex.fetch_positions([self.symbol]):
            contracts = abs(float(p.get("contracts") or 0))
            if contracts > 0:
                side = p.get("side") or ("long" if float(p["info"].get("pos", 0)) > 0 else "short")
                return {"side": side, "contracts": contracts, "entry": float(p.get("entryPrice") or 0)}
        return None

    def sync_position(self):
        pos = self.exchange_position()
        mine = self.state.get("position")
        if not pos and not mine and self.state.get("pending"):
            self.check_missed_trade()
        elif pos and not mine:
            pend = self.state.get("pending") or {}
            fill_ms = self.ex.milliseconds()
            if pend.get("id"):
                try:
                    order = self.ex.fetch_order(pend["id"], self.symbol)
                    fill_ms = int(order.get("lastTradeTimestamp") or order.get("timestamp") or fill_ms)
                except ccxt.BaseError:
                    pass
                try:
                    self.ex.cancel_order(pend["id"], self.symbol)  # stop any further partial fills
                except ccxt.BaseError:
                    pass
            pos = self.exchange_position() or pos
            self.state["position"] = {
                "side": pos["side"], "contracts": pos["contracts"], "entry": pos["entry"],
                "stop": pend.get("stop"), "target": pend.get("target"), "risk": pend.get("risk"),
                "be": False, "fill_ms": fill_ms, "opened": datetime.now(timezone.utc).isoformat()}
            self.state["pending"] = None
            self.gate.on_entry()
            self.save_state()
            log.info("FILLED %s %s contracts @ %.2f (stop %s, target %s).", pos["side"].upper(),
                     pos["contracts"], pos["entry"], pend.get("stop"), pend.get("target"))
            self.ensure_protection()
        elif mine and not pos:
            self.log_closed(mine)
            self.state["position"] = None
            self.state["closed_since_candle"] = True
            self.save_state()
            self.cleanup_orders()

    def check_missed_trade(self):
        """The entry may have filled AND been stopped/targeted between two checks
        (fast markets): then there's no position, but the entry order is filled."""
        pend = self.state.get("pending")
        if not pend:
            return
        try:
            order = self.ex.fetch_order(pend["id"], self.symbol)
        except ccxt.BaseError:
            return
        if float(order.get("filled") or 0) <= 0:
            return
        log.warning("The %s entry filled and its position already closed between checks.", pend["side"])
        self.gate.on_entry()
        self.state["pending"] = None
        self.state["closed_since_candle"] = True
        self.save_state()
        self.log_closed({"side": pend["side"], "entry": float(order.get("average") or pend["entry"])})
        self.cleanup_orders()

    def ensure_protection(self):
        """Make sure the open position has a stop-loss (and a take-profit)."""
        p = self.state["position"]
        if p.get("stop") is None:
            log.error("Open position without known stop level. Closing it to be safe.")
            self.close_position("no stop level")
            return
        time.sleep(1)  # give OKX a moment to place the attached orders
        algos = self.algo_orders()
        has_sl = any(self.order_field(o, "stopLossPrice", "slTriggerPx") for o in algos)
        has_tp = any(self.order_field(o, "takeProfitPrice", "tpTriggerPx") for o in algos) or any(
            o.get("reduceOnly") for o in self.ex.fetch_open_orders(self.symbol))
        close_side = "sell" if p["side"] == "long" else "buy"
        if not has_sl:
            log.warning("No stop-loss found on OKX. Placing one at %.2f.", p["stop"])
            try:
                self.ex.create_order(self.symbol, "market", close_side, p["contracts"], None,
                                     {"stopLossPrice": p["stop"], "reduceOnly": True, "marginMode": MARGIN})
            except ccxt.BaseError as e:
                log.error("Could not place a stop-loss (%s). Closing the position.", str(e)[:120])
                self.close_position("no stop-loss")
                return
        if not has_tp and p.get("target"):
            log.warning("No take-profit found on OKX. Placing a limit at %.2f.", p["target"])
            try:
                self.ex.create_order(self.symbol, "limit", close_side, p["contracts"], p["target"],
                                     {"reduceOnly": True, "marginMode": MARGIN})
            except ccxt.BaseError as e:
                log.error("Could not place the take-profit (%s). The stop-loss stays active.", str(e)[:120])
        log.info("Position protected on OKX: stop %.2f, target %s.", p["stop"], p.get("target"))

    def close_position(self, reason: str):
        pos = self.exchange_position()
        if pos:
            side = "sell" if pos["side"] == "long" else "buy"
            self.ex.create_order(self.symbol, "market", side, pos["contracts"], None,
                                 {"reduceOnly": True, "marginMode": MARGIN})
            log.info("Closed %s position at market (%s).", pos["side"], reason)
        if self.state.get("position"):
            self.log_closed(self.state["position"])
            self.state["closed_since_candle"] = True
        self.state["position"] = None
        self.save_state()
        self.cleanup_orders()

    def log_closed(self, p: dict):
        msg = f"Position closed ({p['side']}, entry {p['entry']:.2f})."
        try:
            hist = self.ex.fetch_positions_history([self.symbol], limit=1)
            if hist and hist[0].get("realizedPnl") is not None:
                msg += f" Realized PnL {float(hist[0]['realizedPnl']):+.2f} USDT."
        except (ccxt.BaseError, AttributeError):
            pass
        log.info(msg + f" Equity now {self.equity():.2f} USDT.")

    def cleanup_orders(self):
        """Cancel leftover orders for this market (the unused target or stop)."""
        for o in self.ex.fetch_open_orders(self.symbol):
            try:
                self.ex.cancel_order(o["id"], self.symbol)
            except ccxt.BaseError:
                pass
        for o in self.algo_orders():
            try:
                self.ex.cancel_order(o["id"], self.symbol, {"trigger": True})
            except ccxt.BaseError:
                pass

    # ------------------------------------------------------------- helpers
    def algo_orders(self) -> list:
        orders = []
        for kind in ("conditional", "oco"):
            try:
                orders += self.ex.fetch_open_orders(self.symbol, params={"ordType": kind})
            except ccxt.BaseError as e:
                log.warning("Could not list %s orders: %s", kind, str(e)[:100])
        return orders

    @staticmethod
    def order_field(order: dict, unified: str, raw: str) -> bool:
        value = order.get(unified)
        if value in (None, "", 0):
            value = (order.get("info") or {}).get(raw)
        return value not in (None, "", "0", 0)

    def equity(self) -> float:
        bal = self.ex.fetch_balance()
        return float((bal.get("USDT") or {}).get("total") or 0)
