import os
import time

from bot_cycle_control import run_cycle_preflight
from unified_bot_logic import run_ltc_style_logic

HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "strangedad")
TOKEN = "SWAP.BTC"
DELAY = 60

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
HIVE_NODES = ["https://api.hive.blog"]
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "5JoBxPVFouxr39B7KFKmZA2CPfCHJk7kEHRPzExTpM8iQdgNCTm")
STATE_FILE = os.path.join(os.path.dirname(__file__), ".btc_state.json")


def smart_trade(account_name, token):
    print("\n==============================")
    print(f"[BTC BOT] Starting Smart Trade for {token}")
    allowed, _ = run_cycle_preflight(
        "[BTC BOT]",
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
        bot_label="BTC BOT",
    )


if __name__ == "__main__":
    print(f"[BTC BOT] Account source: {HIVE_ACCOUNT}")
    print(f"[BTC BOT] Active key set: {HIVE_ACTIVE_KEY not in ('', 'your_active_key', None)}")
    while True:
        try:
            print(f"[BTC BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(HIVE_ACCOUNT, TOKEN)
            print(f"[BOT] Waiting {DELAY} seconds before next cycle...")
            time.sleep(DELAY)
        except KeyboardInterrupt:
            print("[BTC BOT] Stopped by user.")
            break
        except Exception as e:
            print(f"[BTC BOT] Exception: {e}")
            time.sleep(DELAY)
