import time
from rc_intelligence import evaluate_tx_window

RC_FAIL_COOLDOWN_SECONDS = 3600


def run_cycle_preflight(
    bot_label,
    account_name,
    dynamic_delay=None,
    action="order",
    divider="==============================",
    desired_open_orders=None,
    current_open_orders=None,
    max_inline_wait=30,
):
    kwargs = {"account_name": account_name, "action": action}
    if desired_open_orders is not None:
        kwargs["desired_open_orders"] = desired_open_orders
    if current_open_orders is not None:
        kwargs["current_open_orders"] = current_open_orders

    rc_gate = evaluate_tx_window(**kwargs)
    rc_percent = rc_gate.get("rc_percent")
    rc_text = "n/a" if rc_percent is None else f"{rc_percent:.2f}%"

    print(
        f"{bot_label} RC preflight: rc={rc_text} mode={rc_gate.get('mode')} "
        f"allow={rc_gate.get('allow')} wait={rc_gate.get('wait_seconds')}s"
    )

    if not rc_gate.get("allow", True):
        wait_seconds = RC_FAIL_COOLDOWN_SECONDS
        next_delay = dynamic_delay
        if dynamic_delay is not None:
            next_delay = max(10, wait_seconds)
        print(f"{bot_label} RC preflight blocked cycle before strategy actions.")
        if next_delay is not None:
            print(f"{bot_label} Next cycle delayed to {next_delay}s based on RC policy.")
        else:
            print(f"{bot_label} RC preflight cooldown: sleeping {wait_seconds}s before next cycle check.")
            time.sleep(wait_seconds)
        print(f"{divider}\n")
        return False, next_delay

    wait_seconds = int(rc_gate.get("wait_seconds", 0) or 0)
    if wait_seconds > 0 and wait_seconds <= max_inline_wait:
        time.sleep(wait_seconds)

    return True, dynamic_delay
