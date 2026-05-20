def accumulate_doge_profit(account_name, active_key, nodes):
    # Use 1% of HIVE balance to buy SWAP.DOGE as profit holding
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    if hive_balance <= 0:
        print("[LTC PROFIT] No HIVE balance to accumulate DOGE.")
        return
    spend = hive_balance * 0.01
    if spend < 0.0001:
        print(f"[LTC PROFIT] Not enough HIVE to accumulate DOGE (need > 0.0001, have {spend}).")
        return
    market = get_orderbook_top("SWAP.DOGE")
    if not market:
        print("[LTC PROFIT] Could not fetch DOGE orderbook.")
        return
    ask = float(market.get("lowestAsk", 0))
    if ask <= 0:
        print("[LTC PROFIT] No valid DOGE ask price.")
        return
    qty = round(spend / ask, 8)
    if qty <= 0:
        print(f"[LTC PROFIT] Calculated DOGE qty too low: {qty}")
        return
    place_order(account_name, "SWAP.DOGE", ask, qty, order_type="buy", active_key=active_key, nodes=nodes)
    print(f"[LTC PROFIT] Accumulated {qty} SWAP.DOGE at {ask} using {spend} HIVE.")

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# Define required constants and helper functions at the top
HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "paulmoon410")
TOKEN = "SWAP.LTC"
DELAY = 60
HIVE_NODES = ["https://api.hive.blog"]
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "5HxoYQDJFZhrfjsUVy1ht3wTMqwzdSdwzSvW98kyYj1AVJ7r7Ts")
STATE_FILE = os.path.join(os.path.dirname(__file__), ".ltc_state.json")

def get_total_orders_from_orderbook(account_name):
    from place_order import _get_total_open_orders
    return _get_total_open_orders(account_name)

def get_open_orders_from_orderbook(account_name, token):
    from place_order import get_open_orders
    return get_open_orders(account_name, token)

import time
import os
import json
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, cancel_order, cancel_oldest_order, get_balance
from bot_cycle_control import run_cycle_preflight
from unified_bot_logic import run_ltc_style_logic
from strategy import StrategyState

# Persistent trade history file
TRADE_HISTORY_FILE = os.path.join(os.path.dirname(__file__), ".ltc_trade_history.json")

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

def get_resource_credits(account_name):
    """Return current resource credits percentage for the Hive account."""
    try:
        import requests
        url = f"https://api.hive.blog"
        # Placeholder for actual implementation
    except Exception:
        pass

def smart_trade(account_name, token):
    print("\n==============================")
    print(f"[LTC BOT] Starting Smart Trade for {token}")
    allowed, _ = run_cycle_preflight(
        "[LTC BOT]",
        account_name,
        dynamic_delay=None,
        divider="==============================",
    )
    if not allowed:
        return

    return run_ltc_style_logic(
        account_name=account_name,
        token=token,
        active_key=HIVE_ACTIVE_KEY,
        nodes=HIVE_NODES,
        state_file=STATE_FILE,
        bot_label="LTC BOT",
    )

    can_transact = HIVE_ACTIVE_KEY not in ("", "your_active_key", None)

    state = StrategyState(STATE_FILE)

    # Open order cap logic (event-driven cancel)
    open_orders_debug = get_open_orders_from_orderbook(account_name, token)
    print(f"[LTC BOT] Open orders for {token}: {len(open_orders_debug)}")
    while True:
        open_count = get_total_orders_from_orderbook(account_name)
        print(f"[LTC BOT] Total open orders (all tokens): {open_count}")
        if open_count >= 190:
            print(f"[LTC BOT] Open orders >= 190: cancelling 3 oldest and skipping ALL buys.")
            if not can_transact:
                print("[LTC BOT] Cancel skipped: HIVE_ACTIVE_KEY not set.")
                print("==============================\n")
                return
            cancelled_txids = set()
            for _ in range(3):
                cancelled = cancel_oldest_order(
                    account_name,
                    None,
                    active_key=HIVE_ACTIVE_KEY,
                    nodes=HIVE_NODES,
                    excluded_txids=cancelled_txids,
                    force_cancel=True,
                )
                if not cancelled:
                    break
                time.sleep(5)
            # Recheck after cancels
            continue
        break
    if open_count >= 100:
        print(f"[LTC BOT] Open orders at {open_count}. Between 100 and 189: No cancels, normal trading allowed.")
    else:
        print(f"[LTC BOT] Open orders < 100: placing new orders only.")

    # Only proceed to any buy logic if open order cap is not exceeded
    market = get_orderbook_top(token)
    if not market:
        print(f"[LTC BOT] Market fetch failed for {token}. Skipping this cycle.")
        print("==============================\n")
        return
    print(f"[LTC BOT] Market fetch success for {token}.")
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    spread = ask - bid
    print(f"[LTC BOT] Bid: {bid}, Ask: {ask}")
    # Inventory-aware quoting
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    ltc_balance = get_balance(account_name, token)
    print(f"[LTC BOT] Balances: HIVE={hive_balance:.4f}, LTC={ltc_balance:.4f}")
    token_price = ask if ask > 0 else bid
    buy_quote, sell_quote = state.get_adaptive_quote(bid, ask, spread, ltc_balance, hive_balance, token_price)

    # Aggressive market buy for SWAP.DOGE only if open order cap is not exceeded
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
                if not can_transact:
                    print("[LTC BOT] Aggressive market buy skipped: HIVE_ACTIVE_KEY not set.")
                else:
                    try:
                        place_order(account_name, "SWAP.DOGE", doge_bid, market_buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
                        print(f"[LTC BOT] Aggressive market buy: {market_buy_qty} SWAP.DOGE at {doge_bid} (1% of SWAP.HIVE)")
                    except Exception as e:
                        print(f"[LTC BOT] Aggressive market buy exception: {e}")
            else:
                print(f"[LTC BOT] Could not fetch SWAP.DOGE bid for 1% buy.")
    print(f"[LTC BOT] Quotes: BUY={buy_quote}, SELL={sell_quote}")
    buy_qty = round(hive_balance * 0.20 / buy_quote, 8) if buy_quote > 0 else 0
    sell_qty = round(ltc_balance * 0.20, 8)

    # Check for duplicate orders
    open_orders = get_open_orders(account_name, token)
    duplicate_buy = any(o.get('type') == 'buy' and float(o.get('price', 0)) == buy_quote for o in open_orders)
    duplicate_sell = any(o.get('type') == 'sell' and float(o.get('price', 0)) == sell_quote for o in open_orders)

    # Place BUY order
    if buy_qty > 0 and not duplicate_buy and can_transact:
        try:
            order_id = place_order(account_name, token, buy_quote, buy_qty, order_type="buy", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            if order_id:
                state.track_order(order_id, 'buy', buy_quote, buy_qty)
                print(f"[LTC BOT] BUY order submitted: {buy_qty} {token} at {buy_quote}")
        except Exception:
            pass
    elif buy_qty > 0 and not duplicate_buy and not can_transact:
        print("[LTC BOT] BUY skipped: HIVE_ACTIVE_KEY not set.")

    # Place SELL order
    if sell_qty > 0 and not duplicate_sell and can_transact:
        try:
            order_id = place_order(account_name, token, sell_quote, sell_qty, order_type="sell", active_key=HIVE_ACTIVE_KEY, nodes=HIVE_NODES)
            if order_id:
                state.track_order(order_id, 'sell', sell_quote, sell_qty)
                print(f"[LTC BOT] SELL order submitted: {sell_qty} {token} at {sell_quote}")
        except Exception:
            pass
    elif sell_qty > 0 and not duplicate_sell and not can_transact:
        print("[LTC BOT] SELL skipped: HIVE_ACTIVE_KEY not set.")

    print(f"[LTC BOT] Trade cycle for {token} complete.")
    print("==============================\n")


import datetime

def ltc_scalping_logic(account_name, token, active_key, nodes):
    from place_order import _get_total_open_orders
    open_count = _get_total_open_orders(account_name)
    if open_count >= 190:
        print(f"[LTC SCALP] Skipping all trading: open order count is {open_count} (>= 190). No scalp orders will be placed.")
        return
    # Fast scalping: buy/sell 0.00000001 LTC at a tight spread, only one scalp order at a time
    qty = 0.00000001
    market = get_orderbook_top(token)
    if not market:
        return
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    if ask <= 0 or bid <= 0 or ask <= bid:
        return
    buy_price = round(bid + 0.00000001, 8)
    sell_price = round(ask - 0.00000001, 8)
    if sell_price <= buy_price:
        print(f"[LTC SCALP] Skipping: no profit between buy {buy_price} and sell {sell_price}")
        return
    open_orders = get_open_orders(account_name, token)
    # Avoid self-trading: check if best ask is our own order or if scalp buy would cross our own sell
    best_ask_owner = market.get("lowestAskOwner", "")
    open_orders = get_open_orders(account_name, token)
    has_own_sell_at_buy = any(o.get('type') == 'sell' and abs(float(o.get('price', 0)) - buy_price) < 1e-12 for o in open_orders)
    if (best_ask_owner and best_ask_owner.lower() == account_name.lower()) or has_own_sell_at_buy:
        print(f"[LTC SCALP] Skipping scalp BUY: best ask is our own order or would cross our own sell.")
    else:
        # Handle buy side
        scalp_buy_orders = [o for o in open_orders if o.get('type') == 'buy' and abs(float(o.get('quantity', 0)) - qty) < 1e-12]
        if scalp_buy_orders:
            current_order = scalp_buy_orders[0]
            current_price = float(current_order.get('price', 0))
            if abs(current_price - buy_price) > 1e-12:
                # Cancel old scalp order if not at best price
                cancel_order(account_name, current_order.get('txid'), active_key=active_key, nodes=nodes)
                print(f"[LTC SCALP] Cancelled old scalp buy at {current_price}")
                place_order(account_name, token, buy_price, qty, order_type="buy", active_key=active_key, nodes=nodes)
                print(f"[LTC SCALP] BUY {qty} {token} at {buy_price}")
            else:
                print(f"[LTC SCALP] Buy order already at best price {buy_price}")
        else:
            place_order(account_name, token, buy_price, qty, order_type="buy", active_key=active_key, nodes=nodes)
            print(f"[LTC SCALP] BUY {qty} {token} at {buy_price}")

    # Avoid self-trading: check if best bid is our own order or if scalp sell would cross our own buy
    best_bid_owner = market.get("highestBidOwner", "")
    has_own_buy_at_sell = any(o.get('type') == 'buy' and abs(float(o.get('price', 0)) - sell_price) < 1e-12 for o in open_orders)
    if (best_bid_owner and best_bid_owner.lower() == account_name.lower()) or has_own_buy_at_sell:
        print(f"[LTC SCALP] Skipping scalp SELL: best bid is our own order or would cross our own buy.")
    else:
        # Handle sell side
        scalp_sell_orders = [o for o in open_orders if o.get('type') == 'sell' and abs(float(o.get('quantity', 0)) - qty) < 1e-12]
        if scalp_sell_orders:
            current_order = scalp_sell_orders[0]
            current_price = float(current_order.get('price', 0))
            if abs(current_price - sell_price) > 1e-12:
                cancel_order(account_name, current_order.get('txid'), active_key=active_key, nodes=nodes)
                print(f"[LTC SCALP] Cancelled old scalp sell at {current_price}")
                place_order(account_name, token, sell_price, qty, order_type="sell", active_key=active_key, nodes=nodes)
                print(f"[LTC SCALP] SELL {qty} {token} at {sell_price}")
            else:
                print(f"[LTC SCALP] Sell order already at best price {sell_price}")
        else:
            place_order(account_name, token, sell_price, qty, order_type="sell", active_key=active_key, nodes=nodes)
            print(f"[LTC SCALP] SELL {qty} {token} at {sell_price}")

def ltc_semi_aggressive_logic(account_name, token, active_key, nodes):
    from place_order import _get_total_open_orders
    open_count = _get_total_open_orders(account_name)
    if open_count >= 190:
        print(f"[LTC SEMI] Skipping all trading: open order count is {open_count} (>= 190). No semi-aggressive orders will be placed.")
        return
    # 10% volume, every 10 min, moderate profit
    ltc_balance = get_balance(account_name, token)
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    market = get_orderbook_top(token)
    if not market:
        return
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    if ask <= 0 or bid <= 0 or ask <= bid:
        return
    qty = round(hive_balance * 0.10 / ask, 8) if ask > 0 else 0
    sell_qty = round(ltc_balance * 0.10, 8)
    buy_price = round(bid, 8)
    sell_price = round(buy_price * 1.002, 8)  # 0.2% profit
    open_orders = get_open_orders(account_name, token)
    duplicate_buy = any(o.get('type') == 'buy' and float(o.get('price', 0)) == buy_price for o in open_orders)
    duplicate_sell = any(o.get('type') == 'sell' and float(o.get('price', 0)) == sell_price for o in open_orders)
    if qty > 0 and not duplicate_buy:
        place_order(account_name, token, buy_price, qty, order_type="buy", active_key=active_key, nodes=nodes)
        print(f"[LTC SEMI] BUY {qty} {token} at {buy_price}")
    if sell_qty > 0 and not duplicate_sell:
        place_order(account_name, token, sell_price, sell_qty, order_type="sell", active_key=active_key, nodes=nodes)
        print(f"[LTC SEMI] SELL {sell_qty} {token} at {sell_price}")

def ltc_hourly_logic(account_name, token, active_key, nodes):
    from place_order import _get_total_open_orders
    open_count = _get_total_open_orders(account_name)
    if open_count >= 190:
        print(f"[LTC HOUR] Skipping all trading: open order count is {open_count} (>= 190). No hourly orders will be placed.")
        return
    # Large trade, higher profit, every hour, 5% volume, 10% profit margins
    ltc_balance = get_balance(account_name, token)
    hive_balance = get_balance(account_name, "SWAP.HIVE")
    market = get_orderbook_top(token)
    if not market:
        return
    bid = float(market.get("highestBid", 0))
    ask = float(market.get("lowestAsk", 0))
    if ask <= 0 or bid <= 0 or ask <= bid:
        return
    qty = round(hive_balance * 0.05 / ask, 8) if ask > 0 else 0
    sell_qty = round(ltc_balance * 0.05, 8)
    buy_price = round(bid * 0.90, 8)  # 10% below market bid
    sell_price = round(ask * 1.10, 8)  # 10% above market ask
    open_orders = get_open_orders(account_name, token)
    duplicate_buy = any(o.get('type') == 'buy' and float(o.get('price', 0)) == buy_price for o in open_orders)
    duplicate_sell = any(o.get('type') == 'sell' and float(o.get('price', 0)) == sell_price for o in open_orders)
    if qty > 0 and not duplicate_buy:
        place_order(account_name, token, buy_price, qty, order_type="buy", active_key=active_key, nodes=nodes)
        print(f"[LTC HOUR] BUY {qty} {token} at {buy_price}")
    if sell_qty > 0 and not duplicate_sell:
        place_order(account_name, token, sell_price, sell_qty, order_type="sell", active_key=active_key, nodes=nodes)
        print(f"[LTC HOUR] SELL {sell_qty} {token} at {sell_price}")

    # Accumulate DOGE profit holding each time the large trade logic runs
    accumulate_doge_profit(account_name, active_key, nodes)


def _fetch_combined_open_orders_for_account(account_name):
    """Fetch open orders from both openOrders and orderbook tables, then de-duplicate by txId."""
    from place_order import get_open_orders
    import requests

    combined = []
    try:
        open_orders_rows = get_open_orders(account_name, None)
        if isinstance(open_orders_rows, list):
            combined.extend(open_orders_rows)
    except Exception:
        pass

    for node in [
        "https://api.hive-engine.com/rpc/contracts",
        "https://herpc.dtools.dev",
        "https://engine.rishipanthee.com/rpc",
        "https://api2.hive-engine.com/rpc/contracts",
    ]:
        try:
            for table in ("buyBook", "sellBook"):
                payload = {
                    "jsonrpc": "2.0",
                    "method": "find",
                    "params": {
                        "contract": "market",
                        "table": table,
                        "query": {"account": account_name},
                        "limit": 1000,
                    },
                    "id": 1,
                }
                r = requests.post(node, json=payload, timeout=10)
                if r.status_code == 200:
                    res = r.json().get("result", [])
                    if isinstance(res, list):
                        for o in res:
                            o["_bookTable"] = table
                        combined.extend(res)
            # First successful node with any rows is enough.
            if combined:
                break
        except Exception:
            continue

    deduped_by_txid = {}
    for order in combined:
        txid = order.get("txId") or order.get("txid")
        if txid:
            order["txId"] = txid
            deduped_by_txid[str(txid)] = order

    return list(deduped_by_txid.values())


def _stable_total_open_orders(account_name, samples=3, delay_seconds=1.0):
    """Sample total open orders a few times and use the minimum to smooth RPC lag."""
    from place_order import _get_total_open_orders

    counts = []
    for i in range(max(1, int(samples))):
        try:
            counts.append(int(_get_total_open_orders(account_name)))
        except Exception:
            pass
        if i < samples - 1:
            time.sleep(delay_seconds)
    if not counts:
        return 0, []
    return min(counts), counts

if __name__ == "__main__":
    print(f"[LTC BOT] Account source: {HIVE_ACCOUNT}")
    print(f"[LTC BOT] Active key set: {HIVE_ACTIVE_KEY not in ('', 'your_active_key', None)}")
    while True:
        try:
            print(f"[LTC BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(HIVE_ACCOUNT, TOKEN)
            print(f"[BOT] Waiting {DELAY} seconds before next cycle...")
            time.sleep(DELAY)
        except KeyboardInterrupt:
            print("[LTC BOT] Stopped by user.")
            break
        except Exception as e:
            print(f"[LTC BOT] Exception: {e}")
            time.sleep(DELAY)
