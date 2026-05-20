import time
import json as jsonlib
import requests
import os
import math
from decimal import Decimal, ROUND_UP, ROUND_DOWN
from typing import Optional
import json
from rc_intelligence import evaluate_tx_window, record_tx_outcome

_last_tx_time = 0
_TX_MIN_DELAY = 10  # seconds

ORDER_MATCH_PRICE_TOLERANCE_PCT = 0.001  # 0.1%
ORDER_MATCH_PRICE_MIN_ABS = 0.00000001
ORDER_MATCH_QTY_TOLERANCE_PCT = 0.001  # 0.1%
ORDER_MATCH_QTY_MIN_ABS = 0.00000001

MIN_SPREAD_PCT = 0.0005  # 0.05%
MIN_EXPECTED_EDGE_PCT = 0.0002  # 0.02%
MAX_SLIPPAGE_PCT = 0.01  # 1.0%
MIN_TOP_LIQUIDITY_MULTIPLIER = 1.0  # top-of-book qty must be >= 1x order size
MAX_OPEN_ORDERS_PER_TOKEN_SIDE = 2
ORDER_COOLDOWN_SECONDS = 30
REQUIRE_MAKER_ONLY = True
RELAXED_MIN_SPREAD_PCT = 0.0  # allow ultra-tight books in fallback mode
RELAXED_MIN_EXPECTED_EDGE_PCT = 0.0  # require only maker-side positive edge
TOKEN_SIDE_ORDER_LIMITS = {
    "SWAP.MATIC": 20,
}
ALLOW_DUPLICATE_TOKENS = {
    "SWAP.MATIC",
}

TRADE_STATE_FILE = os.path.join(os.path.dirname(__file__), ".trade_state.json")
ENABLE_PEK_TRADING = os.environ.get("ENABLE_PEK_TRADING", "false").lower() in ("1", "true", "yes")
PEK_TOKENS = {"PEK", "SWAP.PEK"}

ENABLE_STEEM_TRADING = os.environ.get("ENABLE_STEEM_TRADING", "false").lower() in ("1", "true", "yes")
STEEM_TOKENS = {"STEEM", "SWAP.STEEM"}

def _is_pek_disabled(token):
    return str(token or "").upper() in PEK_TOKENS and not ENABLE_PEK_TRADING


def _is_steem_disabled(token):
    return str(token or "").upper() in STEEM_TOKENS and not ENABLE_STEEM_TRADING

def _enforce_tx_delay():
    global _last_tx_time
    now = time.time()
    elapsed = now - _last_tx_time
    if elapsed < _TX_MIN_DELAY:
        time.sleep(_TX_MIN_DELAY - elapsed)
    _last_tx_time = time.time()

def _load_trade_state():
    """Load trade history from persistent state file."""
    try:
        if os.path.exists(TRADE_STATE_FILE):
            with open(TRADE_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_trade_state(state):
    """Save trade history to persistent state file."""
    try:
        with open(TRADE_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def track_trade_attempt(account_name, token, filled=False):
    """Track a trade attempt (buy or sell). filled=True if order was completed."""
    state = _load_trade_state()
    key = f"{account_name}:{token}"
    
    if key not in state:
        state[key] = {"filled": 0, "attempted": 0, "last_updated": 0}
    
    state[key]["attempted"] += 1
    if filled:
        state[key]["filled"] += 1
    state[key]["last_updated"] = int(time.time())
    
    _save_trade_state(state)

def get_success_rate(account_name, token, lookback_window=20):
    """
    Return success rate (0.0-1.0) for a token.
    Uses recent trade history (last lookback_window trades).
    """
    state = _load_trade_state()
    key = f"{account_name}:{token}"
    
    if key not in state:
        return 0.5  # Default neutral rate
    
    data = state[key]
    attempted = data.get("attempted", 0)
    filled = data.get("filled", 0)
    
    if attempted == 0:
        return 0.5
    
    # Simple moving average: filled / attempted
    rate = filled / attempted
    return min(max(rate, 0.0), 1.0)

def get_hive_instance(posting_key=None, active_key=None, nodes=None):
    from beem import Hive
    if nodes is None:
        nodes = ["https://api.hive.blog", "https://anyx.io"]
    keys = []
    if posting_key:
        keys.append(posting_key)
    if active_key:
        keys.append(active_key)
    return Hive(node=nodes, keys=keys)

def get_account_instance(account_name, hive_instance):
    from beem.account import Account
    return Account(account_name, blockchain_instance=hive_instance)

def validate_order_payload(token, quantity, price):
    if token == "SWAP.USDT":
        min_qty = 0.01
        quantity = max(float(quantity), min_qty)
        quantity = round(quantity, 2)
        price = round(float(price), 8)  # allow sub-1e-6 prices
    else:
        quantity = round(float(quantity), 8)
        price = round(float(price), 8)  # allow sub-1e-6 prices
    if token == "SWAP.USDT":
        return f"{quantity:.2f}", f"{price:.8f}"
    return f"{quantity:.8f}", f"{price:.8f}"

def get_balance(account_name, token):
    payload = {
        "jsonrpc": "2.0",
        "method": "find",
        "params": {
            "contract": "tokens",
            "table": "balances",
            "query": {"account": account_name, "symbol": token},
        },
        "id": 1,
    }
    r = requests.post("https://api.hive-engine.com/rpc/contracts", json=payload)
    if r.status_code == 200:
        result = r.json()
        if result.get("result"):
            try:
                return float(result["result"][0]["balance"])
            except Exception:
                return 0.0
    return 0.0

def get_open_orders(account_name, token=None, nodes=None, debug=False):
    if nodes is None:
        nodes = [
            "https://api.hive-engine.com/rpc/contracts",
            "https://herpc.dtools.dev",
            "https://engine.rishipanthee.com/rpc",
            "https://api2.hive-engine.com/rpc/contracts",
        ]
    all_orders = []
    for node in nodes:
        try:
            offset = 0
            page_size = 1000
            while True:
                payload = {
                    "jsonrpc": "2.0",
                    "method": "find",
                    "params": {
                        "contract": "market",
                        "table": "openOrders",
                        "query": {"account": account_name},
                        "limit": page_size,
                        "offset": offset
                    },
                    "id": 1
                }
                if token:
                    payload["params"]["query"]["symbol"] = token
                resp = requests.post(node, json=payload, timeout=10)
                if debug:
                    print(f"[place_order.py] [DEBUG] Node: {node}")
                    print(f"[place_order.py] [DEBUG] Payload: {payload}")
                    print(f"[place_order.py] [DEBUG] Status: {resp.status_code}")
                    print(f"[place_order.py] [DEBUG] Response: {resp.text[:500]}{'...' if len(resp.text) > 500 else ''}")
                if resp.status_code != 200:
                    break
                data = resp.json()
                orders = data.get('result', [])
                if not isinstance(orders, list):
                    orders = []
                all_orders.extend(orders)
                if len(orders) < page_size:
                    break
                offset += page_size
            if all_orders:
                break
        except Exception as e:
            if debug:
                print(f"[place_order.py] [DEBUG] Exception: {e}")
            continue
    return all_orders

def _get_total_open_orders(account_name):
    try:
        orders = get_open_orders(account_name)
        if orders:
            return len(orders)
    except Exception:
        orders = []
    try:
        combined = []
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
                            combined.extend(res)
                if combined:
                    break
            except Exception:
                continue
        return len(combined)
    except Exception:
        return 0

def _within_tolerance(a, b, pct_tol, min_abs):
    try:
        a_val = float(a)
        b_val = float(b)
    except Exception:
        return False
    diff = abs(a_val - b_val)
    tol = max(abs(b_val) * pct_tol, min_abs)
    return diff <= tol

def _order_matches(open_order, token, price, quantity, order_type):
    try:
        open_symbol = open_order.get("symbol")
        if open_symbol != token:
            return False
        side = open_order.get("type") or open_order.get("side")
        if side and side.lower() != order_type.lower():
            return False
        open_price = open_order.get("price") or open_order.get("tokenPrice")
        open_qty = open_order.get("quantity") or open_order.get("tokenQuantity")
        if open_price is None or open_qty is None:
            return False
        price_match = _within_tolerance(open_price, price, ORDER_MATCH_PRICE_TOLERANCE_PCT, ORDER_MATCH_PRICE_MIN_ABS)
        qty_match = _within_tolerance(open_qty, quantity, ORDER_MATCH_QTY_TOLERANCE_PCT, ORDER_MATCH_QTY_MIN_ABS)
        return price_match and qty_match
    except Exception:
        return False

def has_duplicate_open_order(account_name, token, price, quantity, order_type, nodes=None):
    try:
        orders = get_open_orders(account_name, token=token, nodes=nodes)
        for order in orders:
            if _order_matches(order, token, price, quantity, order_type):
                return True
    except Exception:
        return False
    return False

def _count_open_orders(account_name, token, order_type, nodes=None):
    count = 0
    try:
        orders = get_open_orders(account_name, token=token, nodes=nodes)
        for order in orders:
            side = order.get("type") or order.get("side")
            if side and side.lower() == order_type.lower():
                count += 1
    except Exception:
        return 0
    return count

def _get_orderbook_snapshot(token):
    try:
        from fetch_market import get_orderbook_top
        return get_orderbook_top(token)
    except Exception:
        return None

def _get_last_order_time(account_name, token, order_type):
    state = _load_trade_state()
    key = f"last_order:{account_name}:{token}:{order_type.lower()}"
    try:
        return int(state.get(key, 0))
    except Exception:
        return 0

def _set_last_order_time(account_name, token, order_type, ts):
    state = _load_trade_state()
    key = f"last_order:{account_name}:{token}:{order_type.lower()}"
    state[key] = int(ts)
    _save_trade_state(state)

def _passes_profit_filters(account_name, token, price, quantity, order_type, nodes=None):
    token_upper = (token or "").upper()
    min_spread_pct = MIN_SPREAD_PCT
    min_expected_edge_pct = MIN_EXPECTED_EDGE_PCT
    min_top_liquidity_multiplier = MIN_TOP_LIQUIDITY_MULTIPLIER
    require_maker_only = REQUIRE_MAKER_ONLY
    if token_upper in {"SWAP.HBD", "SWAP.MATIC"}:
        min_spread_pct = 0.0001  # 0.01%
        min_expected_edge_pct = 0.00005  # 0.005%
        min_top_liquidity_multiplier = 0.0
        require_maker_only = True
    elif token_upper == "SWAP.LTC":
        # LTC books can show very thin top-level qty; keep profit/maker rules,
        # but don't require top-of-book quantity to fully cover order size.
        min_spread_pct = 0.0001  # 0.01%
        min_expected_edge_pct = 0.00025  # 0.025%
        min_top_liquidity_multiplier = 0.0
        require_maker_only = True
    elif token_upper == "SWAP.BTC":
        # BTC usually has very tight spread at this price scale.
        # Keep maker-only orders and positive edge checks, but relax spread/liq gates.
        min_spread_pct = 0.000002  # 0.0002%
        min_expected_edge_pct = 0.000002  # 0.0002%
        min_top_liquidity_multiplier = 0.0
        require_maker_only = True
    elif token_upper == "SWAP.BNB":
        # BNB books are often thin at top level; retain maker/edge checks,
        # but relax spread and top-liquidity gating like other large-price tokens.
        min_spread_pct = 0.00001  # 0.001%
        min_expected_edge_pct = 0.00001  # 0.001%
        min_top_liquidity_multiplier = 0.0
        require_maker_only = True
    allow_duplicates = token_upper in ALLOW_DUPLICATE_TOKENS
    side_limit = int(TOKEN_SIDE_ORDER_LIMITS.get(token_upper, MAX_OPEN_ORDERS_PER_TOKEN_SIDE))

    if not allow_duplicates and has_duplicate_open_order(account_name, token, price, quantity, order_type, nodes=nodes):
        return False
    if _count_open_orders(account_name, token, order_type, nodes=nodes) >= side_limit:
        return False
    last_ts = _get_last_order_time(account_name, token, order_type)
    if last_ts and (time.time() - last_ts) < ORDER_COOLDOWN_SECONDS:
        return False

    snapshot = _get_orderbook_snapshot(token)
    if not snapshot:
        return False

    highest_bid = float(snapshot.get("highestBid", 0) or 0)
    lowest_ask = float(snapshot.get("lowestAsk", 0) or 0)
    highest_bid_qty = float(snapshot.get("highestBidQty", 0) or 0)
    lowest_ask_qty = float(snapshot.get("lowestAskQty", 0) or 0)

    if highest_bid <= 0 or lowest_ask <= 0 or lowest_ask <= highest_bid:
        return False

    price_val = float(price)
    qty_val = float(quantity)
    side = order_type.lower()

    def _evaluate(min_spread, min_edge, min_liq_mult, maker_only):
        spread_pct = (lowest_ask - highest_bid) / lowest_ask
        if spread_pct < min_spread:
            return False

        if maker_only:
            if side == "buy" and price_val >= lowest_ask:
                return False
            if side == "sell" and price_val <= highest_bid:
                return False

        if side == "buy":
            edge = (lowest_ask - price_val) / max(price_val, 1e-12)
            if edge < min_edge:
                return False
            if price_val > lowest_ask * (1 + MAX_SLIPPAGE_PCT):
                return False
            if lowest_ask_qty > 0 and lowest_ask_qty < (qty_val * min_liq_mult):
                return False
        else:
            edge = (price_val - highest_bid) / max(price_val, 1e-12)
            if edge < min_edge:
                return False
            if price_val < highest_bid * (1 - MAX_SLIPPAGE_PCT):
                return False
            if highest_bid_qty > 0 and highest_bid_qty < (qty_val * min_liq_mult):
                return False
        return True

    if _evaluate(min_spread_pct, min_expected_edge_pct, min_top_liquidity_multiplier, require_maker_only):
        return True

    # Global relaxed fallback: still require maker-side positive edge and sane slippage,
    # but allow non-optimal opportunities rather than skipping entire cycles.
    relaxed_pass = _evaluate(
        RELAXED_MIN_SPREAD_PCT,
        RELAXED_MIN_EXPECTED_EDGE_PCT,
        0.0,
        True,
    )
    if relaxed_pass:
        print(
            f"[ORDER] Relaxed profit filter pass: Token={token}, Price={price_val}, "
            f"Qty={qty_val}, Type={order_type}"
        )
    return relaxed_pass

def cancel_order(account_name, order_id, verbose=True, nodes=None, posting_key=None, active_key=None, side=None):
    from beem import Hive
    from beem.transactionbuilder import TransactionBuilder
    from beembase.operations import Custom_json
    import json as jsonlib
    if nodes is None:
        nodes = ["https://api.hive.blog", "https://anyx.io", "https://api.openhive.network"]
    # If order_id is a txId from orderbook, use 'id' and require 'side' (type)
    # If order_id is from openOrders, use 'orderId' (legacy)
    # Try both for compatibility
    if hasattr(order_id, 'startswith') and len(str(order_id)) == 40:
        # Looks like a txId
        resolved_side = (side or 'buy').lower()
        payload = {"contractName": "market", "contractAction": "cancel", "contractPayload": {"type": resolved_side, "id": str(order_id)}}
    else:
        payload = {"contractName": "market", "contractAction": "cancel", "contractPayload": {"orderId": str(order_id)}}
    for node in nodes:
        try:
            hive = Hive(node=node, keys=[k for k in [posting_key, active_key] if k])
            tx = TransactionBuilder(blockchain_instance=hive)
            op = Custom_json(required_auths=[account_name], required_posting_auths=[], id="ssc-mainnet-hive", json=jsonlib.dumps(payload))
            tx.appendOps([op])
            tx.appendSigner(account_name, "active")
            tx.sign()
            result = tx.broadcast()
            if isinstance(result, dict) and result.get('error'):
                continue
            if verbose:
                print(f"[OK] Cancelled order {order_id} (payload: {payload})")
            return True
        except Exception as e:
            if verbose:
                print(f"[X] Cancel exception: {e}")
            continue
    if verbose:
        print(f"[X] Cancel failed for order {order_id} (payload: {payload}) on all nodes.")
    return False

def build_and_send_op(account_name, symbol, price, quantity, order_type, posting_key=None, active_key=None, nodes=None):
    from beem.transactionbuilder import TransactionBuilder
    from beembase.operations import Custom_json
    from beem.instance import set_shared_blockchain_instance
    normalized_symbol = str(symbol).upper()
    if order_type == "buy" and normalized_symbol in {"SWAP.BCH", "BCH"}:
        print(f"[ORDER] Blocked BCH buy for {account_name}. symbol={symbol}")
        return None
    hive = get_hive_instance(posting_key, active_key, nodes)
    set_shared_blockchain_instance(hive)
    quantity, price = validate_order_payload(symbol, quantity, price)
    payload = {"contractName": "market", "contractAction": order_type, "contractPayload": {"symbol": symbol, "quantity": quantity, "price": price}}
    print(f"[ORDER] build_and_send_op payload: {payload}")
    tx = TransactionBuilder(blockchain_instance=hive)
    op = Custom_json(required_auths=[account_name], required_posting_auths=[], id="ssc-mainnet-hive", json=jsonlib.dumps(payload))
    tx.appendOps([op])
    tx.appendSigner(account_name, "active")
    try:
        tx.sign()
        result = tx.broadcast()
        print(f"[ORDER] Blockchain response: {result}")
        if isinstance(result, dict):
            if result.get('error'):
                print(f"[ORDER] Error in blockchain response: {result.get('error')}")
                return None
            if 'id' in result:
                return result.get('id')
        if isinstance(result, str) and result:
            return result
        if result:
            return str(result)
        print(f"[ORDER] No valid result returned from blockchain.")
        return None
    except Exception as e:
        print(f"[ORDER] Exception in build_and_send_op: {e}")
        return None

def place_order(account_name, token, price, quantity, order_type="buy", posting_key=None, active_key=None, nodes=None, skip_profit_filters=False):
    _enforce_tx_delay()
    if _is_pek_disabled(token):
        print(
            f"[ORDER] Blocked {order_type.upper()} {token} for {account_name}: "
            "PEK trading is disabled (set ENABLE_PEK_TRADING=true to override)."
        )
        return False
    if _is_steem_disabled(token):
        print(
            f"[ORDER] Blocked {order_type.upper()} {token} for {account_name}: "
            "STEEM trading is disabled (set ENABLE_STEEM_TRADING=true to override)."
        )
        return False
    desired_open_orders = 150
    try:
        desired_open_orders = int(os.environ.get("RC_DESIRED_OPEN_ORDERS", "150"))
    except Exception:
        desired_open_orders = 150
    current_open_orders = None
    try:
        current_open_orders = _get_total_open_orders(account_name)
    except Exception:
        current_open_orders = None
    rc_decision = evaluate_tx_window(
        account_name,
        action=order_type,
        desired_open_orders=desired_open_orders,
        current_open_orders=current_open_orders,
    )
    if not rc_decision.get("allow", True):
        print(
            f"[ORDER][RC] Skipping {order_type.upper()} {token} for {account_name}: "
            f"mode={rc_decision.get('mode')} rc={rc_decision.get('rc_percent')} "
            f"reason={rc_decision.get('reason')}"
        )
        return False
    wait_seconds = int(rc_decision.get("wait_seconds", 0) or 0)
    if wait_seconds > 0 and wait_seconds <= 10:
        time.sleep(wait_seconds)

    posting_key = posting_key or os.environ.get("HIVE_POSTING_KEY")
    active_key = active_key or os.environ.get("HIVE_ACTIVE_KEY")
    nodes = nodes or ["https://api.hive.blog", "https://anyx.io"]
    normalized_token = str(token).upper()
    if order_type == "buy" and normalized_token in {"SWAP.BCH", "BCH"}:
        print(f"[ORDER] Blocked BCH buy for {account_name}. token={token}")
        return False
    token_used = "SWAP.HIVE" if order_type == "buy" else token
    available = get_balance(account_name, token_used)
    if order_type == "buy":
        cost = float(price) * float(quantity)
        if available < cost and float(price) > 0:
            quantity = max((available * 0.95) / float(price), 0.00001)
    else:
        if available < quantity:
            quantity = max(available * 0.95, 0.00001)
    if quantity <= 0:
        print(f"[ORDER] Skipping order: quantity <= 0 after balance check. Available: {available}, price: {price}, quantity: {quantity}")
        return False
    if not skip_profit_filters and not _passes_profit_filters(account_name, token, price, quantity, order_type, nodes=nodes):
        print(f"[ORDER] Skipping order: did not pass profit filters. Token: {token}, Price: {price}, Qty: {quantity}, Type: {order_type}")
        return False
    print(f"[ORDER] Placing order: account={account_name}, token={token}, price={price}, quantity={quantity}, type={order_type}")
    tx_id = build_and_send_op(account_name, token, price, quantity, order_type, posting_key, active_key, nodes)
    if not tx_id:
        record_tx_outcome(account_name, success=False)
        print(f"[ORDER] Order failed: No tx_id returned. See above for error details.")
    else:
        record_tx_outcome(account_name, success=True)
        _set_last_order_time(account_name, token, order_type, time.time())
    return True if tx_id else False

DEFAULT_GAS_TOKEN = "SWAP.DOGE"
DEFAULT_GAS_AMOUNT = 0.01
DEFAULT_GAS_PRICE = 1.0

def buy_gas(account_name, gas_token=None, gas_amount=None, gas_price=None, posting_key=None, active_key=None, nodes=None):
    _enforce_tx_delay()
    token = gas_token if gas_token is not None else DEFAULT_GAS_TOKEN
    amount = gas_amount if gas_amount is not None else DEFAULT_GAS_AMOUNT
    price = gas_price if gas_price is not None else DEFAULT_GAS_PRICE
    return place_order(account_name, token, price, amount, order_type="buy", posting_key=posting_key, active_key=active_key, nodes=nodes)

def buy_matic_gas(account_name, posting_key=None, active_key=None, nodes=None, price=0.00001, amount=0.00001, verbose=True):
    _enforce_tx_delay()
    rc_decision = evaluate_tx_window(account_name, action="buy")
    if not rc_decision.get("allow", True):
        if verbose:
            print(
                f"[GAS][RC] Skipping gas buy for {account_name}: mode={rc_decision.get('mode')} "
                f"rc={rc_decision.get('rc_percent')} reason={rc_decision.get('reason')}"
            )
        return False

    token = "SWAP.DOGE"
    try:
        amount_val = float(amount)
    except Exception:
        return False
    if amount_val <= 0:
        return False
    market_price = 0.0
    try:
        from fetch_market import get_orderbook_top
        market = get_orderbook_top(token)
        market_price = float(market.get("lowestAsk", 0)) if market else 0.0
    except Exception:
        market_price = 0.0
    if market_price <= 0:
        try:
            price_val = float(price)
        except Exception:
            price_val = 0.0
        if price_val <= 0:
            if verbose:
                print("[GAS] SWAP.DOGE buy skipped: market ask unavailable.")
            return False
        if verbose:
            print(f"[GAS] SWAP.DOGE using fallback price {price_val:.8f} (market ask unavailable).")
    else:
        try:
            price_val = float(market_price)
        except Exception:
            return False
    if price_val <= 0:
        return False
    try:
        available = get_balance(account_name, "SWAP.HIVE")
    except Exception:
        available = 0.0
    cost = price_val * amount_val
    if available <= 0 or cost > available:
        if verbose:
            print(f"[GAS] SWAP.DOGE buy skipped: HIVE balance {available:.8f} < cost {cost:.8f}")
        return False
    q_dec = Decimal(str(amount_val)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    p_dec = Decimal(str(price_val)).quantize(Decimal("0.00000001"), rounding=ROUND_UP)
    quantity = f"{q_dec:.8f}"
    price = f"{p_dec:.8f}"
    tx_id = build_and_send_op(account_name, token, price, quantity, "buy", posting_key=posting_key, active_key=active_key, nodes=nodes)
    if tx_id:
        record_tx_outcome(account_name, success=True)
        if verbose:
            print(f"[GAS] SWAP.DOGE buy submitted tx={tx_id} price={price} qty={quantity} (market ask)")
        return True
    record_tx_outcome(account_name, success=False)
    # fallback attempt using standard place_order
    try:
        placed = place_order(account_name, token, price_val, amount_val, order_type="buy", posting_key=posting_key, active_key=active_key, nodes=nodes)
    except Exception:
        placed = False
    if verbose and not placed:
        print("[GAS] SWAP.DOGE buy failed: no tx id returned from direct or fallback order.")
    return True if placed else False

PEAKECOIN_GAS_TOKEN = "PEK"
PEAKECOIN_GAS_AMOUNT = 1
PEAKECOIN_GAS_PRICE = 0.000001
PEAKECOIN_GAS_OFFSET = 62
PEAKECOIN_GAS_INTERVAL = 3600

def buy_peakecoin_gas(account_name, posting_key=None, active_key=None, nodes=None, pek_amount: Optional[float] = None):
    _enforce_tx_delay()
    if not ENABLE_PEK_TRADING:
        print("[GAS] PEK buy skipped: PEK trading is disabled (ENABLE_PEK_TRADING=false).")
        return False
    try:
        from fetch_market import get_orderbook_top
        market = get_orderbook_top("PEK")
        ask = float(market.get("lowestAsk", 0)) if market else 0
    except Exception:
        ask = 0
    price = ask if ask > 0 else PEAKECOIN_GAS_PRICE
    if pek_amount is not None:
        try:
            amount = round(float(pek_amount), 8)
        except Exception:
            amount = 0.0
        if amount <= 0:
            print(f"[GAS] PEK buy skipped: amount {amount} <= 0")
            return False
        print(f"[GAS] Buying {amount} PEK at {price} (cost: {amount * price:.8f} HIVE)")
    else:
        MIN_SPEND_HIVE = 0.001
        try:
            calc_amount = MIN_SPEND_HIVE / price if price > 0 else PEAKECOIN_GAS_AMOUNT
        except Exception:
            calc_amount = PEAKECOIN_GAS_AMOUNT
        amount = max(PEAKECOIN_GAS_AMOUNT, round(calc_amount, 6))
        print(f"[GAS] Buying {amount} PEK at {price} (auto minimum-spend mode)")
    return place_order(account_name, PEAKECOIN_GAS_TOKEN, price, amount, order_type="buy", posting_key=posting_key, active_key=active_key, nodes=nodes)

def next_peakecoin_gas_time(start_time, interval=PEAKECOIN_GAS_INTERVAL, offset=PEAKECOIN_GAS_OFFSET):
    now = time.time()
    if now < start_time + offset:
        return start_time + offset
    cycles = math.ceil((now - (start_time + offset)) / interval)
    return start_time + offset + cycles * interval

def should_buy_peakecoin_gas(last_gas_time, interval=PEAKECOIN_GAS_INTERVAL, offset=PEAKECOIN_GAS_OFFSET):
    now = time.time()
    if last_gas_time == 0:
        return now >= (now // interval) * interval + offset
    return now - last_gas_time >= interval

def next_gas_time(start_time, interval=3600, offset=20):
    now = time.time()
    if now < start_time + offset:
        return start_time + offset
    cycles = math.ceil((now - (start_time + offset)) / interval)
    return start_time + offset + cycles * interval

def should_buy_gas(last_gas_time, interval=3600, offset=20):
    now = time.time()
    if last_gas_time == 0:
        return now >= (now // interval) * interval + offset
    return now - last_gas_time >= interval

def get_hive_posting_key():
    return os.environ.get("HIVE_POSTING_KEY")

def cancel_oldest_order(account_name, token, active_key=None, nodes=None, excluded_txids=None, force_cancel=False):
    from beem import Hive
    from beem.transactionbuilder import TransactionBuilder
    from beembase.operations import Custom_json
    import requests
    try:
        total_open = _get_total_open_orders(account_name)
        if total_open < 190 and not force_cancel:
            print(f"[i] Cancel skipped: total open orders {total_open} < 190 for {account_name}.")
            return False
    except Exception:
        pass
    try:
        payload = {
            "jsonrpc": "2.0",
            "method": "rc_api.find_rc_accounts",
            "params": {"accounts": [account_name]},
            "id": 1,
        }
        resp = requests.post("https://api.hive.blog", json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            rc = data.get("result", {}).get("rc_accounts", [{}])[0]
            if rc and "rc_manabar" in rc and "max_rc" in rc:
                current = int(rc["rc_manabar"]["current_mana"])
                max_rc = int(rc["max_rc"])
                rc_percent = round(current / max_rc * 100, 2) if max_rc > 0 else 0.0
                if rc_percent < 10.0:
                    print(f"[i] Cancel skipped: low RC {rc_percent}% for {account_name}.")
                    return False
    except Exception:
        pass
    try:
        orders = get_open_orders(account_name, token)
        if not orders:
            print(f"[i] No openOrders rows for {account_name}/{token}; falling back to orderbook...")
            combined = []
            for node in ["https://api.hive-engine.com/rpc/contracts", "https://herpc.dtools.dev", "https://engine.rishipanthee.com/rpc", "https://api2.hive-engine.com/rpc/contracts"]:
                try:
                    for table in ("buyBook", "sellBook"):
                        # Build query - only include symbol filter if token is specified
                        query = {"account": account_name}
                        if token is not None:
                            query["symbol"] = token
                        payload = {"jsonrpc": "2.0", "method": "find", "params": {"contract": "market", "table": table, "query": query, "limit": 1000}, "id": 1}
                        r = requests.post(node, json=payload, timeout=10)
                        if r.status_code == 200:
                            res = r.json().get("result", [])
                            if isinstance(res, list):
                                for o in res:
                                    o["_bookTable"] = table
                                combined.extend(res)
                    if combined:
                        break
                except Exception:
                    continue
            orders = combined
            if not orders:
                print(f"[i] No cancellable orders found in openOrders or orderbook for {account_name}/{token}")
                return False
        if excluded_txids:
            excluded = {str(x) for x in excluded_txids if x is not None}
            orders = [o for o in orders if str(o.get('txId') or '') not in excluded]
            if not orders:
                print(f"[i] No unique cancellable orders left for {account_name}/{token} after exclusions.")
                return False

        oldest = min(orders, key=lambda o: (o.get('timestamp', 10**18), o.get('id', 10**18)))
        
        # Extract txId - this is the transaction hash needed for cancel
        txid = oldest.get('txId')
        if not txid:
            print(f"[X] Cancel: order missing txId field. Order keys: {list(oldest.keys())}")
            print(f"  Order data: {oldest}")
            return False
        
        side = oldest.get('type') or oldest.get('side') or ('buy' if oldest.get('_bookTable') == 'buyBook' else ('sell' if oldest.get('_bookTable') == 'sellBook' else 'unknown'))
        qty = oldest.get('quantity') or oldest.get('tokenQuantity') or '?'
        price = oldest.get('price') or oldest.get('tokenPrice') or '?'
        ts = oldest.get('timestamp')
        print(f"[i] Cancelling oldest order txid={txid} side={side} qty={qty} price={price} ts={ts}")
        if nodes is None:
            nodes = ["https://api.hive.blog", "https://anyx.io", "https://api.openhive.network"]
        hive = Hive(keys=[active_key], node=nodes)
        payload = {"contractName": "market", "contractAction": "cancel", "contractPayload": {"type": side, "id": str(txid)}}
        tx = TransactionBuilder(blockchain_instance=hive)
        op = Custom_json(required_auths=[account_name], required_posting_auths=[], id="ssc-mainnet-hive", json=jsonlib.dumps(payload))
        tx.appendOps([op])
        tx.appendSigner(account_name, "active")
        tx.sign()
        result = tx.broadcast()
        if isinstance(result, dict) and result.get('error'):
            print(f"[X] Cancel broadcast error: {result.get('error')}")
            return False
        if excluded_txids is not None:
            try:
                excluded_txids.add(str(txid))
            except Exception:
                pass
        print(f"[OK] Cancelled order {txid} ({side} {qty} @ {price})")
        return True
    except Exception as e:
        print(f"[X] Cancel exception: {e}")
        return False

__all__ = [
    'place_order', 'get_open_orders', 'cancel_order', 'get_balance',
    'buy_gas', 'should_buy_gas', 'buy_peakecoin_gas', 'should_buy_peakecoin_gas',
    'cancel_oldest_order', 'track_trade_attempt', 'get_success_rate',
    'has_duplicate_open_order',
]
