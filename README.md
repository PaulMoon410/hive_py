# PeakeCoin Hive Engine Trading Bots

This project contains automated trading bots for various Hive Engine tokens (LTC, ETH, BTC, DOGE, HBD, BNB, USDT) and a dashboard to monitor them.

## Features

- Automated buy/sell logic for each token
- Profit enforcement and duplicate order prevention
- Uses only the Hive active key for transactions
- Dashboard for monitoring all bots with color-coded output

## Requirements

- Python 3.11+ (recommended)
- `requests` library (`pip install requests`)
- Hive Engine account and active key

## Setup

1. Clone this repository or copy the files to your machine.
2. Install dependencies:
   ```
   pip install requests
   ```
3. Edit the bot scripts if you need to change account details or tokens.

## Running the Bots

To launch all bots and the dashboard:
```
python dashboard.py
```

To run multiple accounts on different currencies, define jobs in `dashboard_config.json`
based on `dashboard_config.json.example`.

Dashboard launch options:
```
python dashboard.py --terminal-mode inline --clean-output --console-cols 200 --console-lines 9000
python dashboard.py --terminal-mode new-window
```

- `inline` mode keeps all bot logs in one terminal with clean prefixed output.
- `new-window` mode opens each bot in a separate terminal window.
- `--console-cols` and `--console-lines` resize the inline terminal on Windows.
- `--clean-output` removes ANSI color/control codes for cleaner shared output.

To run a single bot (for testing):
```
python uni_ltc.py
```

## Files

- `dashboard.py` — Launches and monitors all bots
- `uni_ltc.py`, `uni_eth.py`, ... — Individual bot scripts for each token
- `profit_strategies.py`, `fetch_market.py`, `place_order.py` — Shared logic modules

## Notes

- Make sure your Hive account has enough resource credits and balances.
- The bots will wait between trade cycles (see `DELAY` in each script).
- All trading is at your own risk.
