
import os
import time
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from bot_cycle_control import run_cycle_preflight
from unified_bot_logic import run_ltc_style_logic

HIVE_ACCOUNT = os.environ.get("HIVE_ACCOUNT", "peakecoin")
TOKEN = "SWAP.PEK"
DELAY = 60
HIVE_NODES = ["https://api.hive.blog"]
HIVE_ACTIVE_KEY = os.environ.get("HIVE_ACTIVE_KEY", "Your Active Key Here")
STATE_FILE = os.path.join(os.path.dirname(__file__), ".pek_state.json")
ENABLE_PEK_BOT = os.environ.get("ENABLE_PEK_BOT", "false").lower() in ("1", "true", "yes")


def smart_trade(account_name, token):
    if not ENABLE_PEK_BOT:
        print("[PEK BOT] Disabled by config (ENABLE_PEK_BOT=false). Skipping cycle.")
        return

    print("\n==============================")
    print(f"[PEK BOT] Starting Smart Trade for {token}")
    allowed, _ = run_cycle_preflight(
        "[PEK BOT]",
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
        bot_label="PEK BOT",
    )


if __name__ == "__main__":
    if not ENABLE_PEK_BOT:
        print("[PEK BOT] Disabled by config (ENABLE_PEK_BOT=false). Exiting.")
        raise SystemExit(0)

    print(f"[PEK BOT] Account source: {HIVE_ACCOUNT}")
    print(f"[PEK BOT] Active key set: {HIVE_ACTIVE_KEY not in ('', 'your_active_key', None, 'Your Active Key Here')}")
    while True:
        try:
            print(f"[PEK BOT] Starting new trade cycle for {TOKEN}.")
            smart_trade(HIVE_ACCOUNT, TOKEN)
            print(f"[BOT] Waiting {DELAY} seconds before next cycle...")
            time.sleep(DELAY)
        except KeyboardInterrupt:
            print("[PEK BOT] Stopped by user.")
            break
        except Exception as e:
            print(f"[PEK BOT] Exception: {e}")
            time.sleep(DELAY)
