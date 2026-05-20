from profit_strategies import is_profitable_or_volume_increase, get_profit_percent, get_dynamic_min_profit_percent, adaptive_profit_floor, guarantee_profit
import time
import datetime
import json
import os
from bot_cycle_control import run_cycle_preflight
from unified_bot_logic import run_ltc_style_logic
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, cancel_order, get_balance, buy_peakecoin_gas, cancel_oldest_order, track_trade_attempt, get_success_rate, buy_matic_gas

# Persistent trade history file
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), ".matic_trade_history.json")
UNIFIED_STATE_FILE = os.path.join(os.path.dirname(__file__), ".matic_state_unified.json")

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

# ...existing code...

def smart_trade(account_name, token, forbidden_user=None):
    global LOW_RC_CONSECUTIVE_COUNT, DYNAMIC_DELAY
    # Always use 1% of SWAP.HIVE balance to buy SWAP.DOGE every cycle (aggressive market buy)
    market = get_orderbook_top(token)
    bid = float(market.get("highestBid", 0)) if market else 0
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
                    print(f"[MATIC BOT] Aggressive market buy: {market_buy_qty} SWAP.DOGE at {doge_bid} (1% of SWAP.HIVE)")
                except Exception as e:
                    print(f"[MATIC BOT] Aggressive market buy exception: {e}")
            else:
                print(f"[MATIC BOT] Could not fetch SWAP.DOGE bid for 1% buy.")
    # ...existing code...
from profit_strategies import is_profitable_or_volume_increase, get_profit_percent, get_dynamic_min_profit_percent, adaptive_profit_floor, guarantee_profit
import time
import datetime
import json
import os
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, cancel_order, get_balance, buy_peakecoin_gas, cancel_oldest_order, track_trade_attempt, get_success_rate, buy_matic_gas

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# 🔐 Hive account
HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "peakecoin.matic")
HIVE_POSTING_KEY = ""  # Not needed for trading
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "5Jjp6U8jJBu82xnPteQa5M42Zd5nRCrD3PkyPwWmGBGu3yQubTa")
ACTIVE_KEY = HIVE_ACTIVE_KEY
HIVE_NODES = ["https://api.hive.blog", "https://anyx.io"]
TOKEN = os.environ.get("TOKEN", "SWAP.MATIC")
TICK = 0.0000001
DELAY = 1500  # 25 minutes in seconds
MIN_OPEN_ORDERS_TARGET = 150
MAX_REFILL_ORDERS_PER_CYCLE = 20
REFILL_ORDER_QTY = 0.00000001
LOW_RC_CONSECUTIVE_COUNT = 0  # Track consecutive low RC occurrences
DYNAMIC_DELAY = DELAY  # Start with base delay
PROFIT_FLOOR = 0.002  # 0.2%
PROFIT_CEILING = 0.03  # 3%
SPREAD_MULTIPLIER = 1.5
MATIC_TINY_PRICE = 0.00000001

def get_resource_credits(account_name):
    """Return current resource credits percentage for the Hive account."""
    try:
        import requests
        url = f"https://api.hive.blog"
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

def smart_trade(account_name, token, forbidden_user=None):
    global LOW_RC_CONSECUTIVE_COUNT, DYNAMIC_DELAY
    open_count = get_total_orders_from_orderbook(account_name)
    allowed, DYNAMIC_DELAY = run_cycle_preflight(
        "[MATIC BOT]",
        account_name,
        dynamic_delay=DYNAMIC_DELAY,
        divider="==============================",
        desired_open_orders=MIN_OPEN_ORDERS_TARGET,
        current_open_orders=open_count,
    )
    if not allowed:
        return

    return run_ltc_style_logic(
        account_name=account_name,
        token=token,
        active_key=HIVE_ACTIVE_KEY,
        nodes=HIVE_NODES,
        state_file=UNIFIED_STATE_FILE,
        bot_label="MATIC BOT",
    )
    
    print("\n==============================")
    print(f"[MATIC BOT] Starting Smart Trade for {token}")
    try:
        success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
        if success:
            print("[MATIC BOT] SWAP.DOGE gas buy submitted (cycle-start).")
        else:
            print("[MATIC BOT] SWAP.DOGE gas buy skipped or failed (cycle-start).")
    except Exception as e:
        print(f"[MATIC BOT] SWAP.DOGE buy exception (cycle-start): {e}")

    rc_percent = get_resource_credits(account_name)
    if rc_percent is not None:
        print(f"[MATIC BOT] Resource Credits: {rc_percent}%")
        if rc_percent < 10.0:
            LOW_RC_CONSECUTIVE_COUNT += 1
            DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY * 2)
            print(f"[MATIC BOT] WARNING: Resource Credits too low ({rc_percent}%). Skipping trade cycle.")
            print(f"[MATIC BOT] Low RC count: {LOW_RC_CONSECUTIVE_COUNT}. Next delay: {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
            print("==============================\n")
            return
        else:
            if DYNAMIC_DELAY > DELAY:
                DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY - 120)
                if DYNAMIC_DELAY == DELAY:
                    LOW_RC_CONSECUTIVE_COUNT = 0
                print(f"[MATIC BOT] RC recovered! Reducing delay to {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
    else:
        print(f"[MATIC BOT] Resource Credits: Unable to fetch.")
    
    # CHECK TOTAL OPEN ORDERS using orderbook (more reliable than openOrders table)
    print(f"[MATIC BOT] Current total open orders (from orderbook): {open_count}")
    
    # If severely maxed out (200+), cancel multiple oldest orders
    if open_count >= 200:
        print(f"[MATIC BOT] 🚨 CRITICAL: Account maxed out with {open_count} orders! Cancelling 3 oldest orders...")
        for i in range(3):
            try:
                cancel_oldest_order(account_name, None, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                print(f"[MATIC BOT] Cancelled oldest order {i+1}/3")
                time.sleep(2)
            except Exception as e:
                print(f"[MATIC BOT] Failed to cancel order {i+1}: {e}")
        print(f"[MATIC BOT] Skipping trade cycle to allow orders to clear.")
        print("==============================\n")
        return
    
    # Keep up to 190 open orders; only cancel when exceeding that target
    if open_count > 190:
        print(f"[MATIC BOT] Open orders at {open_count}. Above 190 target, cancelling oldest {token} order to self-regulate.")
        try:
            cancel_oldest_order(account_name, token, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            time.sleep(2)
        except Exception as e:
            print(f"[MATIC BOT] Failed to cancel oldest {token} order: {e}")
    else:
        print(f"[MATIC BOT] Open orders at {open_count}. At/below 190 target, proceeding with normal trading.")
    
    market = get_orderbook_top(token)
    if not market:
        print(f"[MATIC BOT] Market fetch failed for {token}. Skipping this cycle.")
        print("==============================\n")
        # Buy smallest increment of SWAP.DOGE at fixed tiny price
        time.sleep(2)
        try:
            success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            if success:
                print("[MATIC BOT] SWAP.DOGE gas buy submitted.")
            else:
                print("[MATIC BOT] SWAP.DOGE gas buy skipped or failed.")
        except Exception as e:
            print(f"[MATIC BOT] SWAP.DOGE buy exception: {e}")
        time.sleep(2)
        return
    
    print(f"[MATIC BOT] Market fetch success for {token}.")
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))

    if open_count < MIN_OPEN_ORDERS_TARGET:
        deficit = MIN_OPEN_ORDERS_TARGET - open_count
        refill_attempts = min(deficit, MAX_REFILL_ORDERS_PER_CYCLE)
        refill_base_price = bid if bid > 0 else (ask * 0.999 if ask > 0 else 0)
        refill_placed = 0
        if refill_base_price > 0:
            print(f"[MATIC BOT] Open orders below target ({open_count}/{MIN_OPEN_ORDERS_TARGET}). Attempting {refill_attempts} refill buys.")
            for i in range(refill_attempts):
                price = round(max(TICK, refill_base_price * (1 - (0.0002 * (i + 1)))), 8)
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
                    print(f"[MATIC BOT] Refill order {i+1}/{refill_attempts} exception: {e}")
                time.sleep(0.2)
            if refill_placed > 0:
                open_count += refill_placed
                print(f"[MATIC BOT] Refill placed {refill_placed} orders. Estimated open orders now: {open_count}")
            else:
                print(f"[MATIC BOT] Refill attempts placed 0 orders this cycle.")
        else:
            print("[MATIC BOT] Refill skipped: no valid market price anchor.")

    buy_price = round(bid * 0.95, 8) if bid > 0 else 0  # Buy at 5% below highest bid
    
    # Adaptive profit floor based on recent success rate
    success_rate = get_success_rate(HIVE_ACCOUNT, token)
    adaptive_floor = adaptive_profit_floor(success_rate, base_floor=PROFIT_FLOOR, base_ceiling=PROFIT_CEILING)
    
    min_profit_percent = get_dynamic_min_profit_percent(
        bid,
        ask,
        floor=adaptive_floor,
        ceiling=PROFIT_CEILING,
        spread_multiplier=SPREAD_MULTIPLIER,
    )
    min_sell_price = round(buy_price * (1 + min_profit_percent), 8) if buy_price > 0 else 0
    sell_price = round(max(ask, min_sell_price), 8) if (ask > 0 or min_sell_price > 0) else 0
    # Guarantee profit: ensure sell > buy
    sell_price = guarantee_profit(buy_price, sell_price, min_margin=0.0001)
    sell_price = round(sell_price, 8)
    
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    matic_balance = get_balance(account_name, token)
    
    buy_qty = round(hive_balance * 0.20 / buy_price, 8) if buy_price > 0 else 0  # 20% of HIVE for buys
    sell_qty = round(matic_balance * 0.20, 8)  # 20% of MATIC for sells
    
    print(f"[MATIC BOT] Success rate: {success_rate*100:.1f}% | Adaptive floor: {adaptive_floor*100:.3f}% | Dynamic profit target: {min_profit_percent * 100:.3f}%")
    print(f"[MATIC BOT] Preparing BUY: {buy_qty} {token} at {buy_price}")
    print(f"[MATIC BOT] Preparing SELL: {sell_qty} {token} at {sell_price}")
    
    # Place BUY order
    open_orders = get_open_orders(account_name, token) or []
    print(f"[MATIC BOT] Open orders for {token}: {len(open_orders)} found")
    duplicate_buy = any(
        o.get('type') == 'buy' and float(o.get('price', 0) or 0) == buy_price
        for o in open_orders
    )
    
    if buy_qty <= 0:
        print(f"[MATIC BOT] Skipping BUY: buy_qty is zero or negative. Check HIVE balance and buy price.")
    elif duplicate_buy:
        print(f"[MATIC BOT] Skipping BUY: Duplicate buy order at {buy_price} detected.")
    else:
        try:
            place_order(account_name, token, buy_price, buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            record_buy(buy_price, buy_qty)
            print(f"[MATIC BOT] BUY order submitted: {buy_qty} {token} at {buy_price}")
            track_trade_attempt(account_name, token, filled=True)
            time.sleep(5)  # Wait for block confirmation
            open_orders = get_open_orders(account_name, token)
            if open_orders:
                print(f"[MATIC BOT] Open orders after BUY: {len(open_orders)} found.")
            else:
                print(f"[MATIC BOT] No open orders found after BUY (may be node delay).")
            time.sleep(1)
        except Exception as e:
            print(f"[MATIC BOT] BUY order exception: {e}")
    
    # Place SELL order at market-based price, only if profitable
    if sell_price > 0 and sell_qty > 0 and is_profitable_sell(sell_price):
        open_orders = get_open_orders(account_name, token) or []
        print(f"[MATIC BOT] Open orders for {token} before SELL: {len(open_orders)} found")
        duplicate_sell = any(
            o.get('type') == 'sell' and float(o.get('price', 0) or 0) == sell_price
            for o in open_orders
        )
        if duplicate_sell:
            print(f"[MATIC BOT] Skipping SELL: Duplicate sell order at {sell_price} detected.")
        else:
            try:
                place_order(account_name, token, sell_price, sell_qty, order_type="sell", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                record_sell(sell_price, sell_qty)
                print(f"[MATIC BOT] SELL order submitted: {sell_qty} {token} at {sell_price}")
                print(f"[MATIC BOT] Profit percent: {get_profit_percent(buy_price, sell_price)}%")
                track_trade_attempt(account_name, token, filled=True)
                time.sleep(5)  # Wait for block confirmation
                open_orders = get_open_orders(account_name, token)
                if open_orders:
                    print(f"[MATIC BOT] Open orders after SELL: {len(open_orders)} found.")
                else:
                    print(f"[MATIC BOT] No open orders found after SELL (may be node delay).")
                time.sleep(1)
            except Exception as e:
                print(f"[MATIC BOT] SELL order exception: {e}")
    elif sell_price > 0 and sell_qty > 0:
        print(f"[MATIC BOT] SELL order skipped: Not profitable (sell {sell_price} <= last buy {get_last_buy_price()})")
    else:
        print(f"[MATIC BOT] SELL order skipped: sell_qty is zero.")
    
    # Buy smallest increment of token at market price
    time.sleep(2)
    try:
        if ask > 0:
            place_order(account_name, token, ask, 0.00000001, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            print(f"[MATIC BOT] {token} tiny buy attempted at {ask}.")
        else:
            print(f"[MATIC BOT] {token} market ask unavailable, skipping tiny buy.")
    except Exception as e:
        print(f"[MATIC BOT] {token} tiny buy exception: {e}")
    
    # Always buy smallest increment of SWAP.DOGE at fixed tiny price
    try:
        success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
        if success:
            print("[MATIC BOT] SWAP.DOGE gas buy submitted.")
        else:
            print("[MATIC BOT] SWAP.DOGE gas buy skipped or failed.")
    except Exception as e:
        print(f"[MATIC BOT] SWAP.DOGE buy exception: {e}")
    
    # Buy PEK gas via centralized helper at end of cycle
    try:
        buy_peakecoin_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES, pek_amount=0.00000001)
        print("[MATIC BOT] PEK gas buy attempted via helper.")
    except Exception as e:
        print(f"[MATIC BOT] PEK gas buy exception: {e}")

    print(f"[MATIC BOT] Trade cycle for {token} complete.")
    print("==============================\n")

if __name__ == "__main__":
    while True:
        try:
            print(f"[MATIC BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(HIVE_ACCOUNT, TOKEN)
            print(f"[MATIC BOT] Waiting 10 seconds before next cycle...")
            time.sleep(10)
        except Exception as e:
            print(f"[MATIC BOT] Unexpected error in main loop: {e}")
        remaining = max(0, DYNAMIC_DELAY - 10)
        if remaining > 0:
            print(f"[MATIC BOT] Waiting {remaining} seconds to complete delay interval...")
            time.sleep(remaining)
