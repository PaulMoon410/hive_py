from profit_strategies import get_profit_percent, get_dynamic_min_profit_percent, adaptive_profit_floor, guarantee_profit
import time
import os
import json
from bot_cycle_control import run_cycle_preflight
from unified_bot_logic import run_ltc_style_logic
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, get_balance, buy_peakecoin_gas, cancel_oldest_order, track_trade_attempt, get_success_rate, buy_matic_gas

# Persistent trade history file
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), ".hbd_trade_history.json")
UNIFIED_STATE_FILE = os.path.join(os.path.dirname(__file__), ".hbd_state_unified.json")

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
HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "peakecoin")
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "5JmdCunm6rED9u2XWgGXbthdz3VFdkeALjFyfZaS78K7havZ5kG")
HIVE_NODES = ["https://api.hive.blog", "https://anyx.io"]
TOKEN = "SWAP.HBD"
DELAY = 1500
LOW_RC_CONSECUTIVE_COUNT = 0  # Track consecutive low RC occurrences
DYNAMIC_DELAY = DELAY  # Start with base delay
PROFIT_FLOOR = 0.002  # 0.2%
PROFIT_CEILING = 0.03  # 3%
SPREAD_MULTIPLIER = 1.5
LADDER_START_PROFIT_PCT = 0.20  # Start cycle at 20% spread target
LADDER_MAX_STEPS = 50           # Tighten ladder over 50 cycles/trades
ABOVE_LOWEST_SELL_PCT = 0.001   # 0.10% above lowest ask to restart cycle
MATIC_TINY_PRICE = 0.00000001
LADDER_STEP = 0
PREV_HAD_SELL_ORDER = False
can_transact = HIVE_ACTIVE_KEY not in ("", "your_active_key", None)

def get_ladder_profit_percent(base_profit_percent, floor, step,
                              start_profit_percent=LADDER_START_PROFIT_PCT,
                              max_steps=LADDER_MAX_STEPS):
    """Start high, then linearly tighten toward dynamic target over max_steps."""
    if max_steps <= 0:
        max_steps = 1
    clamped_step = max(0, min(int(step), int(max_steps)))
    progress = clamped_step / max_steps
    end_target = max(float(base_profit_percent), float(floor))
    start_target = max(float(start_profit_percent), end_target)
    target = start_target - ((start_target - end_target) * progress)
    target = max(float(floor), target)
    return round(target, 6)

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

def smart_trade(account_name, token):
    global LOW_RC_CONSECUTIVE_COUNT, DYNAMIC_DELAY, LADDER_STEP, PREV_HAD_SELL_ORDER
    allowed, DYNAMIC_DELAY = run_cycle_preflight(
        "[HBD BOT]",
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
        bot_label="HBD BOT",
    )

    # Re-evaluate can_transact at cycle start in case env changed
    _can_transact = HIVE_ACTIVE_KEY not in ("", "your_active_key", None)
    # Always use 1% of SWAP.HIVE balance to buy SWAP.DOGE every cycle (aggressive market buy)
    market = get_orderbook_top(token)
    bid = float(market.get("highestBid", 0)) if market else 0
    if bid > 0 and _can_transact:
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
                    print(f"[HBD BOT] Aggressive market buy: {market_buy_qty} SWAP.DOGE at {doge_bid} (1% of SWAP.HIVE)")
                except Exception as e:
                    print(f"[HBD BOT] Aggressive market buy exception: {e}")
            else:
                print(f"[HBD BOT] Could not fetch SWAP.DOGE bid for 1% buy.")
    elif not _can_transact:
        print("[HBD BOT] Aggressive DOGE buy skipped: HIVE_ACTIVE_KEY not set.")
    print("\n==============================")
    print(f"[HBD BOT] Starting Smart Trade for {token}")
    try:
        success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
        if success:
            print("[HBD BOT] SWAP.DOGE gas buy submitted (cycle-start).")
        else:
            print("[HBD BOT] SWAP.DOGE gas buy skipped or failed (cycle-start).")
    except Exception as e:
        print(f"[HBD BOT] SWAP.DOGE buy exception (cycle-start): {e}")

    rc_percent = get_resource_credits(account_name)
    if rc_percent is not None:
        print(f"[HBD BOT] Resource Credits: {rc_percent}%")
        if rc_percent < 10.0:
            LOW_RC_CONSECUTIVE_COUNT += 1
            DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY * 2)
            print(f"[HBD BOT] WARNING: Resource Credits too low ({rc_percent}%). Skipping trade cycle.")
            print(f"[HBD BOT] Low RC count: {LOW_RC_CONSECUTIVE_COUNT}. Next delay: {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
            print("==============================\n")
            return
        else:
            if DYNAMIC_DELAY > DELAY:
                DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY - 120)
                if DYNAMIC_DELAY == DELAY:
                    LOW_RC_CONSECUTIVE_COUNT = 0
                print(f"[HBD BOT] RC recovered! Reducing delay to {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
    else:
        print(f"[HBD BOT] Resource Credits: Unable to fetch.")
    
    # CHECK TOTAL OPEN ORDERS using orderbook (more reliable than openOrders table)
    open_count = get_total_orders_from_orderbook(account_name)
    print(f"[HBD BOT] Current total open orders (from orderbook): {open_count}")
    
    # If severely maxed out (200+), cancel oldest orders first; continue trading if capacity is recovered
    if open_count >= 200:
        print(f"[HBD BOT] 🚨 CRITICAL: Account maxed out with {open_count} orders! Cancelling oldest orders to recover capacity...")
        cancelled_txids = set()
        for i in range(5):
            if open_count <= 195:
                break
            try:
                cancelled = cancel_oldest_order(
                    account_name,
                    None,
                    active_key=HIVE_ACTIVE_KEY,
                    nodes=HIVE_NODES,
                    excluded_txids=cancelled_txids,
                )
                if not cancelled:
                    print(f"[HBD BOT] No additional unique orders were cancelled on attempt {i+1}/5.")
                    break
                print(f"[HBD BOT] Cancelled oldest order {i+1}/5")
                time.sleep(2)
                open_count = get_total_orders_from_orderbook(account_name)
            except Exception as e:
                print(f"[HBD BOT] Failed to cancel order {i+1}: {e}")
                break

        print(f"[HBD BOT] Open orders after critical cleanup: {open_count}")
        if open_count >= 200:
            print(f"[HBD BOT] Still at hard limit. Skipping trade cycle to allow orders to clear.")
            print("==============================\n")
            return
    
    # Keep up to 190 open orders; only cancel when exceeding that target
    if open_count > 190:
        print(f"[HBD BOT] Open orders at {open_count}. Above 190 target, cancelling oldest {token} order to self-regulate.")
        try:
            cancel_oldest_order(account_name, token, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            time.sleep(2)  # Give time for the cancellation to propagate
        except Exception as e:
            print(f"[HBD BOT] Failed to cancel oldest {token} order: {e}")
    else:
        print(f"[HBD BOT] Open orders at {open_count}. At/below 190 target, proceeding with normal trading.")
    
    market = get_orderbook_top(token)
    if not market:
        print(f"[HBD BOT] Market fetch failed for {token}. Skipping this cycle.")
        print("==============================\n")
        # Buy smallest increment of SWAP.DOGE at fixed tiny price
        time.sleep(2)
        try:
            success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            if success:
                print("[HBD BOT] SWAP.DOGE gas buy submitted.")
            else:
                print("[HBD BOT] SWAP.DOGE gas buy skipped or failed.")
        except Exception as e:
            print(f"[HBD BOT] SWAP.DOGE buy exception: {e}")
        time.sleep(2)
        return
    print(f"[HBD BOT] Market fetch success for {token}.")
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    buy_price = round(bid * 0.95, 8) if bid > 0 else 0  # Buy at 5% below highest bid

    # Check open sell state for ladder progression
    cycle_open_orders = get_open_orders(account_name, token) or []
    current_has_sell_order = any(o.get('type') == 'sell' for o in cycle_open_orders)
    if PREV_HAD_SELL_ORDER and not current_has_sell_order:
        # Likely filled/cancelled since last cycle: reset ladder to wider profit target
        LADDER_STEP = 0
        print(f"[HBD BOT] Sell order likely filled/cleared. Resetting ladder to wide target.")
    elif current_has_sell_order:
        # Still working sell(s): tighten target for better fill probability
        LADDER_STEP = min(LADDER_STEP + 1, LADDER_MAX_STEPS)
    
    # Adaptive profit floor based on recent success rate
    success_rate = get_success_rate(HIVE_ACCOUNT, token)
    adaptive_floor = adaptive_profit_floor(success_rate, base_floor=PROFIT_FLOOR, base_ceiling=PROFIT_CEILING)
    
    dynamic_min_profit_percent = get_dynamic_min_profit_percent(
        bid,
        ask,
        floor=adaptive_floor,
        ceiling=PROFIT_CEILING,
        spread_multiplier=SPREAD_MULTIPLIER,
    )
    ladder_profit_percent = get_ladder_profit_percent(
        dynamic_min_profit_percent,
        floor=adaptive_floor,
        step=LADDER_STEP,
    )
    min_sell_price = round(buy_price * (1 + ladder_profit_percent), 8) if buy_price > 0 else 0
    # Re-anchor cycle above current lowest ask by configurable percent
    lowest_sell_anchor = round(ask * (1 + ABOVE_LOWEST_SELL_PCT), 8) if ask > 0 else 0
    sell_price = round(max(lowest_sell_anchor, min_sell_price), 8) if (ask > 0 or min_sell_price > 0) else 0
    # Guarantee profit: ensure sell > buy
    sell_price = guarantee_profit(buy_price, sell_price, min_margin=0.0001)
    sell_price = round(sell_price, 8)
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    hbd_balance = get_balance(account_name, token)
    buy_qty = round(hive_balance * 0.20 / buy_price, 8) if buy_price > 0 else 0
    sell_qty = round(hbd_balance * 0.20, 8)
    
    print(
        f"[HBD BOT] Success rate: {success_rate*100:.1f}% | Adaptive floor: {adaptive_floor*100:.3f}% "
        f"| Dynamic target: {dynamic_min_profit_percent*100:.3f}% | Ladder step: {LADDER_STEP}/{LADDER_MAX_STEPS} "
        f"| Ladder target: {ladder_profit_percent*100:.3f}%"
    )
    print(f"[HBD BOT] Preparing BUY: {buy_qty} {token} at {buy_price}")
    print(f"[HBD BOT] Preparing SELL: {sell_qty} {token} at {sell_price}")
    open_orders = get_open_orders(account_name, token) or []
    duplicate_buy = any(o.get('type') == 'buy' and float(o.get('price', 0)) == buy_price for o in open_orders)
    if not _can_transact:
        print("[HBD BOT] BUY skipped: HIVE_ACTIVE_KEY not set.")
    elif buy_qty <= 0:
        print(f"[HBD BOT] Skipping BUY: buy_qty is zero or negative. Check HIVE balance and buy price.")
    elif duplicate_buy:
        print(f"[HBD BOT] Skipping BUY: Duplicate buy order at {buy_price} detected.")
    else:
        try:
            result = place_order(account_name, token, buy_price, buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            if result:
                record_buy(buy_price, buy_qty)
                print(f"[HBD BOT] BUY order submitted: {buy_qty} {token} at {buy_price}")
                track_trade_attempt(account_name, token, filled=True)
                time.sleep(5)
                open_orders = get_open_orders(account_name, token) or []
                if open_orders:
                    print(f"[HBD BOT] Open orders after BUY: {len(open_orders)} found.")
                else:
                    print(f"[HBD BOT] No open orders found after BUY (may be node delay).")
            else:
                print(f"[HBD BOT] BUY order failed: blocked by filters, duplicate protection, cooldown, or insufficient balance.")
            time.sleep(1)
        except Exception as e:
            print(f"[HBD BOT] BUY order exception: {e}")
    
    # Place SELL order at market-based price, only if profitable
    if not _can_transact:
        print("[HBD BOT] SELL skipped: HIVE_ACTIVE_KEY not set.")
    elif sell_price > 0 and sell_qty > 0 and is_profitable_sell(sell_price):
        open_orders = get_open_orders(account_name, token) or []
        duplicate_sell = any(o.get('type') == 'sell' and float(o.get('price', 0)) == sell_price for o in open_orders)
        if duplicate_sell:
            print(f"[HBD BOT] Skipping SELL: Duplicate sell order at {sell_price} detected.")
        else:
            try:
                result = place_order(account_name, token, sell_price, sell_qty, order_type="sell", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                if result:
                    record_sell(sell_price, sell_qty)
                    print(f"[HBD BOT] SELL order submitted: {sell_qty} {token} at {sell_price}")
                    print(f"[HBD BOT] Profit percent: {get_profit_percent(buy_price, sell_price)}%")
                    track_trade_attempt(account_name, token, filled=True)
                    time.sleep(5)
                    open_orders = get_open_orders(account_name, token) or []
                    if open_orders:
                        print(f"[HBD BOT] Open orders after SELL: {len(open_orders)} found.")
                    else:
                        print(f"[HBD BOT] No open orders found after SELL (may be node delay).")
                else:
                    print(f"[HBD BOT] SELL order failed: blocked by filters, duplicate protection, cooldown, or insufficient balance.")
                time.sleep(1)
            except Exception as e:
                print(f"[HBD BOT] SELL order exception: {e}")
    elif sell_price > 0 and sell_qty > 0:
        print(f"[HBD BOT] SELL order skipped: Not profitable (sell {sell_price} <= last buy {get_last_buy_price()})")
    else:
        print(f"[HBD BOT] SELL order skipped: sell_qty is zero.")
    
    # Buy smallest increment of token at market price
    time.sleep(2)
    try:
        tiny_buy_price = round(bid, 8) if bid > 0 else round(ask * 0.9999, 8) if ask > 0 else 0
        if ask > 0 and tiny_buy_price > 0 and tiny_buy_price < ask:
            placed = place_order(account_name, token, tiny_buy_price, 0.00000001, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            if placed:
                print(f"[HBD BOT] {token} tiny maker buy submitted at {tiny_buy_price}.")
            else:
                print(f"[HBD BOT] {token} tiny maker buy skipped by filters or duplicate protection.")
        else:
            print(f"[HBD BOT] {token} market spread unavailable, skipping tiny maker buy.")
    except Exception as e:
        print(f"[HBD BOT] {token} tiny buy exception: {e}")
    
    # Buy smallest increment of SWAP.DOGE at fixed tiny price
    try:
        success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
        if success:
            print("[HBD BOT] SWAP.DOGE gas buy submitted.")
        else:
            print("[HBD BOT] SWAP.DOGE gas buy skipped or failed.")
    except Exception as e:
        print(f"[HBD BOT] SWAP.DOGE buy exception: {e}")
    
    # Buy PEK gas via centralized helper at end of cycle
    try:
        buy_peakecoin_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES, pek_amount=0.00000001)
        print("[HBD BOT] PEK gas buy attempted via helper.")
    except Exception as e:
        print(f"[HBD BOT] PEK gas buy exception: {e}")

    # Persist cycle-to-cycle sell state for ladder reset/tighten behavior
    try:
        end_orders = get_open_orders(account_name, token) or []
        PREV_HAD_SELL_ORDER = any(o.get('type') == 'sell' for o in end_orders)
    except Exception:
        PREV_HAD_SELL_ORDER = current_has_sell_order

    print(f"[HBD BOT] Trade cycle for {token} complete.")
    print("==============================\n")

if __name__ == "__main__":
    print(f"[HBD BOT] Account source: {HIVE_ACCOUNT}")
    print(f"[HBD BOT] Active key set: {HIVE_ACTIVE_KEY not in ('', 'your_active_key', None)}")
    while True:
        try:
            print(f"[BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(HIVE_ACCOUNT, TOKEN)
            print(f"[BOT] Waiting 10 seconds before next cycle...")
            time.sleep(10)
        except Exception as e:
            print(f"[BOT] Unexpected error in main loop: {e}")
            import traceback
            traceback.print_exc()
        remaining = max(0, DYNAMIC_DELAY - 10)
        if remaining > 0:
            print(f"[BOT] Waiting {remaining} seconds to complete delay interval...")
            time.sleep(remaining)
