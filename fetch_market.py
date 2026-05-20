import requests

ENGINE_NODES = [
    "https://api.hive-engine.com/rpc/contracts",
    "https://herpc.dtools.dev",
    "https://engine.rishipanthee.com/rpc",
    "https://api2.hive-engine.com/rpc/contracts",
]
REQUEST_TIMEOUT_SECONDS = 12
MAX_FETCH_ATTEMPTS = 2


def _post_engine_payload(payload, token):
    last_error = None
    for _ in range(MAX_FETCH_ATTEMPTS):
        for node in ENGINE_NODES:
            try:
                response = requests.post(node, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                last_error = f"request exception via {node}: {exc}"
                continue

            if response.status_code != 200:
                last_error = f"http {response.status_code} via {node}"
                continue

            try:
                data = response.json()
            except ValueError as exc:
                last_error = f"json parse error via {node}: {exc}"
                continue

            result = data.get("result", [])
            if isinstance(result, list):
                return result

            last_error = f"unexpected result shape via {node}"

    if last_error:
        print(f"[MARKET] Fetch failed for {token}: {last_error}")
    return None

def _extract_qty(order):
    for key in ("quantity", "tokenQuantity", "symbolQuantity"):
        if key in order:
            try:
                return float(order[key])
            except Exception:
                continue
    return 0.0

def get_orderbook_top(token="SWAP.LTC"):
    # Pull top buy orders (usually works correctly with sorting)
    buy_payload = {
        "jsonrpc": "2.0",
        "method": "find",
        "params": {
            "contract": "market",
            "table": "buyBook",
            "query": {"symbol": token},
            "limit": 1000,
            "indexes": [{"index": "priceDec", "descending": True}]
        },
        "id": 1
    }

    # Pull up to 1000 sell orders to ensure we capture the true lowest ask
    sell_payload = {
        "jsonrpc": "2.0",
        "method": "find",
        "params": {
            "contract": "market",
            "table": "sellBook",
            "query": {"symbol": token},
            "limit": 1000,
            "indexes": [{"index": "price", "descending": False}]
        },
        "id": 2
    }

    buy_result = _post_engine_payload(buy_payload, token)
    sell_result = _post_engine_payload(sell_payload, token)
    if buy_result is None or sell_result is None:
        return None

    # Use the highest priced buy order (top bid)
    highest_bid = float(buy_result[0]["price"]) if buy_result else 0
    highest_bid_qty = _extract_qty(buy_result[0]) if buy_result else 0.0

    # Use the true lowest sell price found in the result
    valid_asks = [order for order in sell_result if float(order.get("price", 0)) > 0]
    if valid_asks:
        lowest_ask_order = min(valid_asks, key=lambda o: float(o["price"]))
        lowest_ask = float(lowest_ask_order["price"])
        lowest_ask_qty = _extract_qty(lowest_ask_order)
    else:
        lowest_ask = 0
        lowest_ask_qty = 0.0

    return {
        "highestBid": highest_bid,
        "highestBidQty": highest_bid_qty,
        "lowestAsk": lowest_ask,
        "lowestAskQty": lowest_ask_qty,
    }

def get_account_open_orders(account, limit=1000):
    """
    Fetch all open orders for the given account (across all tokens), paginated if needed.
    Returns a list of all open orders.
    """
    url = "https://api.hive-engine.com/rpc/contracts"
    all_orders = []
    offset = 0
    page_size = limit
    while True:
        payload = {
            "jsonrpc": "2.0",
            "method": "find",
            "params": {
                "contract": "market",
                "table": "openOrders",
                "query": {"account": account},
                "limit": page_size,
                "offset": offset
            },
            "id": 1
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[ERROR] Failed to fetch open orders for {account} (status {resp.status_code})")
            break
        data = resp.json()
        orders = data.get('result')
        if not isinstance(orders, list):
            orders = []
        all_orders.extend(orders)
        if len(orders) < page_size:
            break
        offset += page_size
    return all_orders

def get_account_open_orders_all_tokens(account, limit=1000):
    """
    Fetch all open orders for the given account (across all tokens), paginated if needed.
    Returns a list of all open orders (all tokens).
    """
    url = "https://api.hive-engine.com/rpc/contracts"
    all_orders = []
    offset = 0
    page_size = limit
    while True:
        payload = {
            "jsonrpc": "2.0",
            "method": "find",
            "params": {
                "contract": "market",
                "table": "openOrders",
                "query": {"account": account},
                "limit": page_size,
                "offset": offset
            },
            "id": 1
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[ERROR] Failed to fetch open orders for {account} (status {resp.status_code})")
            break
        data = resp.json()
        orders = data.get('result')
        if not isinstance(orders, list):
            orders = []
        all_orders.extend(orders)
        if len(orders) < page_size:
            break
        offset += page_size
    return all_orders

class MarketFetcher:
    def get_top_of_book(self, token):
        return get_orderbook_top(token)

    def get_open_orders(self, account, token):
        all_orders = get_account_open_orders(account)
        return [o for o in all_orders if o.get('symbol') == token]
