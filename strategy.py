# strategy.py
"""
Shared adaptive quoting and learning engine for all bots.
Handles quoting, fill tracking, inventory-aware logic, and state persistence.
"""
import json
import os
import time

class StrategyState:
    def __init__(self, state_file):
        self.state_file = state_file
        self.state = {
            'buy_distance': 0.01,  # as fraction of spread
            'sell_distance': 0.01,
            'min_profit': 0.005,
            'inventory_target': 0.5,  # 50% in token, 50% in HIVE
            'order_stats': {},  # order_id: {status, submit_time, fill_time, cancel_time, side, price, size}
            'fill_history': [],  # [{order_id, side, price, size, fill_time, pnl}]
            'cancel_history': [],
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'last_update': time.time(),
        }
        self.load()

    def load(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    self.state.update(json.load(f))
            except Exception:
                pass

    def save(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception:
            pass

    def track_order(self, order_id, side, price, size):
        self.state['order_stats'][order_id] = {
            'status': 'open',
            'submit_time': time.time(),
            'side': side,
            'price': price,
            'size': size
        }
        self.save()

    def update_order_fill(self, order_id, fill_size, fill_price):
        order = self.state['order_stats'].get(order_id)
        if order:
            order['status'] = 'filled'
            order['fill_time'] = time.time()
            pnl = (fill_price - order['price']) * fill_size if order['side'] == 'buy' else (order['price'] - fill_price) * fill_size
            self.state['fill_history'].append({
                'order_id': order_id,
                'side': order['side'],
                'price': fill_price,
                'size': fill_size,
                'fill_time': order['fill_time'],
                'pnl': pnl
            })
            self.state['realized_pnl'] += pnl
            self.save()

    def update_order_cancel(self, order_id):
        order = self.state['order_stats'].get(order_id)
        if order:
            order['status'] = 'canceled'
            order['cancel_time'] = time.time()
            self.state['cancel_history'].append(order_id)
            self.save()

    def get_inventory_skew(self, token_balance, hive_balance, token_price):
        total_value = token_balance * token_price + hive_balance
        if total_value == 0:
            return 0.0
        token_ratio = (token_balance * token_price) / total_value
        return token_ratio - self.state['inventory_target']

    def get_adaptive_quote(self, best_bid, best_ask, spread, token_balance, hive_balance, token_price, volatility=0.0):
        # Inventory skew: if holding too much token, tighten sell, widen buy; vice versa
        skew = self.get_inventory_skew(token_balance, hive_balance, token_price)
        buy_dist = self.state['buy_distance'] + max(0, -skew) * 0.01
        sell_dist = self.state['sell_distance'] + max(0, skew) * 0.01
        # Volatility adjustment
        if volatility > 0.01:
            buy_dist += volatility * 0.5
            sell_dist += volatility * 0.5
        # Quote placement
        buy_quote = best_bid + spread * buy_dist
        sell_quote = best_ask - spread * sell_dist
        # Ensure min profit
        if sell_quote - buy_quote < self.state['min_profit'] * spread:
            mid = (best_bid + best_ask) / 2
            buy_quote = mid - self.state['min_profit'] * spread / 2
            sell_quote = mid + self.state['min_profit'] * spread / 2
        return round(buy_quote, 8), round(sell_quote, 8)

    def get_order_ttl(self, liquidity, base_ttl=300):
        # Lower liquidity = longer patience
        if liquidity < 10:
            return base_ttl * 2
        elif liquidity < 100:
            return base_ttl * 1.5
        return base_ttl

    def should_cancel_order(self, order, best_bid, best_ask, max_age=1800):
        now = time.time()
        if order['status'] != 'open':
            return False
        age = now - order['submit_time']
        # Cancel if too old
        if age > max_age:
            return True
        # Cancel if price is now far from best quote
        if order['side'] == 'buy' and order['price'] < best_bid * 0.98:
            return True
        if order['side'] == 'sell' and order['price'] > best_ask * 1.02:
            return True
        return False

class Strategy:
    def generate_quote(self, market_data, inventory, risk, params):
        # Use adaptive quoting from StrategyState if available, else fallback
        bid = float(market_data.get('highestBid', 0))
        ask = float(market_data.get('lowestAsk', 0))
        spread = ask - bid
        if bid <= 0 or ask <= 0 or spread <= 0:
            return None
        token = params.get('token')
        token_price = ask if ask > 0 else bid
        hive_bal = inventory.get('SWAP.HIVE', 0)
        token_bal = inventory.get(token, 0)
        # Use simple inventory-aware quoting
        buy_price = bid + spread * 0.1
        sell_price = ask - spread * 0.1
        buy_qty = round(hive_bal * 0.2 / buy_price, 8) if buy_price > 0 else 0
        sell_qty = round(token_bal * 0.2, 8)
        return {
            'buy_price': buy_price,
            'buy_qty': buy_qty,
            'sell_price': sell_price,
            'sell_qty': sell_qty
        }

# Example usage:
# state = StrategyState('.ltc_state.json')
# buy_quote, sell_quote = state.get_adaptive_quote(...)
# state.track_order(order_id, 'buy', buy_quote, size)
# state.update_order_fill(order_id, fill_size, fill_price)
# state.update_order_cancel(order_id)
