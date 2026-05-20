from profit_strategies import get_profit_percent, get_dynamic_min_profit_percent, adaptive_profit_floor, guarantee_profit
import time
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, get_balance, buy_peakecoin_gas, cancel_oldest_order, track_trade_attempt, get_success_rate, buy_matic_gas


import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# Hive account details
HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "peakecoin")
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "5JmdCunm6rED9u2XWgGXbthdz3VFdkeALjFyfZaS78K7havZ5kG")
HIVE_NODES = ["https://api.hive.blog", "https://anyx.io"]
TOKEN = "SWAP.USDT"
DELAY = 1500
LOW_RC_CONSECUTIVE_COUNT = 0  # Track consecutive low RC occurrences
DYNAMIC_DELAY = DELAY  # Start with base delay
PROFIT_FLOOR = 0.0015  # 0.15%
PROFIT_CEILING = 0.02  # 2%
SPREAD_MULTIPLIER = 1.2
MATIC_TINY_PRICE = 0.00000001

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
                    print(f"[TETHER BOT] Aggressive market buy: {market_buy_qty} SWAP.DOGE at {doge_bid} (1% of SWAP.HIVE)")
                except Exception as e:
                    print(f"[TETHER BOT] Aggressive market buy exception: {e}")
            else:
                print(f"[TETHER BOT] Could not fetch SWAP.DOGE bid for 1% buy.")
    print("\n==============================")
    print(f"[USDT BOT] Starting Smart Trade for {token}")
    try:
        success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
        if success:
            print("[USDT BOT] SWAP.DOGE gas buy submitted (cycle-start).")
        else:
            print("[USDT BOT] SWAP.DOGE gas buy skipped or failed (cycle-start).")
    except Exception as e:
        print(f"[USDT BOT] SWAP.DOGE buy exception (cycle-start): {e}")

    rc_percent = get_resource_credits(account_name)
    if rc_percent is not None:
        print(f"[USDT BOT] Resource Credits: {rc_percent}%")
        if rc_percent < 10.0:
            LOW_RC_CONSECUTIVE_COUNT += 1
            DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY * 2)
            print(f"[USDT BOT] WARNING: Resource Credits too low ({rc_percent}%). Skipping trade cycle.")
            print(f"[USDT BOT] Low RC count: {LOW_RC_CONSECUTIVE_COUNT}. Next delay: {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
            print("==============================\n")
            return
        else:
            if DYNAMIC_DELAY > DELAY:
                DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY - 120)
                if DYNAMIC_DELAY == DELAY:
                    LOW_RC_CONSECUTIVE_COUNT = 0
                print(f"[USDT BOT] RC recovered! Reducing delay to {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
    else:
        print(f"[USDT BOT] Resource Credits: Unable to fetch.")
    
    # CHECK TOTAL OPEN ORDERS using orderbook (more reliable than openOrders table)
    open_count = get_total_orders_from_orderbook(account_name)
    print(f"[USDT BOT] Current total open orders (from orderbook): {open_count}")
    
    # If severely maxed out (200+), cancel multiple oldest orders
    if open_count >= 200:
        print(f"[USDT BOT] 🚨 CRITICAL: Account maxed out with {open_count} orders! Cancelling 3 oldest orders...")
        for i in range(3):
            try:
                cancel_oldest_order(account_name, None, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                print(f"[USDT BOT] Cancelled oldest order {i+1}/3")
                time.sleep(2)
            except Exception as e:
                print(f"[USDT BOT] Failed to cancel order {i+1}: {e}")
        print(f"[USDT BOT] Skipping trade cycle to allow orders to clear.")
        print("==============================\n")
        return
    
    # If approaching limit, cancel one oldest order for THIS token, but continue trading
    if open_count >= 190:
        print(f"[USDT BOT] Open orders at {open_count}. Cancelling oldest {token} order to self-regulate.")
        try:
            cancel_oldest_order(account_name, token, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            time.sleep(2)
        except Exception as e:
            print(f"[USDT BOT] Failed to cancel oldest {token} order: {e}")
    else:
        print(f"[USDT BOT] Open orders at {open_count}. Below 190 threshold, proceeding with normal trading.")
    
    market = get_orderbook_top(token)
    if not market:
        print(f"[USDT BOT] Market fetch failed for {token}. Skipping this cycle.")
        print("==============================\n")
        return
    print(f"[USDT BOT] Market fetch success for {token}.")
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    buy_price = round(bid * 0.98, 6) if bid > 0 else 0  # Buy at 2% below highest bid
    
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
    min_sell_price = round(buy_price * (1 + min_profit_percent), 6) if buy_price > 0 else 0
    sell_price = round(max(ask, min_sell_price), 6) if (ask > 0 or min_sell_price > 0) else 0
    # Guarantee profit: ensure sell > buy
    sell_price = guarantee_profit(buy_price, sell_price, min_margin=0.0001)
    sell_price = round(sell_price, 6)
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    usdt_balance = get_balance(account_name, token)
    print(f"[USDT BOT] HIVE balance: {hive_balance}")
    print(f"[USDT BOT] USDT balance: {usdt_balance}")
    print(f"[USDT BOT] Buy price: {buy_price}")
    print(f"[USDT BOT] Success rate: {success_rate*100:.1f}% | Adaptive floor: {adaptive_floor*100:.3f}% | Dynamic profit target: {min_profit_percent * 100:.3f}%")
    buy_qty = round(hive_balance * 0.20 / buy_price, 6) if buy_price > 0 else 0
    sell_qty = round(usdt_balance * 0.30, 6)
    
    print(f"[USDT BOT] Preparing BUY: {buy_qty} {token} at {buy_price}")
    print(f"[USDT BOT] Preparing SELL: {sell_qty} {token} at {sell_price}")
    open_orders = get_open_orders(account_name, token)
    duplicate_buy = any(o.get('type') == 'buy' and float(o.get('price', 0)) == buy_price for o in open_orders)
    if buy_qty <= 0:
        print(f"[USDT BOT] Skipping BUY: buy_qty is zero or negative. Check HIVE balance and buy price.")
    elif duplicate_buy:
        print(f"[USDT BOT] Skipping BUY: Duplicate buy order at {buy_price} detected.")
    else:
        try:
            place_order(account_name, token, buy_price, buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            print(f"[USDT BOT] BUY order submitted: {buy_qty} {token} at {buy_price}")
            track_trade_attempt(account_name, token, filled=True)
            time.sleep(5)
            open_orders = get_open_orders(account_name, token)
            if open_orders:
                print(f"[USDT BOT] Open orders after BUY: {len(open_orders)} found.")
            else:
                print(f"[USDT BOT] No open orders found after BUY (may be node delay).")
            time.sleep(1)
        except Exception as e:
            print(f"[USDT BOT] BUY order exception: {e}")
    min_sell_price = round(buy_price * (1 + min_profit_percent), 6)
    force_sell_price = min_sell_price  # Always enforce profit, ignore market ask
    if sell_qty > 0:
        open_orders = get_open_orders(account_name, token)
        duplicate_sell = any(o.get('type') == 'sell' and float(o.get('price', 0)) == force_sell_price for o in open_orders)
        if duplicate_sell:
            print(f"[USDT BOT] Skipping SELL: Duplicate sell order at {force_sell_price} detected.")
        elif force_sell_price > buy_price:
            try:
                place_order(account_name, token, force_sell_price, sell_qty, order_type="sell", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                print(f"[USDT BOT] SELL order submitted: {sell_qty} {token} at {force_sell_price}")
                print(f"[USDT BOT] Profit percent: {get_profit_percent(buy_price, force_sell_price)}%")
                track_trade_attempt(account_name, token, filled=True)
                time.sleep(5)
                open_orders = get_open_orders(account_name, token)
                if open_orders:
                    print(f"[USDT BOT] Open orders after SELL: {len(open_orders)} found.")
                else:
                    print(f"[USDT BOT] No open orders found after SELL (may be node delay).")
                time.sleep(1)
            except Exception as e:
                print(f"[USDT BOT] SELL order exception: {e}")
        else:
            print(f"[USDT BOT] SELL order skipped: Not profitable (sell price <= buy price).")
    else:
        print(f"[USDT BOT] SELL order skipped: sell_qty is zero.")
    
    # Removed tiny buy of the bot's own token at market price
    
    # Always buy smallest increment of SWAP.DOGE at fixed tiny price
    try:
        success = buy_matic_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
        if success:
            print("[USDT BOT] SWAP.DOGE gas buy submitted.")
        else:
            print("[USDT BOT] SWAP.DOGE gas buy skipped or failed.")
    except Exception as e:
        print(f"[USDT BOT] SWAP.DOGE buy exception: {e}")
    
    # Always buy a tiny amount of PEK at the end of each cycle
    try:
        buy_peakecoin_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES, pek_amount=0.00000001)
        print("[USDT BOT] PEK gas buy attempted: 0.00000001 PEK")
    except Exception as e:
        print(f"[USDT BOT] PEK gas buy exception: {e}")

    print(f"[USDT BOT] Trade cycle for {token} complete.")
    print("==============================\n")

if __name__ == "__main__":
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
