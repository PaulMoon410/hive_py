import os
from dotenv import load_dotenv

# Load .env if present for local dashboard environment
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import argparse
import json
import os
import random
import re
import subprocess
import sys
import threading
import time

PRINT_LOCK = threading.Lock()
RUNTIME_LOCK = threading.Lock()
BOT_RUNTIME = {}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "dashboard_config.json")

DEFAULT_BOT_JOBS = [
    {"name": "LTC", "script": "uni_ltc.py", "currency": "SWAP.LTC", "enabled": True},
    {"name": "ETH", "script": "uni_eth.py", "currency": "SWAP.ETH", "enabled": True},
    {"name": "BTC", "script": "uni_btc.py", "currency": "SWAP.BTC", "enabled": True},
    {"name": "HBD", "script": "uni_hbd.py", "currency": "SWAP.HBD", "enabled": True},
    {"name": "BNB", "script": "uni_bnb.py", "currency": "SWAP.BNB", "enabled": True},
    {"name": "MATIC", "script": "uni_matic.py", "currency": "SWAP.MATIC", "enabled": True},
    {"name": "PEK", "script": "uni_pek.py", "currency": "SWAP.PEK", "enabled": False},
]

DEFAULT_LAUNCH_SETTINGS = {
    "wait_for_cycle": True,
    "cycle_timeout_seconds": 300,
    "stagger_seconds_max": 10,
    "terminal_mode": "inline",
    "console_cols": 180,
    "console_lines": 7000,
    "clean_output": False,
}

# ANSI color codes for each bot
BOT_COLORS = {
    "LTC": "\033[96m",   # Cyan
    "ETH": "\033[92m",   # Green
    "BTC": "\033[93m",   # Yellow
    "DOGE": "\033[95m",  # Magenta
    "HBD": "\033[94m",   # Blue
    "BNB": "\033[33m",   # Orange/Yellow
    #"STEEM": "\033[90m",  # Dark Gray
    "MATIC": "\033[35m",  # Purple
    "PEK": "\033[91m",    # Red
}
RESET_COLOR = "\033[0m"
CLEAN_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
CYCLE_COMPLETE_RE = re.compile(r"\[BOT\]\s+Waiting\s+\d+\s+seconds\s+before\s+next\s+cycle", re.IGNORECASE)
WAIT_NEXT_CYCLE_RE = re.compile(r"\[BOT\]\s+Waiting\s+(\d+)\s+seconds\s+before\s+next\s+cycle", re.IGNORECASE)
WAIT_REMAINDER_RE = re.compile(r"\[BOT\]\s+Waiting\s+(\d+)\s+seconds\s+to\s+complete\s+delay\s+interval", re.IGNORECASE)


def dashboard_print(message):
    with PRINT_LOCK:
        print(message, flush=True)


def update_bot_runtime(name, line_text):
    now = time.time()
    m_cycle = WAIT_NEXT_CYCLE_RE.search(line_text)
    m_remainder = WAIT_REMAINDER_RE.search(line_text)
    if not m_cycle and not m_remainder:
        return

    with RUNTIME_LOCK:
        state = BOT_RUNTIME.setdefault(name, {})
        if m_cycle:
            wait_seconds = int(m_cycle.group(1))
            state["base_wait"] = wait_seconds
            state["base_ts"] = now
            state["cycle_wait"] = wait_seconds
            state["next_run_ts"] = now + wait_seconds
        elif m_remainder:
            wait_seconds = int(m_remainder.group(1))
            base_wait = int(state.get("base_wait", 0) or 0)
            base_ts = float(state.get("base_ts", 0) or 0)
            if base_wait > 0 and (now - base_ts) <= 30:
                state["cycle_wait"] = base_wait + wait_seconds
            else:
                state["cycle_wait"] = wait_seconds
            state["next_run_ts"] = now + wait_seconds

        ordered = sorted(
            ((bot, s.get("next_run_ts", 0), s.get("cycle_wait", 0)) for bot, s in BOT_RUNTIME.items() if s.get("next_run_ts")),
            key=lambda item: item[1],
        )

    if ordered:
        parts = []
        for bot, next_ts, cycle_wait in ordered:
            eta = max(0, int(next_ts - time.time()))
            parts.append(f"{bot}:eta={eta}s cycle={int(cycle_wait)}s")
        dashboard_print("[DASHBOARD] Delay order -> " + " | ".join(parts))

def parse_args():
    parser = argparse.ArgumentParser(description="Launch and monitor Uni bots")
    parser.add_argument(
        "--terminal-mode",
        choices=("inline", "new-window"),
        default=None,
        help="inline: aggregate all output here, new-window: open each bot in its own terminal",
    )
    parser.add_argument("--console-cols", type=int, default=None, help="terminal width for inline mode")
    parser.add_argument("--console-lines", type=int, default=None, help="terminal buffer lines for inline mode")
    parser.add_argument("--clean-output", action="store_true", help="force clean plain output")
    parser.add_argument("--no-clean-output", action="store_true", help="force colored/raw output")
    return parser.parse_args()


def load_dashboard_config():
    config = {
        "jobs": list(DEFAULT_BOT_JOBS),
        "launch": dict(DEFAULT_LAUNCH_SETTINGS),
    }
    if not os.path.exists(CONFIG_PATH):
        return config

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception as exc:
        dashboard_print(f"[DASHBOARD] Failed to load {CONFIG_PATH}: {exc}. Using defaults.")
        return config

    loaded_jobs = loaded.get("jobs") if isinstance(loaded, dict) else None
    if isinstance(loaded_jobs, list) and loaded_jobs:
        config["jobs"] = loaded_jobs

    loaded_launch = loaded.get("launch") if isinstance(loaded, dict) else None
    if isinstance(loaded_launch, dict):
        merged_launch = dict(DEFAULT_LAUNCH_SETTINGS)
        merged_launch.update(loaded_launch)
        config["launch"] = merged_launch
    return config


def build_launch_settings(parsed_args, config_launch):
    launch = dict(DEFAULT_LAUNCH_SETTINGS)
    launch.update(config_launch or {})

    if parsed_args.terminal_mode:
        launch["terminal_mode"] = parsed_args.terminal_mode
    if parsed_args.console_cols:
        launch["console_cols"] = parsed_args.console_cols
    if parsed_args.console_lines:
        launch["console_lines"] = parsed_args.console_lines

    if parsed_args.clean_output and parsed_args.no_clean_output:
        dashboard_print("[DASHBOARD] --clean-output and --no-clean-output both set, keeping config value.")
    elif parsed_args.clean_output:
        launch["clean_output"] = True
    elif parsed_args.no_clean_output:
        launch["clean_output"] = False

    launch["wait_for_cycle"] = bool(launch.get("wait_for_cycle", True))
    launch["cycle_timeout_seconds"] = int(launch.get("cycle_timeout_seconds", 300))
    launch["stagger_seconds_max"] = max(0, float(launch.get("stagger_seconds_max", 10)))
    launch["console_cols"] = max(80, int(launch.get("console_cols", 180)))
    launch["console_lines"] = max(1000, int(launch.get("console_lines", 7000)))
    if launch.get("terminal_mode") not in ("inline", "new-window"):
        launch["terminal_mode"] = "inline"
    return launch


def apply_console_size(cols, lines):
    if os.name != "nt":
        return
    if not sys.stdout or not sys.stdout.isatty():
        return
    try:
        os.system(f"mode con: cols={cols} lines={lines}")
    except Exception as exc:
        dashboard_print(f"[DASHBOARD] Console resize skipped: {exc}")


def normalize_line(raw_text, clean_output):
    text = raw_text.rstrip("\r\n")
    if clean_output:
        text = CLEAN_ANSI_RE.sub("", text)
    return text.strip()


def build_env(job):
    env = os.environ.copy()
    if job.get("account"):
        env["HIVE_ACCOUNT"] = str(job["account"])
    if job.get("active_key"):
        env["HIVE_ACTIVE_KEY"] = str(job["active_key"])
    if job.get("token"):
        env["TOKEN"] = str(job["token"])
    if job.get("enable_pek_bot") is not None:
        env["ENABLE_PEK_BOT"] = "true" if bool(job.get("enable_pek_bot")) else "false"
    # Force color output even when stdout is piped to dashboard
    env["FORCE_BOT_COLORS"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def launch_new_terminal(job):
    script = job["script"]
    script_path = os.path.join(SCRIPT_DIR, script)
    env = build_env(job)
    if not os.path.exists(script_path):
        dashboard_print(f"[DASHBOARD] Missing script for job {job['name']}: {script}")
        return None

    try:
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        proc = subprocess.Popen(
            [sys.executable, "-u", script],
            cwd=SCRIPT_DIR,
            env=env,
            creationflags=creationflags,
        )
        dashboard_print(
            f"[DASHBOARD] Started {job['name']} in a new terminal (pid={proc.pid}, account={job.get('account', '-')}, token={job.get('currency', '-')})."
        )
        return proc
    except Exception as exc:
        dashboard_print(f"[DASHBOARD] Failed to launch {job['name']} in new terminal: {exc}")
        return None


def run_bot_inline(job, launch_settings, first_cycle_done=None):
    name = job["name"]
    script = job["script"]
    clean_output = bool(launch_settings.get("clean_output", True))
    use_color = not clean_output and sys.stdout and sys.stdout.isatty()
    color = BOT_COLORS.get(name, "") if use_color else ""
    reset = RESET_COLOR if use_color else ""

    stagger = random.uniform(0, float(launch_settings.get("stagger_seconds_max", 10)))
    dashboard_print(
        f"{color}[DASHBOARD] Launching {name} ({script}, account={job.get('account', '-')}, token={job.get('currency', '-')}) after {stagger:.1f}s delay...{reset}"
    )
    time.sleep(stagger)
    script_path = os.path.join(SCRIPT_DIR, script)
    if not os.path.exists(script_path):
        dashboard_print(f"[DASHBOARD] Missing script for job {name}: {script}")
        if first_cycle_done is not None:
            first_cycle_done.set()
        return

    proc = subprocess.Popen(
        [sys.executable, "-u", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=SCRIPT_DIR,
        env=build_env(job),
    )
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        text = normalize_line(line, clean_output)
        if not text:
            continue
        dashboard_print(f"{color}[{name}] {text}{reset}")
        update_bot_runtime(name, text)
        if first_cycle_done is not None and CYCLE_COMPLETE_RE.search(text):
            first_cycle_done.set()
    dashboard_print(f"[DASHBOARD] {name} process ended (exit={proc.poll()}).")


def normalize_jobs(raw_jobs):
    jobs = []
    for idx, raw in enumerate(raw_jobs or []):
        if not isinstance(raw, dict):
            continue
        if raw.get("enabled", True) is False:
            continue
        name = str(raw.get("name") or f"BOT-{idx+1}")
        script = str(raw.get("script") or "").strip()
        if not script:
            dashboard_print(f"[DASHBOARD] Skipping {name}: no script provided.")
            continue
        jobs.append(
            {
                "name": name,
                "script": script,
                "currency": str(raw.get("currency") or ""),
                "token": str(raw.get("token") or raw.get("currency") or ""),
                "account": str(raw.get("account") or ""),
                "active_key": str(raw.get("active_key") or ""),
                "enable_pek_bot": raw.get("enable_pek_bot"),
            }
        )
    return jobs

def main():
    parsed_args = parse_args()
    config = load_dashboard_config()
    launch_settings = build_launch_settings(parsed_args, config.get("launch"))
    jobs = normalize_jobs(config.get("jobs"))

    if not jobs:
        dashboard_print("[DASHBOARD] No enabled jobs found. Configure dashboard_config.json and try again.")
        return

    if launch_settings["terminal_mode"] == "inline":
        apply_console_size(launch_settings["console_cols"], launch_settings["console_lines"])

    dashboard_print(
        f"[DASHBOARD] Mode={launch_settings['terminal_mode']} clean_output={launch_settings['clean_output']} jobs={len(jobs)}"
    )

    if launch_settings["terminal_mode"] == "new-window":
        procs = []
        for job in jobs:
            proc = launch_new_terminal(job)
            if proc is not None:
                procs.append((job["name"], proc))
        if not procs:
            dashboard_print("[DASHBOARD] No bots were started.")
            return
        dashboard_print("[DASHBOARD] Bots running in separate terminals. Press Ctrl+C here to stop monitoring.")
        try:
            while True:
                time.sleep(2)
                for name, proc in procs:
                    code = proc.poll()
                    if code is not None:
                        dashboard_print(f"[DASHBOARD] {name} terminal process exited (exit={code}).")
                procs = [(name, p) for name, p in procs if p.poll() is None]
                if not procs:
                    dashboard_print("[DASHBOARD] All bot processes exited.")
                    return
        except KeyboardInterrupt:
            dashboard_print("[DASHBOARD] Monitoring stopped. Bot terminals remain open.")
            return

    threads = []
    for idx, job in enumerate(jobs):
        first_cycle_done = threading.Event()
        t = threading.Thread(
            target=run_bot_inline,
            args=(job, launch_settings, first_cycle_done),
            daemon=True,
        )
        t.start()
        threads.append(t)
        if launch_settings["wait_for_cycle"] and idx < len(jobs) - 1:
            dashboard_print(
                f"[DASHBOARD] Waiting for {job['name']} to complete its current cycle before launching next bot..."
            )
            completed = first_cycle_done.wait(timeout=launch_settings["cycle_timeout_seconds"])
            if not completed:
                dashboard_print(
                    f"[DASHBOARD] Timed out waiting for {job['name']} cycle completion. Launching next bot anyway."
                )
    dashboard_print("[DASHBOARD] Monitoring bots. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(2)
    except KeyboardInterrupt:
        dashboard_print("[DASHBOARD] Exiting...")

if __name__ == "__main__":
    main()
