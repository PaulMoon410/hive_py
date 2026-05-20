def ensure_sufficient_rc(account_name, min_rc=10.0):
    rc_percent = get_resource_credits(account_name)
    if rc_percent is None:
        print(f"[BNB BOT] Unable to fetch Resource Credits. Blocking transaction.")
        return False
    if rc_percent < min_rc:
        print(f"[BNB BOT] Resource Credits too low ({rc_percent}%). Blocking transaction.")
        return False
    return True
from profit_strategies import get_profit_percent, get_dynamic_min_profit_percent, adaptive_profit_floor, guarantee_profit
import time
import json
import os
from bot_cycle_control import run_cycle_preflight
from unified_bot_logic import run_ltc_style_logic
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, get_balance, buy_peakecoin_gas, cancel_oldest_order, track_trade_attempt, get_success_rate, buy_matic_gas, cancel_order

# Persistent trade history file
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), ".bnb_trade_history.json")
UNIFIED_STATE_FILE = os.path.join(os.path.dirname(__file__), ".bnb_state_unified.json")

def load_trade_history():
    try:
        if os.path.exists(TRADE_HISTORY_FILE):
            with open(TRADE_HISTORY_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"buys": [], "sells": []}

def save_trade_history(history):
    try:
        with open(TRADE_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass

def record_buy(price, qty, order_id=None):
    history = load_trade_history()
    history["buys"].append({
        "price": price,
        "qty": qty,
        "timestamp": time.time(),
        "order_id": order_id
    })
    save_trade_history(history)

def record_sell(price, qty, order_id=None):
    history = load_trade_history()
    history["sells"].append({
        "price": price,
        "qty": qty,
        "timestamp": time.time(),
        "order_id": order_id
    })
    save_trade_history(history)

def get_last_buy_price():
    history = load_trade_history()
    if history["buys"]:
        return float(history["buys"][-1]["price"])
    return 0.0

def get_last_sell_price():
    history = load_trade_history()
    if history["sells"]:
        return float(history["sells"][-1]["price"])
    return 0.0

def is_profitable_sell(sell_price):
    last_buy = get_last_buy_price()
    return sell_price > last_buy


import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# Hive account details
HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "peakecoin.bnb")
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "5JgXLzFB8fsH64WPWD9fzC4sJQyxSXhn4ykqXrakCnJaSfDjNuL")
HIVE_NODES = ["https://api.hive.blog", "https://anyx.io"]

# Define the token symbol for this bot
TOKEN = "SWAP.BNB"
DELAY = 3600
MIN_OPEN_ORDERS_TARGET = 150
MAX_REFILL_ORDERS_PER_CYCLE = 20
REFILL_ORDER_QTY = 0.00000001
REFILL_PRICE_TICK = 0.00000001
LOW_RC_CONSECUTIVE_COUNT = 0  # Track consecutive low RC occurrences
DYNAMIC_DELAY = DELAY  # Start with base delay
PROFIT_FLOOR = 0.002  # 0.2%
PROFIT_CEILING = 0.03  # 3%
SPREAD_MULTIPLIER = 1.5
BNB_BUY_DISCOUNT_MIN = 0.94  # Conservative when success is low
BNB_BUY_DISCOUNT_MAX = 0.99  # Aggressive when success is high
BNB_MIN_SPREAD_PCT = 0.003  # Require at least 0.3% spread
BNB_MIN_EXPECTED_EDGE_PCT = 0.002  # Require at least 0.2% edge vs ask
BNB_ENABLE_TINY_BUY = True
BNB_PROFIT_FLOOR_START = 0.0015  # 0.15% starting floor
BNB_PROFIT_FLOOR_MAX = 0.01  # 1.0% max floor as success grows
BNB_PRICE_DROP_GUARD = 0.01  # Skip buys if ask drops >1% vs last cycle
BNB_MIN_PROFIT_OVER_COST = 0.002  # Require at least 0.2% over last buy price
BNB_SCALP_ENABLE = True
BNB_SCALP_MIN_SPREAD_PCT = 0.002  # 0.2% minimum spread for scalp
BNB_SCALP_PROFIT_PCT = 0.003  # 0.3% scalp target
BNB_SCALP_BUY_DISCOUNT = 0.995  # buy slightly below bid
BNB_SCALP_BALANCE_PCT = 0.0001  # use 0.01% of balances for scalp
BNB_SCALP_BURST_COUNT = 3  # number of quick scalp checks per cycle
BNB_SCALP_BURST_DELAY = 5  # seconds between scalp checks
BNB_BUY_DISTANCE_MIN = 0.001  # 0.1% min distance below bid
BNB_BUY_DISTANCE_MAX = 0.01  # 1.0% max distance below bid
BNB_BUY_DISTANCE_STEP = 0.001  # 0.1% step per successful buy
BNB_SELL_DISTANCE_MIN = 0.001  # 0.1% min distance above ask
BNB_SELL_DISTANCE_MAX = 0.02  # 2.0% max distance above ask
BNB_SELL_DISTANCE_STEP = 0.0015  # 0.15% step per successful sell
BNB_ENABLE_GAS_BUY = True

BNB_STATE_FILE = os.path.join(os.path.dirname(__file__), ".bnb_state.json")

def _load_bnb_state():
    try:
        if os.path.exists(BNB_STATE_FILE):
            with open(BNB_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_bnb_state(state):
    try:
        with open(BNB_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def _get_last_ask():
    state = _load_bnb_state()
    try:
        return float(state.get("last_ask", 0))
    except Exception:
        return 0.0

def _set_last_ask(value):
    state = _load_bnb_state()
    state["last_ask"] = float(value)
    _save_bnb_state(state)

def _get_last_buy_price():
    state = _load_bnb_state()
    try:
        return float(state.get("last_buy_price", 0))
    except Exception:
        return 0.0

def _set_last_buy_price(value):
    state = _load_bnb_state()
    state["last_buy_price"] = float(value)
    _save_bnb_state(state)

def _get_distance_state():
    state = _load_bnb_state()
    try:
        buy_dist = float(state.get("buy_distance", BNB_BUY_DISTANCE_MIN))
    except Exception:
        buy_dist = BNB_BUY_DISTANCE_MIN
    try:
        sell_dist = float(state.get("sell_distance", BNB_SELL_DISTANCE_MIN))
    except Exception:
        sell_dist = BNB_SELL_DISTANCE_MIN
    buy_dist = max(BNB_BUY_DISTANCE_MIN, min(BNB_BUY_DISTANCE_MAX, buy_dist))
    sell_dist = max(BNB_SELL_DISTANCE_MIN, min(BNB_SELL_DISTANCE_MAX, sell_dist))
    return buy_dist, sell_dist

def _set_distance_state(buy_dist=None, sell_dist=None):
    state = _load_bnb_state()
    if buy_dist is not None:
        state["buy_distance"] = float(buy_dist)
    if sell_dist is not None:
        state["sell_distance"] = float(sell_dist)
    _save_bnb_state(state)

def _get_pending_scalps():
    state = _load_bnb_state()
    try:
        return state.get("pending_scalps", {})
    except Exception:
        return {}

def _set_pending_scalps(data):
    state = _load_bnb_state()
    state["pending_scalps"] = data
    _save_bnb_state(state)

def _clear_pending_scalps():
    state = _load_bnb_state()
    if "pending_scalps" in state:
        del state["pending_scalps"]
        _save_bnb_state(state)

def _price_equal(a, b, tol=1e-8):
    try:
        return abs(float(a) - float(b)) <= tol
    except Exception:
        return False

def _cancel_pending_scalps(account_name, token):
    pending = _get_pending_scalps()
    if not pending:
        return
    try:
        orders = get_open_orders_from_orderbook(account_name, token)
    except Exception:
        orders = []
    if not orders:
        _clear_pending_scalps()
        return

    to_cancel = []
    for o in orders:
        side = (o.get("type") or o.get("side") or "").lower()
        price = o.get("price") or o.get("tokenPrice")
        txid = o.get("txId")
        if not txid or not price:
            continue
        if side == "buy" and pending.get("buy_price") is not None and _price_equal(price, pending.get("buy_price")):
            to_cancel.append(txid)
        if side == "sell" and pending.get("sell_price") is not None and _price_equal(price, pending.get("sell_price")):
            to_cancel.append(txid)

    for txid in to_cancel:
        try:
            cancel_order(account_name, txid, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            print(f"[BNB BOT] Cancelled stale scalp order {txid}.")
        except Exception as e:
            print(f"[BNB BOT] Scalp cancel exception for {txid}: {e}")

    if to_cancel:
        _clear_pending_scalps()

def _calc_buy_discount(success_rate):
    try:
        rate = max(0.0, min(1.0, float(success_rate)))
    except Exception:
        rate = 0.5
    tuned = BNB_BUY_DISCOUNT_MIN + (BNB_BUY_DISCOUNT_MAX - BNB_BUY_DISCOUNT_MIN) * (rate ** 1.5)
    return round(tuned, 4)

def get_resource_credits(account_name):
    try:
        import requests
        url = "https://api.hive.blog"
        payload = {
            "jsonrpc": "2.0",
            "method": "rc_api.find_rc_accounts",
            "params": {"accounts": [account_name]},
            "id": 1
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            rc = data.get('result', {}).get('rc_accounts', [{}])[0]
            if rc and 'rc_manabar' in rc and 'max_rc' in rc:
                current = int(rc['rc_manabar']['current_mana'])
                max_rc = int(rc['max_rc'])
                percent = round(current / max_rc * 100, 2) if max_rc > 0 else 0.0
                return percent
    except Exception:
        pass
    return None

def get_total_orders_from_orderbook(account_name):
    """Fetch total order count from buyBook and sellBook (more reliable than openOrders)"""
    import requests
    try:
        combined = []
        nodes = ["https://api.hive-engine.com/rpc/contracts", "https://herpc.dtools.dev", "https://engine.rishipanthee.com/rpc"]
        for node in nodes:
            try:
                for table in ("buyBook", "sellBook"):
                    payload = {
                        "jsonrpc": "2.0",
                        "method": "find",
                        "params": {
                            "contract": "market",
                            "table": table,
                            "query": {"account": account_name},
                            "limit": 1000
                        },
                        "id": 1
                    }
                    r = requests.post(node, json=payload, timeout=10)
                    if r.status_code == 200:
                        res = r.json().get("result", [])
                        if isinstance(res, list):
                            combined.extend(res)
                if combined:
                    return len(combined)
            except Exception:
                continue
        return 0
    except Exception:
        return 0

def get_open_orders_from_orderbook(account_name, token=None):
    """Fetch open orders for account (optionally token) from buyBook/sellBook."""
    import requests
    try:
        combined = []
        nodes = [
            "https://api.hive-engine.com/rpc/contracts",
            "https://herpc.dtools.dev",
            "https://engine.rishipanthee.com/rpc",
            "https://api2.hive-engine.com/rpc/contracts",
        ]
        for node in nodes:
            try:
                for table in ("buyBook", "sellBook"):
                    query = {"account": account_name}
                    if token:
                        query["symbol"] = token
                    payload = {
                        "jsonrpc": "2.0",
                        "method": "find",
                        "params": {
                            "contract": "market",
                            "table": table,
                            "query": query,
                            "limit": 1000,
                        },
                        "id": 1,
                    }
                    r = requests.post(node, json=payload, timeout=10)
                    if r.status_code == 200:
                        res = r.json().get("result", [])
                        if isinstance(res, list):
                            combined.extend(res)
                if combined:
                    return combined
            except Exception:
                continue
        return []
    except Exception:
        return []

def smart_trade(account_name, token):
    global LOW_RC_CONSECUTIVE_COUNT, DYNAMIC_DELAY
    allowed, DYNAMIC_DELAY = run_cycle_preflight(
        "[BNB BOT]",
        account_name,
        dynamic_delay=DYNAMIC_DELAY,
        divider="==============================",
    )
    if not allowed:
        return

    return run_ltc_style_logic(
        account_name=account_name,
        token=token,
        active_key=HIVE_ACTIVE_KEY,
        nodes=HIVE_NODES,
        state_file=UNIFIED_STATE_FILE,
        bot_label="BNB BOT",
    )

    # Early RC check before any actions
    rc_percent = get_resource_credits(account_name)
    if rc_percent is None:
        print(f"[BNB BOT] Unable to fetch Resource Credits. Skipping trade cycle.")
        print("==============================\n")
        return
    if rc_percent < 10.0:
        LOW_RC_CONSECUTIVE_COUNT += 1
        DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY * 2)
        print(f"[BNB BOT] Resource Credits: {rc_percent}%")
        print(f"[BNB BOT] WARNING: Resource Credits too low ({rc_percent}%). Skipping trade cycle.")
        print(f"[BNB BOT] Low RC count: {LOW_RC_CONSECUTIVE_COUNT}. Next delay: {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
        print("==============================\n")
        return

    matic_bought = False
    def _buy_matic(tag):
        nonlocal matic_bought
        if not BNB_ENABLE_GAS_BUY:
            print(f"[BNB BOT] Gas-buy helper disabled by config ({tag}).")
            return
        if matic_bought:
            return
        if not ensure_sufficient_rc(account_name):
            print(f"[BNB BOT] Skipping SWAP.DOGE gas buy due to low RC ({tag}).")
            return
        matic_bought = True
        try:
            success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            if success:
                print(f"[BNB BOT] SWAP.DOGE gas buy submitted ({tag}).")
            else:
                print(f"[BNB BOT] SWAP.DOGE gas buy skipped or failed ({tag}).")
        except Exception as e:
            print(f"[BNB BOT] SWAP.DOGE buy exception ({tag}): {e}")

    print("\n==============================")
    print(f"[BNB BOT] Starting Smart Trade for {token}")
    _buy_matic("cycle-start")

    rc_percent = get_resource_credits(account_name)
    if rc_percent is not None:
        print(f"[BNB BOT] Resource Credits: {rc_percent}%")
        if rc_percent < 10.0:
            LOW_RC_CONSECUTIVE_COUNT += 1
            DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY * 2)
            print(f"[BNB BOT] WARNING: Resource Credits too low ({rc_percent}%). Skipping trade cycle.")
            print(f"[BNB BOT] Low RC count: {LOW_RC_CONSECUTIVE_COUNT}. Next delay: {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
            print("==============================\n")
            _buy_matic("low-rc-skip")
            return
        else:
            if DYNAMIC_DELAY > DELAY:
                DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY - 120)
                if DYNAMIC_DELAY == DELAY:
                    LOW_RC_CONSECUTIVE_COUNT = 0
                print(f"[BNB BOT] RC recovered! Reducing delay to {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
    else:
        print(f"[BNB BOT] Resource Credits: Unable to fetch.")
    
    # CHECK TOTAL OPEN ORDERS using orderbook (more reliable than openOrders table)
    open_count = get_total_orders_from_orderbook(account_name)
    print(f"[BNB BOT] Current total open orders (from orderbook): {open_count}")

    # Cancel any pending scalp orders from previous cycle
    _cancel_pending_scalps(account_name, token)

    if open_count < 100:
        print(f"[BNB BOT] Open orders at {open_count}. Below 100: No cancels allowed, only placing new orders if possible.")
        # Proceed with normal trading, but do not cancel any orders
    elif open_count >= 200:
        print(f"[BNB BOT] 🚨 CRITICAL: Account maxed out with {open_count} orders! Cancelling 3 oldest orders...")
        for i in range(3):
            if not ensure_sufficient_rc(account_name):
                print(f"[BNB BOT] Skipping cancel_oldest_order due to low RC.")
                break
            try:
                cancel_oldest_order(account_name, None, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                print(f"[BNB BOT] Cancelled oldest order {i+1}/3")
                time.sleep(2)
            except Exception as e:
                print(f"[BNB BOT] Failed to cancel order {i+1}: {e}")
        print(f"[BNB BOT] Skipping trade cycle to allow orders to clear.")
        print("==============================\n")
        _buy_matic("max-orders-skip")
        return
    elif open_count > 190:
        print(f"[BNB BOT] Open orders at {open_count}. Above 190 target, cancelling oldest {token} order to self-regulate.")
        if ensure_sufficient_rc(account_name):
            try:
                cancel_oldest_order(account_name, token, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                time.sleep(2)
            except Exception as e:
                print(f"[BNB BOT] Failed to cancel oldest {token} order: {e}")
        else:
            print(f"[BNB BOT] Skipping cancel_oldest_order due to low RC.")
    else:
        print(f"[BNB BOT] Open orders at {open_count}. Between 100 and 189: No cancels, normal trading allowed.")
        # Proceed with normal trading, but do not cancel any orders
    market = get_orderbook_top(token)
    if not market:
        print(f"[BNB BOT] Market fetch failed for {token}. Skipping this cycle.")
        print("==============================\n")
        time.sleep(2)
        _buy_matic("market-fetch-fail")
        time.sleep(2)
        return
    print(f"[BNB BOT] Market fetch success for {token}.")
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))

    if open_count < MIN_OPEN_ORDERS_TARGET:
        deficit = MIN_OPEN_ORDERS_TARGET - open_count
        refill_attempts = min(deficit, MAX_REFILL_ORDERS_PER_CYCLE)
        refill_base_price = bid if bid > 0 else (ask * 0.999 if ask > 0 else 0)
        refill_placed = 0
        if refill_base_price > 0:
            print(f"[BNB BOT] Open orders below target ({open_count}/{MIN_OPEN_ORDERS_TARGET}). Attempting {refill_attempts} refill buys.")
            for i in range(refill_attempts):
                price = round(max(REFILL_PRICE_TICK, refill_base_price * (1 - (0.0002 * (i + 1)))), 8)
                if not ensure_sufficient_rc(account_name):
                    print("[BNB BOT] Refill stopped early due to low RC.")
                    break
                try:
                    placed = place_order(
                        account_name,
                        token,
                        price,
                        REFILL_ORDER_QTY,
                        order_type="buy",
                        active_key=HIVE_ACTIVE_KEY,
                        nodes=HIVE_NODES,
                    )
                    if placed:
                        refill_placed += 1
                except Exception as e:
                    print(f"[BNB BOT] Refill order {i+1}/{refill_attempts} exception: {e}")
                time.sleep(0.2)
            if refill_placed > 0:
                open_count += refill_placed
                print(f"[BNB BOT] Refill placed {refill_placed} orders. Estimated open orders now: {open_count}")
            else:
                print(f"[BNB BOT] Refill attempts placed 0 orders this cycle.")
        else:
            print("[BNB BOT] Refill skipped: no valid market price anchor.")

    last_ask = _get_last_ask()
    price_drop_guard = last_ask > 0 and ask > 0 and ask < last_ask * (1 - BNB_PRICE_DROP_GUARD)
    _set_last_ask(ask)
    success_rate = get_success_rate(HIVE_ACCOUNT, token)
    buy_discount = _calc_buy_discount(success_rate)
    buy_distance, sell_distance = _get_distance_state()
    buy_price = round(bid * buy_discount, 8) if bid > 0 else 0
    if bid > 0:
        min_buy_price = round(bid * (1 - buy_distance), 8)
        if buy_price < min_buy_price:
            buy_price = min_buy_price

    # Always use 1% of SWAP.HIVE balance to buy SWAP.DOGE every cycle (aggressive market buy)
    if bid > 0:
        swap_hive_balance = get_balance(account_name, "SWAP.HIVE")
        one_percent_hive = swap_hive_balance * 0.01
        if one_percent_hive > 0:
            # Always buy SWAP.DOGE, not the bot's own token
            doge_bid = 0
            try:
                doge_market = get_orderbook_top("SWAP.DOGE")
                doge_bid = float(doge_market.get("highestBid", 0)) if doge_market else 0
            except Exception:
                pass
            if doge_bid > 0:
                market_buy_qty = round(one_percent_hive / doge_bid, 8)
                try:
                    place_order(account_name, "SWAP.DOGE", doge_bid, market_buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                    print(f"[BNB BOT] Aggressive market buy: {market_buy_qty} SWAP.DOGE at {doge_bid} (1% of SWAP.HIVE)")
                except Exception as e:
                    print(f"[BNB BOT] Aggressive market buy exception: {e}")
            else:
                print(f"[BNB BOT] Could not fetch SWAP.DOGE bid for 1% buy.")

    adaptive_floor = adaptive_profit_floor(success_rate, base_floor=PROFIT_FLOOR, base_ceiling=PROFIT_CEILING)
    ramp_floor = BNB_PROFIT_FLOOR_START + (BNB_PROFIT_FLOOR_MAX - BNB_PROFIT_FLOOR_START) * (success_rate ** 2)
    effective_floor = max(adaptive_floor, ramp_floor)

    min_profit_percent = get_dynamic_min_profit_percent(
        bid,
        ask,
        floor=effective_floor,
        ceiling=PROFIT_CEILING,
        spread_multiplier=SPREAD_MULTIPLIER,
    )
    if ask > 0:
        spread_pct = (ask - bid) / ask
        if spread_pct < BNB_MIN_SPREAD_PCT:
            print(f"[BNB BOT] Spread {spread_pct*100:.3f}% below BNB minimum. Skipping trade cycle.")
            print("==============================\n")
            _buy_matic("spread-skip")
            return
        expected_edge = (ask - buy_price) / max(buy_price, 1e-12)
        if expected_edge < max(min_profit_percent, BNB_MIN_EXPECTED_EDGE_PCT):
            print(f"[BNB BOT] Expected edge {expected_edge*100:.3f}% below target. Skipping trade cycle.")
            print("==============================\n")
            _buy_matic("edge-skip")
            return

    min_sell_price = round(buy_price * (1 + min_profit_percent), 8) if buy_price > 0 else 0
    sell_price = round(max(ask, min_sell_price), 8) if (ask > 0 or min_sell_price > 0) else 0
    if ask > 0:
        max_sell_price = round(ask * (1 + sell_distance), 8)
        if sell_price > max_sell_price:
            sell_price = max_sell_price
    sell_price = guarantee_profit(buy_price, sell_price, min_margin=0.0001)
    sell_price = round(sell_price, 8)
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    bnb_balance = get_balance(account_name, token)
    print(f"[BNB BOT] Balances: HIVE={hive_balance:.8f} | {token}={bnb_balance:.8f}")
    buy_qty = round(hive_balance * 0.20 / buy_price, 8) if buy_price > 0 else 0
    sell_qty = round(bnb_balance * 0.20, 8)
    
    print(f"[BNB BOT] Success rate: {success_rate*100:.1f}% | Buy discount: {buy_discount:.4f} | Adaptive floor: {adaptive_floor*100:.3f}% | Ramp floor: {ramp_floor*100:.3f}% | Dynamic target: {min_profit_percent * 100:.3f}%")
    print(f"[BNB BOT] Preparing BUY: {buy_qty} {token} at {buy_price}")
    open_orders = get_open_orders(account_name, token)
    duplicate_buy = any(o.get('type') == 'buy' and float(o.get('price', 0)) == buy_price for o in open_orders)
    buy_submitted = False
    sell_submitted = False
    if price_drop_guard:
        print(f"[BNB BOT] Price drop guard triggered (ask {ask} < last {last_ask}). Skipping BUY to avoid catching a falling price.")
    elif buy_qty <= 0:
        print(f"[BNB BOT] Skipping BUY: buy_qty is zero or negative. Check HIVE balance and buy price.")
    elif duplicate_buy:
        print(f"[BNB BOT] Skipping BUY: Duplicate buy order at {buy_price} detected.")
    else:
        if ensure_sufficient_rc(account_name):
            try:
                placed = place_order(account_name, token, buy_price, buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                if placed:
                    buy_submitted = True
                    _set_last_buy_price(buy_price)
                    record_buy(buy_price, buy_qty)
                    print(f"[BNB BOT] BUY order submitted: {buy_qty} {token} at {buy_price}")
                else:
                    print(f"[BNB BOT] BUY order blocked by filters: {buy_qty} {token} at {buy_price}")
                track_trade_attempt(account_name, token, filled=True)
                time.sleep(5)
                open_orders = get_open_orders(account_name, token)
                if open_orders:
                    print(f"[BNB BOT] Open orders after BUY: {len(open_orders)} found.")
                else:
                    print(f"[BNB BOT] No open orders found after BUY (may be node delay).")
                time.sleep(1)
            except Exception as e:
                print(f"[BNB BOT] BUY order exception: {e}")
        else:
            print(f"[BNB BOT] Skipping BUY order due to low RC.")
    
    # Place SELL order at market-based price
    if sell_price > 0 and sell_qty > 0 and is_profitable_sell(sell_price):
        if _get_last_buy_price() > 0:
            min_cost_sell = round(_get_last_buy_price() * (1 + BNB_MIN_PROFIT_OVER_COST), 8)
            if sell_price < min_cost_sell:
                print(f"[BNB BOT] SELL skipped: sell_price {sell_price} below cost floor {min_cost_sell}.")
                print("==============================\n")
                _buy_matic("sell-cost-skip")
                return
        open_orders = get_open_orders(account_name, token)
        duplicate_sell = any(o.get('type') == 'sell' and float(o.get('price', 0)) == sell_price for o in open_orders)
        if duplicate_sell:
            print(f"[BNB BOT] Skipping SELL: Duplicate sell order at {sell_price} detected.")
        else:
            if ensure_sufficient_rc(account_name):
                try:
                    placed = place_order(account_name, token, sell_price, sell_qty, order_type="sell", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                    if placed:
                        sell_submitted = True
                        record_sell(sell_price, sell_qty)
                        print(f"[BNB BOT] SELL order submitted: {sell_qty} {token} at {sell_price}")
                    else:
                        print(f"[BNB BOT] SELL order blocked by filters: {sell_qty} {token} at {sell_price}")
                    print(f"[BNB BOT] Profit percent: {get_profit_percent(buy_price, sell_price)}%")
                    track_trade_attempt(account_name, token, filled=True)
                    time.sleep(5)
                    open_orders = get_open_orders(account_name, token)
                    if open_orders:
                        print(f"[BNB BOT] Open orders after SELL: {len(open_orders)} found.")
                    else:
                        print(f"[BNB BOT] No open orders found after SELL (may be node delay).")
                    time.sleep(1)
                except Exception as e:
                    print(f"[BNB BOT] SELL order exception: {e}")
            else:
                print(f"[BNB BOT] Skipping SELL order due to low RC.")
    elif sell_price > 0 and sell_qty > 0:
        print(f"[BNB BOT] SELL order skipped: Not profitable (sell {sell_price} <= last buy {get_last_buy_price()})")
    else:
        print(f"[BNB BOT] SELL order skipped: sell_qty is zero.")

    if buy_submitted:
        buy_distance = min(BNB_BUY_DISTANCE_MAX, buy_distance + BNB_BUY_DISTANCE_STEP)
    else:
        buy_distance = max(BNB_BUY_DISTANCE_MIN, buy_distance - BNB_BUY_DISTANCE_STEP)
    if sell_submitted:
        sell_distance = min(BNB_SELL_DISTANCE_MAX, sell_distance + BNB_SELL_DISTANCE_STEP)
    else:
        sell_distance = max(BNB_SELL_DISTANCE_MIN, sell_distance - BNB_SELL_DISTANCE_STEP)
    _set_distance_state(buy_distance, sell_distance)
    
    if BNB_ENABLE_TINY_BUY:
        # Buy smallest increment of token at market price
        time.sleep(2)
        if ensure_sufficient_rc(account_name):
            try:
                if ask > 0:
                    place_order(account_name, token, ask, 0.00000001, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                    print(f"[BNB BOT] {token} tiny buy attempted at {ask}.")
                else:
                    print(f"[BNB BOT] {token} market ask unavailable, skipping tiny buy.")
            except Exception as e:
                print(f"[BNB BOT] {token} tiny buy exception: {e}")
        else:
            print(f"[BNB BOT] Skipping tiny buy due to low RC.")

    # Optional scalp logic (tandem with main strategy) with quick burst checks
    if BNB_SCALP_ENABLE:
        for i in range(BNB_SCALP_BURST_COUNT):
            if not ensure_sufficient_rc(account_name):
                print(f"[BNB BOT] Skipping scalp logic due to low RC.")
                break
            scalp_market = get_orderbook_top(token)
            if not scalp_market:
                break
            scalp_bid = float(scalp_market.get("highestBid", 0))
            scalp_ask = float(scalp_market.get("lowestAsk", 0))
            if scalp_bid <= 0 or scalp_ask <= 0:
                break
            spread_pct = (scalp_ask - scalp_bid) / scalp_ask
            if spread_pct >= BNB_SCALP_MIN_SPREAD_PCT:
                scalp_buy_price = round(scalp_bid * BNB_SCALP_BUY_DISCOUNT, 8)
                scalp_sell_price = round(scalp_buy_price * (1 + BNB_SCALP_PROFIT_PCT), 8)
                scalp_buy_qty = round(hive_balance * BNB_SCALP_BALANCE_PCT / scalp_buy_price, 8) if scalp_buy_price > 0 else 0
                scalp_sell_qty = round(bnb_balance * BNB_SCALP_BALANCE_PCT, 8)
                open_orders = get_open_orders(account_name, token)
                scalp_buy_dup = any(o.get('type') == 'buy' and float(o.get('price', 0)) == scalp_buy_price for o in open_orders)
                scalp_sell_dup = any(o.get('type') == 'sell' and float(o.get('price', 0)) == scalp_sell_price for o in open_orders)

                placed_scalp = False
                if scalp_buy_qty > 0 and not scalp_buy_dup:
                    try:
                        place_order(account_name, token, scalp_buy_price, scalp_buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                        print(f"[BNB BOT] SCALP BUY submitted: {scalp_buy_qty} {token} at {scalp_buy_price}")
                        placed_scalp = True
                    except Exception as e:
                        print(f"[BNB BOT] SCALP BUY exception: {e}")
                if scalp_sell_qty > 0 and not scalp_sell_dup:
                    try:
                        place_order(account_name, token, scalp_sell_price, scalp_sell_qty, order_type="sell", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                        print(f"[BNB BOT] SCALP SELL submitted: {scalp_sell_qty} {token} at {scalp_sell_price}")
                        placed_scalp = True
                    except Exception as e:
                        print(f"[BNB BOT] SCALP SELL exception: {e}")

                if placed_scalp:
                    _set_pending_scalps({"buy_price": scalp_buy_price, "sell_price": scalp_sell_price})

            if i < (BNB_SCALP_BURST_COUNT - 1) and BNB_SCALP_BURST_DELAY > 0:
                time.sleep(BNB_SCALP_BURST_DELAY)
    
    # Always buy smallest increment of SWAP.DOGE at fixed tiny price
    time.sleep(2)
    _buy_matic("cycle-end")

    # Buy PEK gas via centralized helper at end of cycle
    if ensure_sufficient_rc(account_name):
        try:
            buy_peakecoin_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES, pek_amount=0.00000001)
            print("[BNB BOT] PEK gas buy attempted via helper.")
        except Exception as e:
            print(f"[BNB BOT] PEK gas buy exception: {e}")
    else:
        print(f"[BNB BOT] Skipping PEK gas buy due to low RC.")

    print(f"[BNB BOT] Trade cycle for {token} complete.")
    time.sleep(2)

if __name__ == "__main__":
    while True:
        try:
            print(f"[BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(HIVE_ACCOUNT, TOKEN)
            print(f"[BOT] Waiting 60 seconds before next cycle...")
            time.sleep(60)
        except Exception as e:
            print(f"[BOT] Unexpected error in main loop: {e}")
            import traceback
            traceback.print_exc()
        remaining = max(0, DYNAMIC_DELAY - 60)
        if remaining > 0:
            print(f"[BOT] Waiting {remaining} seconds to complete delay interval...")
            time.sleep(remaining)
