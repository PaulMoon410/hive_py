import os
import time
import json
import requests
from typing import Dict, Optional

STATE_FILE = os.path.join(os.path.dirname(__file__), ".rc_intelligence_state.json")
RC_API_URL = "https://api.hive.blog"

# RC operating bands
RC_CRITICAL = 6.0
RC_LOW = 10.0
RC_GUARDED = 20.0
RC_HEALTHY = 40.0


def _load_state() -> Dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {"accounts": {}}


def _save_state(state: Dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def _ensure_account(state: Dict, account_name: str) -> Dict:
    accounts = state.setdefault("accounts", {})
    acc = accounts.get(account_name)
    if not isinstance(acc, dict):
        acc = {
            "last_rc_percent": None,
            "last_rc_ts": 0,
            "recharge_ema_per_sec": 0.0,
            "attempts": 0,
            "successes": 0,
            "failures": 0,
            "failure_streak": 0,
            "cooldown_until": 0,
            "last_mode": "unknown",
            "last_reason": "",
        }
        accounts[account_name] = acc
    return acc


def get_rc_snapshot(account_name: str, timeout: int = 8) -> Optional[Dict]:
    payload = {
        "jsonrpc": "2.0",
        "method": "rc_api.find_rc_accounts",
        "params": {"accounts": [account_name]},
        "id": 1,
    }
    try:
        resp = requests.post(RC_API_URL, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        rc = data.get("result", {}).get("rc_accounts", [{}])[0]
        if not rc or "rc_manabar" not in rc or "max_rc" not in rc:
            return None
        current = int(rc["rc_manabar"]["current_mana"])
        max_rc = int(rc["max_rc"])
        if max_rc <= 0:
            return None
        percent = round((current / max_rc) * 100, 4)
        return {
            "percent": percent,
            "current_mana": current,
            "max_rc": max_rc,
            "timestamp": int(time.time()),
        }
    except Exception:
        return None


def _estimate_seconds_to_target(current_rc: float, target_rc: float, recharge_per_sec: float) -> int:
    if current_rc >= target_rc:
        return 0
    if recharge_per_sec <= 0:
        return 120
    needed = target_rc - current_rc
    return max(1, int(needed / recharge_per_sec))


def _mode_from_rc(rc_percent: Optional[float]) -> str:
    if rc_percent is None:
        return "unknown"
    if rc_percent < RC_CRITICAL:
        return "critical"
    if rc_percent < RC_LOW:
        return "low"
    if rc_percent < RC_GUARDED:
        return "guarded"
    if rc_percent < RC_HEALTHY:
        return "healthy"
    return "high"


def evaluate_tx_window(
    account_name: str,
    action: str = "order",
    desired_open_orders: int = 150,
    current_open_orders: Optional[int] = None,
) -> Dict:
    """Return an intelligent RC policy decision for the next tx attempt.

    action: buy | sell | cancel | order
    """
    now = int(time.time())
    state = _load_state()
    acc = _ensure_account(state, account_name)

    snapshot = get_rc_snapshot(account_name)
    rc_percent = snapshot.get("percent") if snapshot else acc.get("last_rc_percent")

    # Update recharge EMA when we have a fresh reading.
    if snapshot:
        prev_rc = acc.get("last_rc_percent")
        prev_ts = int(acc.get("last_rc_ts", 0) or 0)
        if prev_rc is not None and prev_ts > 0 and snapshot["timestamp"] > prev_ts:
            delta_rc = float(snapshot["percent"]) - float(prev_rc)
            delta_t = max(1, snapshot["timestamp"] - prev_ts)
            instant_rate = delta_rc / delta_t
            old_ema = float(acc.get("recharge_ema_per_sec", 0.0) or 0.0)
            alpha = 0.25
            new_ema = (alpha * instant_rate) + ((1 - alpha) * old_ema)
            acc["recharge_ema_per_sec"] = max(0.0, new_ema)
        acc["last_rc_percent"] = snapshot["percent"]
        acc["last_rc_ts"] = snapshot["timestamp"]

    cooldown_until = int(acc.get("cooldown_until", 0) or 0)
    if now < cooldown_until:
        wait_seconds = cooldown_until - now
        decision = {
            "allow": False,
            "mode": "cooldown",
            "rc_percent": rc_percent,
            "wait_seconds": wait_seconds,
            "reason": f"cooldown active for {wait_seconds}s",
            "max_new_orders": 0,
        }
        acc["last_mode"] = decision["mode"]
        acc["last_reason"] = decision["reason"]
        _save_state(state)
        return decision

    mode = _mode_from_rc(rc_percent)
    allow = True
    wait_seconds = 0
    max_new_orders = 1
    reason = ""

    is_cancel = str(action).lower() == "cancel"
    needs_open_order_recovery = (
        current_open_orders is not None and current_open_orders < desired_open_orders
    )

    if mode == "critical":
        allow = is_cancel and (rc_percent is not None and rc_percent >= 4.0)
        max_new_orders = 0
        if not is_cancel and needs_open_order_recovery and (rc_percent is not None and rc_percent >= 5.0):
            allow = True
            max_new_orders = 1
        target = 8.0 if not is_cancel else 6.0
        eta = _estimate_seconds_to_target(
            float(rc_percent or 0.0),
            target,
            float(acc.get("recharge_ema_per_sec", 0.0) or 0.0),
        )
        wait_seconds = max(20, min(240, eta))
        if allow and not is_cancel and max_new_orders > 0:
            reason = "rc critical; allowing one recovery order to rebuild book"
        else:
            reason = "rc critical; preserve mana"
    elif mode == "low":
        # Allow recovery buys when book is under target, otherwise block new orders.
        allow = is_cancel or needs_open_order_recovery
        max_new_orders = 1 if allow and not is_cancel else 0
        target = 12.0
        eta = _estimate_seconds_to_target(
            float(rc_percent or 0.0),
            target,
            float(acc.get("recharge_ema_per_sec", 0.0) or 0.0),
        )
        wait_seconds = max(10, min(120, eta))
        reason = "rc low; maintenance mode"
    elif mode == "guarded":
        allow = True
        max_new_orders = 2
        wait_seconds = 5
        reason = "rc guarded; reduced throughput"
    elif mode == "healthy":
        allow = True
        max_new_orders = 4
        wait_seconds = 1
        reason = "rc healthy"
    elif mode == "high":
        allow = True
        max_new_orders = 8
        wait_seconds = 0
        reason = "rc high"
    else:
        allow = True
        max_new_orders = 2
        wait_seconds = 2
        reason = "rc unknown; conservative"

    decision = {
        "allow": allow,
        "mode": mode,
        "rc_percent": rc_percent,
        "wait_seconds": wait_seconds,
        "reason": reason,
        "max_new_orders": max_new_orders,
    }
    acc["last_mode"] = mode
    acc["last_reason"] = reason
    _save_state(state)
    return decision


def record_tx_outcome(account_name: str, success: bool) -> None:
    now = int(time.time())
    state = _load_state()
    acc = _ensure_account(state, account_name)

    acc["attempts"] = int(acc.get("attempts", 0) or 0) + 1
    if success:
        acc["successes"] = int(acc.get("successes", 0) or 0) + 1
        acc["failure_streak"] = 0
        acc["cooldown_until"] = 0
    else:
        acc["failures"] = int(acc.get("failures", 0) or 0) + 1
        streak = int(acc.get("failure_streak", 0) or 0) + 1
        acc["failure_streak"] = streak
        rc_percent = acc.get("last_rc_percent")
        if rc_percent is not None and float(rc_percent) < RC_LOW:
            cooldown = min(300, 15 * (2 ** min(streak, 5)))
        else:
            cooldown = min(60, 5 * streak)
        acc["cooldown_until"] = now + cooldown

    _save_state(state)


def recommended_delay_seconds(account_name: str, base_delay: int) -> int:
    decision = evaluate_tx_window(account_name, action="order")
    return max(int(base_delay), int(decision.get("wait_seconds", 0) or 0))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RC intelligence monitor")
    parser.add_argument("--accounts", nargs="+", required=True, help="Hive accounts to monitor")
    parser.add_argument("--interval", type=int, default=30, help="Polling interval in seconds")
    args = parser.parse_args()

    while True:
        for acc in args.accounts:
            decision = evaluate_tx_window(acc, action="order")
            rc = decision.get("rc_percent")
            rc_str = "n/a" if rc is None else f"{rc:.2f}%"
            print(
                f"[RC] account={acc} rc={rc_str} mode={decision.get('mode')} "
                f"allow={decision.get('allow')} wait={decision.get('wait_seconds')}s "
                f"reason={decision.get('reason')}"
            )
        time.sleep(max(5, args.interval))
