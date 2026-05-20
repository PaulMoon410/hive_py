from profit_strategies import is_profitable_or_volume_increase, get_profit_percent, get_dynamic_min_profit_percent, adaptive_profit_floor, guarantee_profit
import time
import datetime
import json
import os
from bot_cycle_control import run_cycle_preflight
from unified_bot_logic import run_ltc_style_logic
from fetch_market import get_orderbook_top
from place_order import place_order, get_open_orders, cancel_order, get_balance, buy_peakecoin_gas, cancel_oldest_order, track_trade_attempt, get_success_rate, buy_matic_gas

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
# 🔐 Hive account
ACCOUNT = os.environ.get("HIVE_ACCOUNT_DOGE", "mmoonn")
ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY_DOGE", "5JmKAq839tgpG1ESz3ZK7jSe1Y22mbwNvtBsXJ3YT7RCoXFcFKh")
HIVE_NODES = ["https://api.hive.blog", "https://anyx.io", "https://api.openhive.network"]
TOKEN = "SWAP.DOGE"
TICK = 0.0000001
DELAY = 1500  # 25 minutes in seconds
LOW_RC_CONSECUTIVE_COUNT = 0  # Track consecutive low RC occurrences
DYNAMIC_DELAY = DELAY  # Start with base delay
PROFIT_FLOOR = 0.001  # 0.1% (more aggressive)
PROFIT_CEILING = 0.03  # 3%
SPREAD_MULTIPLIER = 1.5
UNIFIED_STATE_FILE = os.path.join(os.path.dirname(__file__), ".doge_state_unified.json")

# Accounts operated by partner bots whose DOGE purchases we want to sell into
PARTNER_ACCOUNTS = ["paulmoon410", "dr-animation", "strangedad", "peakecoin", "peakecoin.bnb", "peakecoin.matic"]

def get_partner_doge_buy_prices(token="SWAP.DOGE"):
    """Query the DEX buyBook for open buy orders placed by partner accounts on SWAP.DOGE.
    Returns the highest buy price found across all partner accounts, or 0 if none found."""
    import requests
    nodes = ["https://api.hive-engine.com/rpc/contracts", "https://herpc.dtools.dev", "https://engine.rishipanthee.com/rpc"]
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
                    "limit": 1000
                },
                "id": 1
            }
            r = requests.post(node, json=payload, timeout=10)
            if r.status_code == 200:
                orders = r.json().get("result", [])
                if isinstance(orders, list):
                    for o in orders:
                        if o.get("account") in PARTNER_ACCOUNTS:
                            try:
                                p = float(o.get("price", 0))
                                if p > highest:
                                    highest = p
                            except Exception:
                                pass
                    return highest  # first successful node response is enough
        except Exception:
            continue
    return highest

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

def smart_trade(account_name, token):
    global LOW_RC_CONSECUTIVE_COUNT, DYNAMIC_DELAY
    allowed, DYNAMIC_DELAY = run_cycle_preflight(
        "[DOGE] [DOGE BOT]",
        account_name,
        dynamic_delay=DYNAMIC_DELAY,
        divider="[DOGE] ==============================",
    )
    if not allowed:
        return

    # Call unified LTC-style logic which handles DOGE coordination with feeder accounts
    run_ltc_style_logic(
        account_name=account_name,
        token=token,
        active_key=ACTIVE_KEY,
        nodes=HIVE_NODES,
        state_file=UNIFIED_STATE_FILE,
        bot_label="DOGE BOT",
    )
    print("[DOGE] ==============================\n")

if __name__ == "__main__":
    if os.environ.get("ENABLE_STANDALONE_DOGE", "false").lower() not in ("1", "true", "yes"):
        print("[DOGE] [BOT] Standalone DOGE loop disabled. DOGE execution is feeder-triggered from other bots.")
        raise SystemExit(0)
    while True:
        try:
            print(f"[DOGE] [BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(ACCOUNT, TOKEN)
            print(f"[DOGE] [BOT] Waiting 10 seconds before next cycle...")
            time.sleep(10)
        except Exception as e:
            print(f"[DOGE] [BOT] Unexpected error in main loop: {e}")
            import traceback
            traceback.print_exc()
        remaining = max(0, DYNAMIC_DELAY - 10)
        if remaining > 0:
            print(f"[DOGE] [BOT] Waiting {remaining} seconds to complete delay interval...")
            time.sleep(remaining)
