from typing import List, Dict
import math


class BacktestEngine:
    def __init__(
        self,
        initial_capital: float = 1.0,
        fee_pct: float = 0.003,      # 0.3%
        slippage_pct: float = 0.002  # 0.2%
    ):
        self.initial_capital = initial_capital
        self.balance = initial_capital
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct

        self.positions: List[Dict] = []
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []

    # ==========================
    # ORDER SIMULATION
    # ==========================
    def buy(self, price: float, timestamp, meta=None):
        cost = self.balance
        if cost <= 0:
            return None

        price_exec = price * (1 + self.slippage_pct)
        fee = cost * self.fee_pct
        size = (cost - fee) / price_exec

        pos = {
            "entry_price": price_exec,
            "size": size,
            "entry_time": timestamp,
            "meta": meta or {},
            "fee_paid": fee,
            "open": True
        }

        self.balance = 0
        self.positions.append(pos)
        self.trades.append({"type": "BUY", **pos})
        return pos

    def sell(self, pos: Dict, price: float, timestamp, reason="EXIT"):
        if not pos["open"]:
            return

        price_exec = price * (1 - self.slippage_pct)
        gross = pos["size"] * price_exec
        fee = gross * self.fee_pct
        net = gross - fee

        pnl = net - (pos["size"] * pos["entry_price"])

        self.balance += net
        pos["open"] = False

        self.trades.append({
            "type": "SELL",
            "exit_price": price_exec,
            "exit_time": timestamp,
            "pnl": pnl,
            "reason": reason,
            "fee_paid": fee
        })

    # ==========================
    # METRICS
    # ==========================
    def mark_equity(self, price: float):
        equity = self.balance
        for p in self.positions:
            if p["open"]:
                equity += p["size"] * price
        self.equity_curve.append(equity)

    def results(self):
        wins = [t for t in self.trades if t.get("pnl", 0) > 0]
        losses = [t for t in self.trades if t.get("pnl", 0) < 0]

        roi = (self.balance - self.initial_capital) / self.initial_capital * 100

        return {
            "initial_capital": self.initial_capital,
            "final_capital": self.balance,
            "roi_pct": round(roi, 2),
            "total_trades": len(wins) + len(losses),
            "win_rate_pct": round(len(wins) / max(1, len(wins) + len(losses)) * 100, 2),
            "profit_factor": (
                abs(sum(t["pnl"] for t in wins)) /
                max(1e-9, abs(sum(t["pnl"] for t in losses)))
            )
        }
