# state.py
"""Order/fill tracking and learning state module."""

class StateTracker:
    def track_order(self, order_id, status, timestamp, price, qty, side):
        print(f"[STATE] Track order: {order_id} {status} {timestamp} {price} {qty} {side}")

    def update_fill(self, order_id, fill_qty, fill_price, timestamp):
        print(f"[STATE] Update fill: {order_id} {fill_qty} {fill_price} {timestamp}")

    def calculate_metrics(self):
        print("[STATE] Calculate metrics (not implemented)")
