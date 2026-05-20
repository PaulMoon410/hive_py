def calculate_min_sell_price(buy_price, min_profit_percent=0.02):
    """Calculate the minimum sell price to achieve desired profit percent (default 2%)."""
    return round(buy_price * (1 + min_profit_percent), 8)

def is_profitable(buy_price, sell_price, min_profit_percent=0.02):
    """
    Return True if sell_price meets or exceeds the minimum profit percent over buy_price.
    Guarantees profit if True.
    """
    min_sell = calculate_min_sell_price(buy_price, min_profit_percent)
    return sell_price >= min_sell

def is_profitable_or_volume_increase(buy_price, sell_price, buy_qty, sell_qty, min_profit_percent=0.02):
    """
    Return True if and only if sell_price meets/exceeds minimum profit percent over buy_price.
    Guarantees profit on every sale.
    """
    min_sell = calculate_min_sell_price(buy_price, min_profit_percent)
    return sell_price >= min_sell

def get_spread_percent(bid, ask):
    """Return bid/ask spread as a decimal percent of mid price."""
    try:
        bid = float(bid)
        ask = float(ask)
    except Exception:
        return 0.0
    if bid <= 0 or ask <= 0:
        return 0.0
    mid = (bid + ask) / 2
    if mid <= 0:
        return 0.0
    spread = max(ask - bid, 0.0)
    return spread / mid

def get_dynamic_min_profit_percent(bid, ask, floor=0.002, ceiling=0.03, spread_multiplier=1.5):
    """
    Return a dynamic minimum profit percent (decimal) based on current spread.
    - floor/ceiling are decimals (0.002 = 0.2%).
    - spread_multiplier scales the spread-derived target.
    """
    spread_pct = get_spread_percent(bid, ask)
    target = spread_pct * spread_multiplier
    if target < floor:
        target = floor
    if target > ceiling:
        target = ceiling
    return round(target, 6)

def adaptive_profit_floor(success_rate, base_floor=0.002, base_ceiling=0.03, 
                         min_floor=0.0005, max_floor=0.05):
    """
    Adapt the profit floor based on success rate (0.0 to 1.0).
    - High success (>0.7) → lower floor to increase volume
    - Low success (<0.3) → raise floor to guarantee better margins
    - Returns adjusted floor clamped between min_floor and max_floor
    """
    if success_rate >= 0.7:
        # High success: lower floor by 30%, but not below min_floor
        adjusted = base_floor * 0.7
    elif success_rate >= 0.5:
        # Moderate success: slight reduction
        adjusted = base_floor * 0.85
    elif success_rate >= 0.3:
        # Low-moderate: keep base
        adjusted = base_floor
    else:
        # Very low success: raise floor by 50%
        adjusted = base_floor * 1.5
    
    # Clamp to bounds
    adjusted = max(adjusted, min_floor)
    adjusted = min(adjusted, max_floor)
    return round(adjusted, 6)

def guarantee_profit(buy_price, sell_price, min_margin=0.0001):
    """
    Ensure sell_price > buy_price with minimum margin.
    If not profitable, adjust sell_price upward.
    Returns guaranteed profitable sell_price.
    """
    guaranteed_sell = buy_price * (1 + min_margin)
    return max(float(sell_price), guaranteed_sell) if sell_price > 0 else guaranteed_sell

def get_profit_percent(buy_price, sell_price):
    """Return the actual profit percent for a given buy/sell price."""
    if buy_price == 0:
        return 0.0
    return round((sell_price - buy_price) / buy_price * 100, 4)
