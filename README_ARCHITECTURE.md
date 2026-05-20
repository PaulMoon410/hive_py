# Modular Trading Bot Architecture

## Modules
- fetch_market.py: Market data
- execution.py: Order/cancel
- risk.py: RC, exposure, approval
- strategy.py: Quote logic
- state.py: Order/fill tracking, learning
- orchestrator.py: Central manager

## Next Steps
- Refactor bots to use these modules
- Implement advanced learning, risk, and quoting logic
