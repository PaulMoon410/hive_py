import time
import requests
import os
import json

from fetch_market import get_orderbook_top
from place_order import (
    _get_total_open_orders,
    buy_peakecoin_gas,
    cancel_order,
    cancel_oldest_order,
    get_balance,
    get_open_orders,
    place_order,
)
from rc_intelligence import evaluate_tx_window
from strategy import StrategyState

# Feeder accounts that place DOGE buy support orders (non-DOGE bots).
DOGE_FEEDER_ACCOUNTS = {
    "paulmoon410",
    "dr-animation",
    "strangedad",
    "peakecoin",
    "peakecoin.bnb",
    "peakecoin.matic",
}

# Price tuning for cross-bot DOGE coordination.
FEEDER_DOGE_BUY_DISCOUNT_FROM_ASK = 0.01  # 1% below ask by default
DOGE_FEEDER_SELL_UNDERCUT = 0.00005  # 0.005% below feeder bid to increase fill odds
DOGE_REBUY_PREMIUM_OVER_BID = 0.00005  # 0.005% above bid for replenishment
MIN_DOGE_COORD_QTY = 0.0  # allow all DOGE coordination sizes (no minimum)
DOGE_PRICE_STEP = 0.00000001
DOGE_BASE_PROFIT_RATIO = 0.0005
DOGE_PROFIT_RATIO_STEP = 0.00025
DOGE_MAX_PROFIT_RATIO = 0.01
DOGE_HOLD_CYCLES = 50
DOGE_QUICK_REBUY_CYCLES = 1
TOKEN_MICRO_BUY_QTY = 0.00000001
MICRO_BUY_MIN_PROFIT_RATIO = 0.0005  # 0.05% minimum edge for all micro buys
DOGE_CYCLE_MARKET_BUY_QTY = 0.1
PEK_CYCLE_BUY_QTY = 0.00000001
DOGE_STRICT_COORD_ONLY = os.environ.get(
    "DOGE_STRICT_COORD_ONLY",
    "true",
).lower() in ("1", "true", "yes")
STRICT_DOGE_COORD_CANCEL_UNMATCHED_FEEDER = os.environ.get(
    "STRICT_DOGE_COORD_CANCEL_UNMATCHED_FEEDER",
    "true",
).lower() in ("1", "true", "yes")

# Micro-buy memory: tracks recent micro-buy attempts to learn profitable prices across cycles
MICRO_BUY_MEMORY_FILE = os.path.join(os.path.dirname(__file__), ".micro_buy_memory.json")
MICRO_BUY_MEMORY_LOOKBACK = 200  # Remember last 200 micro-buy attempts per token before oldest is erased
MICRO_BUY_PRICE_LEARN_ALPHA = 0.3  # EMA weight for recent fills (0.3 = 30% new, 70% old)
PRICE_TICK = 0.00000001

LTC_PROFIT_STATE_FILE = os.path.join(os.path.dirname(__file__), ".ltc_profit_layer_state.json")
LTC_BUFFER_MIN = 0.0001
LTC_BUFFER_MAX = 0.003
LTC_BUFFER_STEP_UP = 0.00005
LTC_BUFFER_STEP_DOWN = 0.00003
LTC_BUFFER_START = 0.0005


def _round_price(value):
    return round(max(0.0, float(value)), 8)


def _load_ltc_profit_state():
    default = {"buffer_pct": LTC_BUFFER_START, "last_spread_pct": 0.0, "updated_ts": 0}
    try:
        if os.path.exists(LTC_PROFIT_STATE_FILE):
            with open(LTC_PROFIT_STATE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    default.update(data)
    except Exception:
        pass
    try:
        default["buffer_pct"] = float(default.get("buffer_pct", LTC_BUFFER_START))
    except Exception:
        default["buffer_pct"] = LTC_BUFFER_START
    default["buffer_pct"] = max(LTC_BUFFER_MIN, min(LTC_BUFFER_MAX, default["buffer_pct"]))
    return default


def _save_ltc_profit_state(state):
    try:
        with open(LTC_PROFIT_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def _compute_ltc_profit_buffer(bid, ask):
    state = _load_ltc_profit_state()
    buffer_pct = float(state.get("buffer_pct", LTC_BUFFER_START))
    spread_pct = ((float(ask) - float(bid)) / float(ask)) if float(ask) > 0 and float(ask) > float(bid) else 0.0

    if spread_pct >= max(0.0005, buffer_pct * 4):
        buffer_pct = min(LTC_BUFFER_MAX, buffer_pct + LTC_BUFFER_STEP_UP)
    elif spread_pct <= max(0.00015, buffer_pct * 1.5):
        buffer_pct = max(LTC_BUFFER_MIN, buffer_pct - LTC_BUFFER_STEP_DOWN)

    state["buffer_pct"] = round(buffer_pct, 8)
    state["last_spread_pct"] = round(spread_pct, 8)
    state["updated_ts"] = int(time.time())
    _save_ltc_profit_state(state)
    return buffer_pct, spread_pct


def _apply_ltc_profit_layer(buy_quote, sell_quote, bid, ask, buffer_pct):
    half_buffer = float(buffer_pct) / 2.0
    layered_buy = float(buy_quote) * (1 - half_buffer)
    layered_sell = float(sell_quote) * (1 + half_buffer)

    # Keep maker-side posture against current top-of-book.
    if ask > PRICE_TICK:
        layered_buy = min(layered_buy, ask - PRICE_TICK)
    if bid > 0:
        layered_sell = max(layered_sell, bid + PRICE_TICK)

    layered_buy = round(max(0.0, layered_buy), 8)
    layered_sell = round(max(0.0, layered_sell), 8)
    if layered_sell <= layered_buy:
        return buy_quote, sell_quote
    return layered_buy, layered_sell


def _default_doge_profit_state():
    return {
        "phase": "ramp_up",
        "current_ratio": DOGE_BASE_PROFIT_RATIO,
        "hold_cycles_remaining": 0,
        "last_realized_ratio": 0.0,
        "last_market_spread_ratio": 0.0,
        "pending_rebuy": None,
    }


def _ensure_doge_coord_state_shape(state):
    normalized = {
        "pause_until": 0,
        "paused": False,
        "reason": "",
        "updated_ts": 0,
        "profit_window": _default_doge_profit_state(),
    }
    if isinstance(state, dict):
        normalized.update(state)
    profit_window = normalized.get("profit_window")
    if not isinstance(profit_window, dict):
        profit_window = {}
    merged_profit = _default_doge_profit_state()
    merged_profit.update(profit_window)
    normalized["profit_window"] = merged_profit
    return normalized


def _compute_feeder_doge_buy_price(doge_bid, doge_ask):
    """Default to 1% below ask; when too tight, fall back to market bid."""
    if doge_ask <= 0:
        return _round_price(doge_bid) if doge_bid > 0 else 0.0

    discounted_ask_price = doge_ask * (1 - FEEDER_DOGE_BUY_DISCOUNT_FROM_ASK)
    if doge_bid > 0 and discounted_ask_price <= doge_bid:
        proximity_price = doge_ask - DOGE_PRICE_STEP
        if proximity_price <= doge_bid:
            # Tight spread fallback: place at bid instead of skipping.
            return _round_price(doge_bid)
        return _round_price(proximity_price)

    return _round_price(discounted_ask_price)


def _classify_feeder_price_skip(doge_bid, doge_ask, feeder_buy_price):
    if feeder_buy_price > 0:
        return "ok"
    if doge_ask <= 0 and doge_bid <= 0:
        return "quote-unavailable"
    if doge_ask > 0 and doge_bid > 0 and (doge_ask - doge_bid) <= DOGE_PRICE_STEP:
        return "spread-too-tight"
    return "price-guard"


def _compute_doge_rebuy_price(doge_bid, doge_ask, previous_sell_price):
    """Rebuy must stay strictly below the prior DOGE sale price."""
    if previous_sell_price <= 0:
        return 0.0

    profit_ratio = _get_current_doge_profit_ratio()
    target_profit_price = previous_sell_price * (1 - profit_ratio)

    capped_sell_price = previous_sell_price - DOGE_PRICE_STEP
    if capped_sell_price <= 0:
        return 0.0

    if doge_bid > 0:
        candidate = doge_bid * (1 + DOGE_REBUY_PREMIUM_OVER_BID)
    elif doge_ask > 0:
        candidate = doge_ask - DOGE_PRICE_STEP
    else:
        candidate = 0.0

    if candidate <= 0:
        return 0.0

    if doge_ask > 0:
        candidate = min(candidate, doge_ask - DOGE_PRICE_STEP)

    candidate = min(candidate, target_profit_price)
    candidate = min(candidate, capped_sell_price)
    if candidate <= 0:
        return 0.0

    return _round_price(candidate)


def _get_current_doge_profit_ratio():
    state = _load_doge_coord_state()
    try:
        ratio = float(state.get("profit_window", {}).get("current_ratio", DOGE_BASE_PROFIT_RATIO))
    except Exception:
        ratio = DOGE_BASE_PROFIT_RATIO
    return max(DOGE_BASE_PROFIT_RATIO, min(DOGE_MAX_PROFIT_RATIO, ratio))


def _get_matching_open_doge_buy(account_name, target_price, target_qty, nodes=None):
    if nodes is None:
        nodes = [
            "https://api.hive-engine.com/rpc/contracts",
            "https://herpc.dtools.dev",
            "https://engine.rishipanthee.com/rpc",
            "https://api2.hive-engine.com/rpc/contracts",
        ]
    try:
        orders = get_open_orders(account_name, token="SWAP.DOGE", nodes=nodes)
    except Exception:
        orders = []
    for order in orders:
        side = (order.get("type") or order.get("side") or "").lower()
        if side != "buy":
            continue
        try:
            price = float(order.get("price") or order.get("tokenPrice") or 0)
            qty = float(order.get("quantity") or order.get("tokenQuantity") or 0)
        except Exception:
            continue
        if abs(price - float(target_price)) <= 0.00000002 and qty >= float(target_qty) * 0.5:
            return order
    return None


def _reset_doge_profit_window(reason, bot_label, state=None):
    if state is None:
        state = _load_doge_coord_state()
    profit_window = state["profit_window"]
    profit_window.update(_default_doge_profit_state())
    state["updated_ts"] = int(time.time())
    _save_doge_coord_state(state)
    print(f"[{bot_label}] DOGE profit window reset: reason={reason} ratio={profit_window['current_ratio']:.6f}")


def _record_doge_rebuy_submission(sale_price, rebuy_price, qty, bot_label, reason, state=None):
    if state is None:
        state = _load_doge_coord_state()
    profit_window = state["profit_window"]
    realized_ratio = 0.0
    if sale_price > 0:
        realized_ratio = max(0.0, (float(sale_price) - float(rebuy_price)) / float(sale_price))
    profit_window["pending_rebuy"] = {
        "sale_price": round(float(sale_price), 8),
        "rebuy_price": round(float(rebuy_price), 8),
        "qty": round(float(qty), 8),
        "reason": reason,
        "submitted_ts": int(time.time()),
        "cycles_open": 0,
        "target_ratio": round(realized_ratio, 8),
    }
    profit_window["last_realized_ratio"] = realized_ratio
    state["updated_ts"] = int(time.time())
    _save_doge_coord_state(state)
    print(
        f"[{bot_label}] DOGE profit window armed: phase={profit_window['phase']} "
        f"target_ratio={realized_ratio:.6f} current_ratio={profit_window['current_ratio']:.6f}"
    )


def _advance_doge_profit_window(bot_label, nodes):
    state = _load_doge_coord_state()
    profit_window = state["profit_window"]
    current_ratio = max(DOGE_BASE_PROFIT_RATIO, min(DOGE_MAX_PROFIT_RATIO, float(profit_window.get("current_ratio", DOGE_BASE_PROFIT_RATIO))))
    phase = profit_window.get("phase", "ramp_up")
    pending_rebuy = profit_window.get("pending_rebuy")

    market = _get_market_with_retries("SWAP.DOGE", attempts=2, delay_seconds=1.0)
    doge_bid = float(market.get("highestBid", 0)) if market else 0.0
    doge_ask = float(market.get("lowestAsk", 0)) if market else 0.0
    spread_ratio = ((doge_ask - doge_bid) / doge_ask) if doge_ask > 0 and doge_ask > doge_bid else 0.0
    profit_window["last_market_spread_ratio"] = round(max(0.0, spread_ratio), 8)

    if phase == "hold":
        if 0 < spread_ratio < current_ratio:
            _reset_doge_profit_window("spread-contracted", bot_label, state=state)
            return _load_doge_coord_state()["profit_window"]
        remaining = int(profit_window.get("hold_cycles_remaining", DOGE_HOLD_CYCLES) or 0)
        remaining = max(0, remaining - 1)
        profit_window["hold_cycles_remaining"] = remaining
        if remaining == 0:
            profit_window["phase"] = "decay"
            print(f"[{bot_label}] DOGE profit window entering decay at ratio={current_ratio:.6f}")

    elif phase == "decay":
        current_ratio = max(DOGE_BASE_PROFIT_RATIO, current_ratio - DOGE_PROFIT_RATIO_STEP)
        profit_window["current_ratio"] = round(current_ratio, 8)

    if pending_rebuy:
        matching_order = _get_matching_open_doge_buy(
            DOGE_EXECUTION_ACCOUNT,
            pending_rebuy.get("rebuy_price", 0),
            pending_rebuy.get("qty", 0),
            nodes=nodes,
        )
        if matching_order:
            pending_rebuy["cycles_open"] = int(pending_rebuy.get("cycles_open", 0) or 0) + 1
            if profit_window.get("phase") == "ramp_up" and pending_rebuy["cycles_open"] > DOGE_QUICK_REBUY_CYCLES:
                profit_window["phase"] = "hold"
                profit_window["hold_cycles_remaining"] = DOGE_HOLD_CYCLES
                print(
                    f"[{bot_label}] DOGE profit window hold started: ratio={current_ratio:.6f} "
                    f"rebuy_open_cycles={pending_rebuy['cycles_open']}"
                )
        else:
            quick_fill = int(pending_rebuy.get("cycles_open", 0) or 0) <= DOGE_QUICK_REBUY_CYCLES
            realized_ratio = float(pending_rebuy.get("target_ratio", 0.0) or 0.0)
            if quick_fill:
                next_ratio = min(DOGE_MAX_PROFIT_RATIO, max(current_ratio, realized_ratio) + DOGE_PROFIT_RATIO_STEP)
                profit_window["current_ratio"] = round(next_ratio, 8)
                profit_window["phase"] = "ramp_up"
                print(
                    f"[{bot_label}] DOGE profit window increased: {current_ratio:.6f} -> {next_ratio:.6f} "
                    f"after quick rebuy fill"
                )
            elif profit_window.get("phase") == "ramp_up":
                profit_window["phase"] = "hold"
                profit_window["hold_cycles_remaining"] = DOGE_HOLD_CYCLES
                print(f"[{bot_label}] DOGE profit window hold confirmed after slow rebuy fill.")
            profit_window["pending_rebuy"] = None

    state["updated_ts"] = int(time.time())
    _save_doge_coord_state(state)
    return profit_window

# DOGE execution account used to cross-fill feeder DOGE bids in tandem.
DOGE_EXECUTION_ACCOUNT = os.environ.get("DOGE_EXECUTION_ACCOUNT", "mmoonn")
DOGE_EXECUTION_ACTIVE_KEY = os.environ.get(
    "DOGE_EXECUTION_ACTIVE_KEY",
    "5JmKAq839tgpG1ESz3ZK7jSe1Y22mbwNvtBsXJ3YT7RCoXFcFKh",  # mmoonn account key
)

DOGE_COORD_STATE_FILE = os.path.join(os.path.dirname(__file__), ".doge_coordination_state.json")
DOGE_COORD_AUDIT_FILE = os.path.join(os.path.dirname(__file__), ".doge_coordination_audit.log")


def _append_doge_audit(event):
    """Append JSONL audit records for DOGE coordination verification."""
    try:
        record = {"ts": int(time.time()), **event}
        with open(DOGE_COORD_AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _load_doge_coord_state():
    try:
        if os.path.exists(DOGE_COORD_STATE_FILE):
            with open(DOGE_COORD_STATE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return _ensure_doge_coord_state_shape(data)
    except Exception:
        pass
    return _ensure_doge_coord_state_shape({})


def _save_doge_coord_state(state):
    try:
        with open(DOGE_COORD_STATE_FILE, "w") as f:
            json.dump(_ensure_doge_coord_state_shape(state), f, indent=2)
    except Exception:
        pass


def _set_doge_pause(wait_seconds, reason):
    now = int(time.time())
    state = _load_doge_coord_state()
    state["paused"] = True
    state["pause_until"] = now + max(30, int(wait_seconds))
    state["reason"] = reason
    state["updated_ts"] = now
    _save_doge_coord_state(state)


def _clear_doge_pause():
    now = int(time.time())
    state = _load_doge_coord_state()
    state["paused"] = False
    state["pause_until"] = 0
    state["reason"] = ""
    state["updated_ts"] = now
    _save_doge_coord_state(state)


def _is_doge_pause_active():
    state = _load_doge_coord_state()
    if not state.get("paused", False):
        return False, state
    now = int(time.time())
    if now >= int(state.get("pause_until", 0) or 0):
        return False, state
    return True, state


def _sync_doge_pause_with_rc():
    """Auto-clear pause once DOGE execution account can place sell-side tx again."""
    decision = evaluate_tx_window(DOGE_EXECUTION_ACCOUNT, action="sell")
    allow = bool(decision.get("allow", False))
    mode = decision.get("mode")
    if allow:
        _clear_doge_pause()
        return False, decision
    if mode in ("critical", "low", "cooldown"):
        _set_doge_pause(int(decision.get("wait_seconds", 120) or 120), f"rc-{mode}")
    return True, decision


def _is_matching_feeder_buy_visible(feeder_account, target_price, min_qty=0.0, retries=8, delay_seconds=2.0):
    """Wait for a feeder DOGE buy to appear in openOrders or buyBook before cross-filling it."""
    nodes = [
        "https://api.hive-engine.com/rpc/contracts",
        "https://herpc.dtools.dev",
        "https://engine.rishipanthee.com/rpc",
        "https://api2.hive-engine.com/rpc/contracts",
    ]

    def _matches(price, qty):
        try:
            return abs(float(price) - float(target_price)) <= 0.00000002 and float(qty) >= float(min_qty) * 0.5
        except Exception:
            return False

    for attempt in range(retries):
        try:
            open_orders = get_open_orders(feeder_account, token="SWAP.DOGE", nodes=nodes)
        except Exception:
            open_orders = []
        for order in open_orders:
            side = (order.get("type") or order.get("side") or "").lower()
            price = order.get("price") or order.get("tokenPrice") or 0
            qty = order.get("quantity") or order.get("tokenQuantity") or 0
            if side == "buy" and _matches(price, qty):
                return True

        for node in nodes:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "method": "find",
                    "params": {
                        "contract": "market",
                        "table": "buyBook",
                        "query": {"account": feeder_account, "symbol": "SWAP.DOGE"},
                        "limit": 1000,
                    },
                    "id": 1,
                }
                resp = requests.post(node, json=payload, timeout=10)
                if resp.status_code != 200:
                    continue
                orders = resp.json().get("result", [])
                if not isinstance(orders, list):
                    continue
                for order in orders:
                    price = order.get("price", 0)
                    qty = order.get("quantity", order.get("tokenQuantity", 0)) or 0
                    if _matches(price, qty):
                        return True
            except Exception:
                continue
        if attempt < retries - 1:
            time.sleep(delay_seconds)
    return False


def _attempt_doge_cross_fill(feeder_account, feeder_price, feeder_qty, nodes, bot_label):
    """Attempt immediate DOGE-account sell into feeder account's DOGE buy support order."""
    if feeder_account == DOGE_EXECUTION_ACCOUNT:
        return False
    if feeder_account not in DOGE_FEEDER_ACCOUNTS:
        print(
            f"[{bot_label}] DOGE cross-fill refused: feeder account {feeder_account} "
            f"is not in whitelist."
        )
        return False

    # Final pre-trade guard: confirm feeder still has a matching DOGE buy open.
    # Use short retries and allow partial remaining quantity to avoid false negatives on propagation.
    matching_whitelisted_order = _is_matching_feeder_buy_visible(
        feeder_account,
        feeder_price,
        min_qty=max(0.0, float(feeder_qty) * 0.1),
        retries=3,
        delay_seconds=1.0,
    )
    if not matching_whitelisted_order:
        print(
            f"[{bot_label}] DOGE cross-fill refused: no matching whitelisted feeder order "
            f"for account={feeder_account} price={float(feeder_price):.8f}."
        )
        return False

    if DOGE_EXECUTION_ACTIVE_KEY in ("", "your_active_key", None):
        print(f"[{bot_label}] DOGE cross-fill skipped: DOGE execution key not configured.")
        return False
    try:
        doge_balance = get_balance(DOGE_EXECUTION_ACCOUNT, "SWAP.DOGE")
    except Exception:
        doge_balance = 0.0
    if doge_balance <= 0:
        print(f"[{bot_label}] DOGE cross-fill skipped: {DOGE_EXECUTION_ACCOUNT} has no SWAP.DOGE balance.")
        return False

    sell_qty = max(0.0, min(float(feeder_qty), doge_balance * 0.15))
    if sell_qty <= 0:
        print(f"[{bot_label}] DOGE cross-fill skipped: no sellable DOGE quantity.")
        return False
    if sell_qty < MIN_DOGE_COORD_QTY:
        print(
            f"[{bot_label}] DOGE cross-fill skipped: quantity {sell_qty:.8f} "
            f"below minimum {MIN_DOGE_COORD_QTY:.8f}."
        )
        return False

    sell_price = round(float(feeder_price) * (1 - DOGE_FEEDER_SELL_UNDERCUT), 8)
    if sell_price <= 0:
        sell_price = round(float(feeder_price), 8)

    placed = place_order(
        DOGE_EXECUTION_ACCOUNT,
        "SWAP.DOGE",
        sell_price,
        sell_qty,
        order_type="sell",
        active_key=DOGE_EXECUTION_ACTIVE_KEY,
        nodes=nodes,
        skip_profit_filters=True,
    )
    if placed:
        _clear_doge_pause()
        print(
            f"[{bot_label}] DOGE tandem sell submitted from {DOGE_EXECUTION_ACCOUNT}: "
            f"{sell_qty:.8f} SWAP.DOGE at {sell_price:.8f} (feeder={feeder_account})"
        )
        return sell_price
    else:
        blocked, decision = _sync_doge_pause_with_rc()
        if blocked:
            print(
                f"[{bot_label}] DOGE coordination paused: mode={decision.get('mode')} "
                f"wait={decision.get('wait_seconds')}s"
            )
        print(
            f"[{bot_label}] DOGE tandem sell skipped/failed from {DOGE_EXECUTION_ACCOUNT} "
            f"at {sell_price:.8f} (feeder={feeder_account})."
        )
        return 0.0


def _place_doge_rebuy_after_sell(account_name, active_key, nodes, qty, rebuy_price, bot_label, reason, sale_price=0.0):
    """Place one DOGE rebuy after a confirmed sell, not as a standalone periodic buy."""
    if qty <= 0 or rebuy_price <= 0:
        return False
    if qty < MIN_DOGE_COORD_QTY:
        print(
            f"[{bot_label}] DOGE rebuy skipped ({reason}): quantity {qty:.8f} "
            f"below minimum {MIN_DOGE_COORD_QTY:.8f}."
        )
        return False
    if active_key in ("", "your_active_key", None):
        print(f"[{bot_label}] DOGE rebuy skipped ({reason}): active key not configured.")
        return False
    if sale_price > 0 and rebuy_price >= (sale_price - DOGE_PRICE_STEP):
        print(
            f"[{bot_label}] DOGE rebuy blocked ({reason}): rebuy {rebuy_price:.8f} "
            f"is not below sale {sale_price:.8f}."
        )
        return False

    placed = place_order(
        account_name,
        "SWAP.DOGE",
        rebuy_price,
        qty,
        order_type="buy",
        active_key=active_key,
        nodes=nodes,
        skip_profit_filters=True,
    )
    if placed:
        print(
            f"[{bot_label}] DOGE immediate rebuy submitted ({reason}): "
            f"{qty:.8f} SWAP.DOGE at {rebuy_price:.8f}"
        )
        return True
    print(f"[{bot_label}] DOGE immediate rebuy skipped ({reason}) (filters/RC/duplicate).")
    return False


def _get_highest_feeder_doge_buy(excluded_account=None, token="SWAP.DOGE"):
    """Return highest open DOGE buy price from feeder accounts, excluding caller account."""
    nodes = [
        "https://api.hive-engine.com/rpc/contracts",
        "https://herpc.dtools.dev",
        "https://engine.rishipanthee.com/rpc",
        "https://api2.hive-engine.com/rpc/contracts",
    ]
    highest = 0.0
    for node in nodes:
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "find",
                "params": {
                    "contract": "market",
                    "table": "buyBook",
                    "query": {"symbol": token},
                    "limit": 1000,
                },
                "id": 1,
            }
            resp = requests.post(node, json=payload, timeout=10)
            if resp.status_code != 200:
                continue
            orders = resp.json().get("result", [])
            if not isinstance(orders, list):
                continue
            for order in orders:
                account = order.get("account")
                if account not in DOGE_FEEDER_ACCOUNTS:
                    continue
                if excluded_account and account == excluded_account:
                    continue
                try:
                    price = float(order.get("price", 0))
                    if price > highest:
                        highest = price
                except Exception:
                    continue
            return highest
        except Exception:
            continue
    return highest


def _get_market_with_retries(token, attempts=3, delay_seconds=1.5):
    """Fetch top-of-book with short retries to smooth transient RPC gaps."""
    last_market = None
    for attempt in range(attempts):
        try:
            last_market = get_orderbook_top(token)
        except Exception:
            last_market = None
        if last_market:
            return last_market
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    return last_market


def _load_micro_buy_memory():
    """Load micro-buy attempt history (filled, cancelled) per token with required sale prices."""
    try:
        if os.path.exists(MICRO_BUY_MEMORY_FILE):
            with open(MICRO_BUY_MEMORY_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _save_micro_buy_memory(memory):
    """Save micro-buy attempt history."""
    try:
        with open(MICRO_BUY_MEMORY_FILE, "w") as f:
            json.dump(memory, f, indent=2)
    except Exception:
        pass


def _record_micro_buy_attempt(token, buy_price, required_sell_price, qty, result, txid=None):
    """Record a micro-buy attempt with its required profit threshold and unique txid for loss avoidance."""
    memory = _load_micro_buy_memory()
    token_key = str(token).upper()
    if token_key not in memory:
        memory[token_key] = []
    
    memory[token_key].append({
        "buy_price": round(float(buy_price), 8),
        "required_sell_price": round(float(required_sell_price), 8),  # minimum sell to break even + profit
        "qty": round(float(qty), 8),
        "result": result,  # "placed", "cancelled", "skipped"
        "txid": str(txid) if txid else None,  # Unique order ID to avoid confusion with similar orders
        "timestamp": int(time.time()),
    })
    
    # Keep only last N attempts per token (rotate oldest out when reaching max)
    if len(memory[token_key]) > MICRO_BUY_MEMORY_LOOKBACK:
        memory[token_key] = memory[token_key][-MICRO_BUY_MEMORY_LOOKBACK:]
    
    _save_micro_buy_memory(memory)


def _get_recent_required_sell_price(token):
    """Get the highest required sell price from recent micro-buy attempts (placed or cancelled).
    
    Only considers attempts within 1 hour and uses txid to uniquely identify orders 
    to avoid confusion between multiple similar orders.
    """
    memory = _load_micro_buy_memory()
    token_key = str(token).upper()
    attempts = memory.get(token_key, [])
    
    # Find attempts from last hour that were actually placed or cancelled (not skipped)
    recent_threshold = int(time.time()) - 3600
    active_attempts = [
        att for att in attempts
        if att.get("result") in ("placed", "cancelled") and att.get("timestamp", 0) > recent_threshold
    ]
    
    if not active_attempts:
        return 0.0
    
    # Return highest required sell price (most conservative, least likely to take loss)
    # txid field ensures we don't confuse different orders with similar prices
    required_prices = [float(att.get("required_sell_price", 0)) for att in active_attempts if att.get("required_sell_price", 0) > 0]
    return max(required_prices) if required_prices else 0.0


def _attempt_token_micro_buy(account_name, token, active_key, nodes, ask_price, bot_label, support_sell_price=0.0):
    if active_key in ("", "your_active_key", None):
        print(f"[{bot_label}] {token} micro buy skipped: HIVE_ACTIVE_KEY not set.")
        return False
    if ask_price <= 0:
        print(f"[{bot_label}] {token} micro buy skipped: market ask unavailable.")
        return False

    # Always place a micro-buy at the current ask price, regardless of profit, for all non-DOGE tokens.
    if token == "SWAP.DOGE":
        print(f"[{bot_label}] DOGE micro buy skipped: DOGE bot uses separate logic.")
        return False

    placed = place_order(
        account_name,
        token,
        ask_price,
        TOKEN_MICRO_BUY_QTY,
        order_type="buy",
        active_key=active_key,
        nodes=nodes,
        skip_profit_filters=True,
    )
    if placed:
        print(
            f"[{bot_label}] {token} forced micro buy submitted: qty={TOKEN_MICRO_BUY_QTY:.8f} "
            f"at {ask_price:.8f} (txid={str(placed)[-16:] if placed else 'unknown'})"
        )
        _record_micro_buy_attempt(token, ask_price, 0.0, TOKEN_MICRO_BUY_QTY, "placed", txid=placed)
    else:
        print(f"[{bot_label}] {token} forced micro buy skipped/failed.")
        _record_micro_buy_attempt(token, ask_price, 0.0, TOKEN_MICRO_BUY_QTY, "skipped", txid=None)
    return bool(placed)


def _attempt_doge_cycle_market_buy(account_name, active_key, nodes, ask_price, bot_label, feeder_bid=0.0, market_bid=0.0):
    """Submit a guarded 0.1 SWAP.DOGE cycle buy only when a profitable re-sell path exists."""
    if active_key in ("", "your_active_key", None):
        print(f"[{bot_label}] DOGE 0.1 market buy skipped: HIVE_ACTIVE_KEY not set.")
        return False
    if ask_price <= 0:
        print(f"[{bot_label}] DOGE 0.1 market buy skipped: market ask unavailable.")
        return False

    exit_support_price = max(float(feeder_bid), float(market_bid))
    if exit_support_price <= 0:
        print(f"[{bot_label}] DOGE cycle buy skipped: no feeder or market bid support available.")
        return False

    target_profit_ratio = _get_current_doge_profit_ratio()
    max_buy_price = round(exit_support_price * (1 - target_profit_ratio), 8)
    if max_buy_price <= 0:
        print(f"[{bot_label}] DOGE cycle buy skipped: computed buy ceiling invalid ({max_buy_price}).")
        return False
    if ask_price > max_buy_price:
        print(
            f"[{bot_label}] DOGE cycle buy skipped: ask {ask_price:.8f} exceeds "
            f"max profitable buy {max_buy_price:.8f} (support_bid={exit_support_price:.8f}, "
            f"target={target_profit_ratio:.6f})."
        )
        return False

    placed = place_order(
        account_name,
        "SWAP.DOGE",
        max_buy_price,
        DOGE_CYCLE_MARKET_BUY_QTY,
        order_type="buy",
        active_key=active_key,
        nodes=nodes,
        skip_profit_filters=True,
    )
    if placed:
        print(
            f"[{bot_label}] DOGE guarded cycle buy submitted: "
            f"qty={DOGE_CYCLE_MARKET_BUY_QTY:.8f} at {max_buy_price:.8f} "
            f"(support_bid={exit_support_price:.8f}, target={target_profit_ratio:.6f})"
        )
    else:
        print(f"[{bot_label}] DOGE 0.1 market buy skipped/failed.")
    return bool(placed)


def _attempt_pek_cycle_buy(account_name, active_key, nodes, bot_label):
    """Submit a fixed PEK micro-buy once per normal bot cycle."""
    if active_key in ("", "your_active_key", None, "Your Active Key Here"):
        print(f"[{bot_label}] PEK cycle buy skipped: HIVE_ACTIVE_KEY not set.")
        return False

    try:
        placed = buy_peakecoin_gas(
            account_name,
            active_key=active_key,
            nodes=nodes,
            pek_amount=PEK_CYCLE_BUY_QTY,
        )
    except Exception as e:
        print(f"[{bot_label}] PEK cycle buy exception: {e}")
        return False

    if placed:
        print(f"[{bot_label}] PEK cycle buy submitted: qty={PEK_CYCLE_BUY_QTY:.8f}")
    else:
        print(f"[{bot_label}] PEK cycle buy skipped/failed.")
    return bool(placed)


def _cancel_matching_feeder_buy(account_name, active_key, nodes, target_price, target_qty, bot_label):
    """Cancel matching feeder buy if tandem sell could not be submitted."""
    matching_order = _get_matching_open_doge_buy(
        account_name,
        target_price,
        target_qty,
        nodes=nodes,
    )
    if not matching_order:
        print(
            f"[{bot_label}] Feeder cleanup: no matching feeder buy visible to cancel "
            f"(price={float(target_price):.8f}, qty={float(target_qty):.8f})."
        )
        return False

    txid = str(matching_order.get("txId") or matching_order.get("txid") or "")
    if not txid:
        print(f"[{bot_label}] Feeder cleanup: matching order missing txId, cannot cancel.")
        return False

    cancelled = cancel_order(
        account_name=account_name,
        order_id=txid,
        verbose=False,
        nodes=nodes,
        active_key=active_key,
        side="buy",
    )
    if cancelled:
        print(f"[{bot_label}] Feeder cleanup: cancelled unmatched feeder buy txId={txid}.")
    else:
        print(f"[{bot_label}] Feeder cleanup: failed to cancel unmatched feeder buy txId={txid}.")
    return bool(cancelled)


def _get_valid_market_with_retries(token, attempts=4, delay_seconds=1.25):
    """Fetch market until bid/ask are both present and non-inverted."""
    last_market = None
    for attempt in range(attempts):
        market = _get_market_with_retries(token, attempts=1, delay_seconds=0)
        if market:
            last_market = market
            try:
                bid = float(market.get("highestBid", 0) or 0)
                ask = float(market.get("lowestAsk", 0) or 0)
            except Exception:
                bid = 0.0
                ask = 0.0
            if bid > 0 and ask > 0 and ask >= bid:
                return market
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    return last_market


def run_ltc_style_logic(account_name, token, active_key, nodes, state_file, bot_label):
    """Run the LTC bot trading logic for any token/account.

    This intentionally mirrors the LTC flow so all bots can share one strategy.
    """
    can_transact = active_key not in ("", "your_active_key", None)
    state = StrategyState(state_file)

    open_orders_debug = get_open_orders(account_name, token)
    print(f"[{bot_label}] Open orders for {token}: {len(open_orders_debug)}")
    open_count = _get_total_open_orders(account_name)
    print(f"[{bot_label}] Total open orders (all tokens): {open_count}")

    if open_count >= 190:
        print(f"[{bot_label}] Open orders >= 190: cancelling 3 oldest and skipping ALL buys.")
        if not can_transact:
            print(f"[{bot_label}] Cancel skipped: HIVE_ACTIVE_KEY not set.")
        else:
            cancelled_txids = set()
            for _ in range(3):
                cancelled = cancel_oldest_order(
                    account_name,
                    None,
                    active_key=active_key,
                    nodes=nodes,
                    excluded_txids=cancelled_txids,
                    force_cancel=True,
                )
                if not cancelled:
                    break
                time.sleep(5)
        print("==============================\n")
        return
    elif open_count >= 100:
        print(f"[{bot_label}] Open orders at {open_count}. Between 100 and 189: No cancels, normal trading allowed.")
    else:
        print(f"[{bot_label}] Open orders < 100: placing new orders only.")

    _attempt_pek_cycle_buy(
        account_name=account_name,
        active_key=active_key,
        nodes=nodes,
        bot_label=bot_label,
    )

    market = _get_valid_market_with_retries(token, attempts=4, delay_seconds=1.25)
    if not market:
        print(f"[{bot_label}] Market fetch failed for {token}. Skipping this cycle.")
        print("==============================\n")
        return

    print(f"[{bot_label}] Market fetch success for {token}.")
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    if bid <= 0 or ask <= 0:
        print(
            f"[{bot_label}] Market book incomplete for {token} (bid={bid}, ask={ask}). "
            f"Skipping this cycle."
        )
        print("==============================\n")
        return
    if ask < bid:
        print(
            f"[{bot_label}] Market book inverted for {token} (bid={bid}, ask={ask}). "
            f"Skipping this cycle."
        )
        print("==============================\n")
        return
    spread = ask - bid
    print(f"[{bot_label}] Bid: {bid}, Ask: {ask}")

    hive_balance = get_balance(account_name, "SWAP.HIVE")
    token_balance = get_balance(account_name, token)
    print(f"[{bot_label}] Balances: HIVE={hive_balance:.4f}, TOKEN={token_balance:.4f}")

    token_price = ask if ask > 0 else bid
    buy_quote, sell_quote = state.get_adaptive_quote(
        bid,
        ask,
        spread,
        token_balance,
        hive_balance,
        token_price,
    )

    if token == "SWAP.LTC":
        ltc_buffer_pct, ltc_spread_pct = _compute_ltc_profit_buffer(bid, ask)
        buy_quote, sell_quote = _apply_ltc_profit_layer(buy_quote, sell_quote, bid, ask, ltc_buffer_pct)
        print(
            f"[{bot_label}] LTC profit layer: buffer={ltc_buffer_pct:.6f} "
            f"spread={ltc_spread_pct:.6f} -> BUY={buy_quote}, SELL={sell_quote}"
        )

    # Keep DOGE coordination lock aligned with DOGE execution account RC state.
    doge_paused, pause_state = _is_doge_pause_active()
    doge_rebuy_quote = 0.0
    if doge_paused:
        # try quick recovery check; clear lock automatically if DOGE sell path is available again
        doge_paused, _ = _sync_doge_pause_with_rc()
        if doge_paused:
            print(
                f"[{bot_label}] DOGE coordination paused until {pause_state.get('pause_until')} "
                f"reason={pause_state.get('reason')}"
            )

    # DOGE bot coordination mode:
    # - Do not run generic LTC strategy.
    # - Sell into feeder bots' DOGE buy support bids.
    # - Rebuy slightly above bid, but never cross the ask.
    doge_feeder_bid = 0.0
    if token == "SWAP.DOGE":
        feeder_bid = _get_highest_feeder_doge_buy(excluded_account=account_name, token=token)
        doge_feeder_bid = feeder_bid
        rebuy_quote = _compute_doge_rebuy_price(bid, ask, sell_quote if sell_quote > 0 else feeder_bid)
        doge_rebuy_quote = rebuy_quote
        buy_quote = rebuy_quote if rebuy_quote > 0 else (round(bid, 8) if bid > 0 else buy_quote)
        if feeder_bid > 0:
            feeder_sell = feeder_bid * (1 - DOGE_FEEDER_SELL_UNDERCUT)
            sell_quote = round(max(feeder_sell, buy_quote), 8)
            print(
                f"[{bot_label}] Feeder-coordination: feeder_bid={feeder_bid:.8f} "
                f"sell_quote={sell_quote:.8f} buy_quote={buy_quote:.8f}"
            )
        else:
            sell_quote = 0
            print(f"[{bot_label}] Feeder-coordination: no feeder bid available, sell leg skipped.")

    # Keep LTC's aggressive DOGE side-buy behavior in all bots.
    if bid > 0 and token != "SWAP.DOGE":
        if doge_paused:
            print(f"[{bot_label}] Feeder DOGE buy skipped: DOGE coordination pause active.")
        else:
            one_percent_hive = hive_balance * 0.01
            if one_percent_hive > 0:
                doge_bid = 0
                doge_ask = 0
                try:
                    doge_market = _get_market_with_retries("SWAP.DOGE", attempts=3, delay_seconds=1.5)
                    doge_bid = float(doge_market.get("highestBid", 0)) if doge_market else 0
                    doge_ask = float(doge_market.get("lowestAsk", 0)) if doge_market else 0
                except Exception:
                    pass
                # Feeder bots support DOGE bot by posting buy orders near the ask (maker-side).
                feeder_buy_price = _compute_feeder_doge_buy_price(doge_bid, doge_ask)
                if feeder_buy_price > 0 and doge_bid > 0 and abs(feeder_buy_price - doge_bid) <= DOGE_PRICE_STEP:
                    print(
                        f"[{bot_label}] Feeder DOGE tight-spread fallback: using market bid {feeder_buy_price:.8f}."
                    )

                # Strict maker guard: do not place if this could execute immediately as taker.
                if doge_ask > 0 and feeder_buy_price >= doge_ask:
                    print(
                        f"[{bot_label}] Feeder DOGE buy skipped: computed price {feeder_buy_price:.8f} "
                        f"is not below ask {doge_ask:.8f}."
                    )
                    feeder_buy_price = 0

                if feeder_buy_price > 0:
                    market_buy_qty = round(one_percent_hive / feeder_buy_price, 8)
                    if market_buy_qty < MIN_DOGE_COORD_QTY:
                        print(
                            f"[{bot_label}] Feeder DOGE buy skipped: quantity {market_buy_qty:.8f} "
                            f"below minimum {MIN_DOGE_COORD_QTY:.8f}."
                        )
                    else:
                        if not can_transact:
                            print(f"[{bot_label}] Aggressive market buy skipped: HIVE_ACTIVE_KEY not set.")
                        else:
                            try:
                                placed = place_order(
                                    account_name,
                                    "SWAP.DOGE",
                                    feeder_buy_price,
                                    market_buy_qty,
                                    order_type="buy",
                                    active_key=active_key,
                                    nodes=nodes,
                                    skip_profit_filters=True,
                                )
                                if placed:
                                    print(
                                        f"[{bot_label}] Feeder DOGE buy: {market_buy_qty} SWAP.DOGE at {feeder_buy_price:.8f} "
                                        f"(near ask, 1% of SWAP.HIVE)"
                                    )
                                    if _is_matching_feeder_buy_visible(account_name, feeder_buy_price, market_buy_qty):
                                        sold_price = _attempt_doge_cross_fill(
                                            feeder_account=account_name,
                                            feeder_price=feeder_buy_price,
                                            feeder_qty=market_buy_qty,
                                            nodes=nodes,
                                            bot_label=bot_label,
                                        )
                                        if sold_price > 0:
                                            doge_bid_for_rebuy = float(doge_market.get("highestBid", 0)) if doge_market else 0
                                            doge_ask_for_rebuy = float(doge_market.get("lowestAsk", 0)) if doge_market else 0
                                            rebuy_price = _compute_doge_rebuy_price(
                                                doge_bid_for_rebuy,
                                                doge_ask_for_rebuy,
                                                sold_price,
                                            )
                                            rebought = _place_doge_rebuy_after_sell(
                                                account_name=DOGE_EXECUTION_ACCOUNT,
                                                active_key=DOGE_EXECUTION_ACTIVE_KEY,
                                                nodes=nodes,
                                                qty=market_buy_qty,
                                                rebuy_price=rebuy_price,
                                                bot_label=bot_label,
                                                reason=f"tandem:{account_name}",
                                                sale_price=sold_price,
                                            )
                                            _append_doge_audit(
                                                {
                                                    "event": "feeder_tandem",
                                                    "source_bot": bot_label,
                                                    "feeder_account": account_name,
                                                    "doge_execution_account": DOGE_EXECUTION_ACCOUNT,
                                                    "qty": round(float(market_buy_qty), 8),
                                                    "feeder_buy_price": round(float(feeder_buy_price), 8),
                                                    "tandem_sell_submitted": bool(sold_price > 0),
                                                    "rebuy_submitted": bool(rebought),
                                                    "tandem_sell_price": round(float(sold_price), 8) if sold_price > 0 else 0,
                                                    "rebuy_price": round(float(rebuy_price), 8) if rebuy_price > 0 else 0,
                                                }
                                            )
                                            if sold_price > 0 and rebought:
                                                print(
                                                    f"[{bot_label}] DOGE FLOW VERIFIED: tandem sell to feeder {account_name} "
                                                    f"and immediate DOGE rebuy both submitted."
                                                )
                                            elif sold_price > 0 and not rebought:
                                                print(
                                                    f"[{bot_label}] DOGE FLOW PARTIAL: tandem sell submitted, rebuy not submitted."
                                                )
                                        elif STRICT_DOGE_COORD_CANCEL_UNMATCHED_FEEDER:
                                            _cancel_matching_feeder_buy(
                                                account_name=account_name,
                                                active_key=active_key,
                                                nodes=nodes,
                                                target_price=feeder_buy_price,
                                                target_qty=market_buy_qty,
                                                bot_label=bot_label,
                                            )
                                    else:
                                        print(
                                            f"[{bot_label}] DOGE tandem sell skipped: feeder buy not yet visible in buyBook."
                                        )
                                        if STRICT_DOGE_COORD_CANCEL_UNMATCHED_FEEDER:
                                            _cancel_matching_feeder_buy(
                                                account_name=account_name,
                                                active_key=active_key,
                                                nodes=nodes,
                                                target_price=feeder_buy_price,
                                                target_qty=market_buy_qty,
                                                bot_label=bot_label,
                                            )
                                else:
                                    print(f"[{bot_label}] Feeder DOGE buy skipped (filters/RC/duplicate).")
                            except Exception as e:
                                print(f"[{bot_label}] Aggressive market buy exception: {e}")
                else:
                    skip_reason = _classify_feeder_price_skip(doge_bid, doge_ask, feeder_buy_price)
                    if skip_reason == "spread-too-tight":
                        print(
                            f"[{bot_label}] Feeder DOGE buy skipped: spread too tight for maker bid "
                            f"(bid={doge_bid}, ask={doge_ask})."
                        )
                    elif skip_reason == "quote-unavailable":
                        print(
                            f"[{bot_label}] Feeder DOGE buy skipped: SWAP.DOGE quote unavailable "
                            f"(bid={doge_bid}, ask={doge_ask})."
                        )
                    else:
                        print(
                            f"[{bot_label}] Feeder DOGE buy skipped: price guard blocked placement "
                            f"(bid={doge_bid}, ask={doge_ask})."
                        )

    print(f"[{bot_label}] Quotes: BUY={buy_quote}, SELL={sell_quote}")
    if token == "SWAP.DOGE":
        buy_qty = round(hive_balance * 0.01 / buy_quote, 8) if buy_quote > 0 else 0
        sell_qty = round(token_balance * 0.15, 8)
    else:
        buy_qty = round(hive_balance * 0.20 / buy_quote, 8) if buy_quote > 0 else 0
        sell_qty = round(token_balance * 0.20, 8)

    open_orders = get_open_orders(account_name, token)
    duplicate_buy = any(o.get("type") == "buy" and float(o.get("price", 0)) == buy_quote for o in open_orders)
    duplicate_sell = any(o.get("type") == "sell" and float(o.get("price", 0)) == sell_quote for o in open_orders)
    if token == "SWAP.MATIC":
        duplicate_buy = False
        duplicate_sell = False

    if token == "SWAP.DOGE":
        print(f"[{bot_label}] DOGE buy leg uses immediate rebuy-after-sell only (no standalone buy order).")
    if token == "SWAP.DOGE" and doge_paused:
        print(f"[{bot_label}] BUY skipped: DOGE coordination pause active until sell path recovers.")
    elif token == "SWAP.DOGE":
        pass
    elif buy_qty > 0 and not duplicate_buy and can_transact:
        try:
            order_id = place_order(
                account_name,
                token,
                buy_quote,
                buy_qty,
                order_type="buy",
                active_key=active_key,
                nodes=nodes,
            )
            if order_id:
                state.track_order(order_id, "buy", buy_quote, buy_qty)
                print(f"[{bot_label}] BUY order submitted: {buy_qty} {token} at {buy_quote}")
        except Exception:
            pass
    elif buy_qty > 0 and not duplicate_buy and not can_transact:
        print(f"[{bot_label}] BUY skipped: HIVE_ACTIVE_KEY not set.")

    if sell_qty > 0 and sell_quote > 0 and not duplicate_sell and can_transact:
        if token == "SWAP.DOGE" and sell_qty < MIN_DOGE_COORD_QTY:
            print(
                f"[{bot_label}] SELL skipped: DOGE quantity {sell_qty:.8f} "
                f"below minimum {MIN_DOGE_COORD_QTY:.8f}."
            )
            print(f"[{bot_label}] Trade cycle for {token} complete.")
            print("==============================\n")
            return
        if token == "SWAP.DOGE" and DOGE_STRICT_COORD_ONLY and doge_rebuy_quote <= 0:
            print(
                f"[{bot_label}] SELL skipped: strict DOGE mode requires a profitable rebuy quote "
                f"before selling into feeder support bids."
            )
            print(f"[{bot_label}] Trade cycle for {token} complete.")
            print("==============================\n")
            return
        try:
            order_id = place_order(
                account_name,
                token,
                sell_quote,
                sell_qty,
                order_type="sell",
                active_key=active_key,
                nodes=nodes,
            )
            if order_id:
                state.track_order(order_id, "sell", sell_quote, sell_qty)
                print(f"[{bot_label}] SELL order submitted: {sell_qty} {token} at {sell_quote}")
                if token == "SWAP.DOGE" and doge_rebuy_quote > 0:
                    rebought = _place_doge_rebuy_after_sell(
                        account_name=account_name,
                        active_key=active_key,
                        nodes=nodes,
                        qty=sell_qty,
                        rebuy_price=doge_rebuy_quote,
                        bot_label=bot_label,
                        reason="doge-cycle-sell",
                        sale_price=sell_quote,
                    )
                    _append_doge_audit(
                        {
                            "event": "doge_cycle_sell",
                            "source_bot": bot_label,
                            "doge_execution_account": account_name,
                            "qty": round(float(sell_qty), 8),
                            "sell_price": round(float(sell_quote), 8),
                            "rebuy_submitted": bool(rebought),
                            "rebuy_price": round(float(doge_rebuy_quote), 8),
                        }
                    )
                    if rebought:
                        print(f"[{bot_label}] DOGE FLOW VERIFIED: cycle sell and immediate rebuy both submitted.")
                    else:
                        print(f"[{bot_label}] DOGE FLOW PARTIAL: cycle sell submitted, rebuy not submitted.")
        except Exception:
            pass
    elif sell_qty > 0 and sell_quote > 0 and not duplicate_sell and not can_transact:
        print(f"[{bot_label}] SELL skipped: HIVE_ACTIVE_KEY not set.")
    elif token == "SWAP.DOGE" and sell_quote <= 0:
        print(f"[{bot_label}] SELL skipped: no feeder buy support available this cycle.")

    # Guarded micro-buy compatibility: only place if minimum-profit edge is available.
    if token == "SWAP.DOGE" and DOGE_STRICT_COORD_ONLY:
        print(
            f"[{bot_label}] Strict DOGE mode active: skipping standalone micro/cycle buys; "
            f"coordination sells and profitable rebuys only."
        )
    else:
        # Check if recent cancelled orders have a higher required sell threshold
        remembered_required_sell = _get_recent_required_sell_price(token)
        if remembered_required_sell > 0 and sell_quote > 0 and sell_quote < remembered_required_sell:
            print(
                f"[{bot_label}] {token} micro buy gated by loss-avoidance: "
                f"current sell_quote={sell_quote:.8f} < remembered required={remembered_required_sell:.8f} "
                f"from recent cancelled/placed attempts. Skipping to avoid loss on recovered capital."
            )
        else:
            _attempt_token_micro_buy(
                account_name=account_name,
                token=token,
                active_key=active_key,
                nodes=nodes,
                ask_price=ask,
                bot_label=bot_label,
                support_sell_price=sell_quote,
            )

        if token == "SWAP.DOGE":
            _attempt_doge_cycle_market_buy(
                account_name=account_name,
                active_key=active_key,
                nodes=nodes,
                ask_price=ask,
                bot_label=bot_label,
                feeder_bid=doge_feeder_bid,
                market_bid=bid,
            )

    print(f"[{bot_label}] Trade cycle for {token} complete.")
    print("==============================\n")
