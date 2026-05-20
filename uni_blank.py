from profit_strategies import get_profit_percent
import time
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, get_balance, buy_peakecoin_gas


import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# Hive account details (fill in before use)
HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "<YOUR_USERNAME>")  # <-- Fill in your Hive username
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "<YOUR_ACTIVE_KEY>")  # <-- Fill in your Hive active key
HIVE_NODES = ["https://api.hive.blog", "https://anyx.io"]
TOKEN = "SWAP.TOKEN"  # <-- Fill in your token symbol, e.g., SWAP.BTC
DELAY = 1500
LOW_RC_CONSECUTIVE_COUNT = 0  # Track consecutive low RC occurrences
DYNAMIC_DELAY = DELAY  # Start with base delay

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

def smart_trade(account_name, token):
    global LOW_RC_CONSECUTIVE_COUNT, DYNAMIC_DELAY
    print("\n==============================")
    print(f"[TOKEN BOT] Starting Smart Trade for {token}")
    rc_percent = get_resource_credits(account_name)
    if rc_percent is not None:
        print(f"[TOKEN BOT] Resource Credits: {rc_percent}%")
        if rc_percent < 10.0:
            LOW_RC_CONSECUTIVE_COUNT += 1
            DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY * 2)
            print(f"[TOKEN BOT] WARNING: Resource Credits too low ({rc_percent}%). Skipping trade cycle.")
            print(f"[TOKEN BOT] Low RC count: {LOW_RC_CONSECUTIVE_COUNT}. Next delay: {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
            print("==============================\n")
            return
        else:
            if DYNAMIC_DELAY > DELAY:
                DYNAMIC_DELAY = max(DELAY, DYNAMIC_DELAY - 120)
                if DYNAMIC_DELAY == DELAY:
                    LOW_RC_CONSECUTIVE_COUNT = 0
                print(f"[TOKEN BOT] RC recovered! Reducing delay to {DYNAMIC_DELAY}s ({DYNAMIC_DELAY/60:.1f} min)")
    else:
        print(f"[TOKEN BOT] Resource Credits: Unable to fetch.")

    # ...existing code...
    # ...existing code...
    print(f"[TOKEN BOT] Trade cycle for {token} complete.")
    print("==============================\n")
    time.sleep(2)
    # Buy PEK gas via centralized helper at end of cycle
    try:
        buy_peakecoin_gas(account_name, active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES, pek_amount=0.00000001)
        print(f"[TOKEN BOT] Gas buy attempted via helper.")
    except Exception as e:
        print(f"[TOKEN BOT] PEK gas buy exception: {e}")
    time.sleep(2)

    market = get_orderbook_top(token)
    if not market:
        print(f"[TOKEN BOT] Market fetch failed for {token}. Skipping this cycle.")
        print("==============================\n")
        return
    print(f"[TOKEN BOT] Market fetch success for {token}.")
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    buy_price = round(bid * 0.98, 8) if bid > 0 else 0  # Buy at 2% below highest bid
    sell_price = round(buy_price * 1.05, 8) if buy_price > 0 else 0  # Sell at 5% above buy price
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    token_balance = get_balance(account_name, token)
    buy_qty = round(hive_balance * 0.20 / buy_price, 8) if buy_price > 0 else 0
    sell_qty = round(token_balance * 0.20, 8)
    print(f"[TOKEN BOT] Preparing BUY: {buy_qty} {token} at {buy_price}")
    print(f"[TOKEN BOT] Preparing SELL: {sell_qty} {token} at {sell_price}")
    open_orders = get_open_orders(account_name, token)
    duplicate_buy = any(o.get('type') == 'buy' and float(o.get('price', 0)) == buy_price for o in open_orders)
    if buy_qty <= 0:
        print(f"[TOKEN BOT] Skipping BUY: buy_qty is zero or negative. Check HIVE balance and buy price.")
    elif duplicate_buy:
        print(f"[TOKEN BOT] Skipping BUY: Duplicate buy order at {buy_price} detected.")
    else:
        try:
            place_order(account_name, token, buy_price, buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            print(f"[TOKEN BOT] BUY order submitted: {buy_qty} {token} at {buy_price}")
            time.sleep(5)
            open_orders = get_open_orders(account_name, token)
            if open_orders:
                print(f"[TOKEN BOT] Open orders after BUY: {len(open_orders)} found.")
            else:
                print(f"[TOKEN BOT] No open orders found after BUY (may be node delay).")
            time.sleep(1)
        except Exception as e:
            print(f"[TOKEN BOT] BUY order exception: {e}")
    min_profit_percent = 0.05
    min_sell_price = round(buy_price * (1 + min_profit_percent), 8)
    force_sell_price = min_sell_price  # Always enforce profit, ignore market ask
    if force_sell_price > buy_price and sell_qty > 0:
        open_orders = get_open_orders(account_name, token)
        duplicate_sell = any(o.get('type') == 'sell' and float(o.get('price', 0)) == force_sell_price for o in open_orders)
        if duplicate_sell:
            print(f"[TOKEN BOT] Skipping SELL: Duplicate sell order at {force_sell_price} detected.")
        else:
            try:
                place_order(account_name, token, force_sell_price, sell_qty, order_type="sell", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                print(f"[TOKEN BOT] SELL order submitted: {sell_qty} {token} at {force_sell_price}")
                print(f"[TOKEN BOT] Profit percent: {get_profit_percent(buy_price, force_sell_price)}%")
                time.sleep(5)
                open_orders = get_open_orders(account_name, token)
                if open_orders:
                    print(f"[TOKEN BOT] Open orders after SELL: {len(open_orders)} found.")
                else:
                    print(f"[TOKEN BOT] No open orders found after SELL (may be node delay).")
                time.sleep(1)
            except Exception as e:
                print(f"[TOKEN BOT] SELL order exception: {e}")
    else:
        print(f"[TOKEN BOT] SELL order skipped: Not profitable or sell_qty is zero.")
    print(f"[TOKEN BOT] Trade cycle for {token} complete.")
    print("==============================\n")
    time.sleep(DYNAMIC_DELAY)

if __name__ == "__main__":
    while True:
        try:
            print(f"[TOKEN BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(HIVE_ACCOUNT, TOKEN)
            print(f"[TOKEN BOT] Waiting 10 seconds before next cycle...")
            time.sleep(10)
        except Exception as e:
            print(f"[TOKEN BOT] Unexpected error in main loop: {e}")
            import traceback
            traceback.print_exc()
        remaining = max(0, DELAY - 10)
        if remaining > 0:
            print(f"[TOKEN BOT] Waiting {remaining} seconds to complete delay interval...")
            time.sleep(remaining)
