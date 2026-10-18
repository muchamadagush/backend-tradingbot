import pandas as pd
from typing import Dict, List
from dataclasses import dataclass


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_price: float
    exit_price: float
    pnl_pct: float
    reason: str


class BacktestEngine:
    def __init__(
        self,
        strategy,
        initial_capital=1.0,
        slippage_pct=0.5,
        fee_pct=0.1
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.slippage_pct = slippage_pct
        self.fee_pct = fee_pct

        self.trades: List[Trade] = []
        self.position = None
        self.capital = initial_capital

    def run(self, df: pd.DataFrame) -> Dict:
        df = df.copy().reset_index(drop=True)

        for i in range(len(df)):
            candle = df.iloc[: i + 1]

            market = {
                "df": candle,
                "close": candle.iloc[-1]["close"],
                "volume": candle.iloc[-1]["volume"],
                "ts": candle.iloc[-1]["ts"]
            }

            # =============================
            # ENTRY
            # =============================
            if self.position is None:
                buy, reason = self.strategy.should_buy(market)
                if buy:
                    price = market["close"] * (1 + self.slippage_pct / 100)

                    self.position = {
                        "entry_price": price,
                        "entry_ts": market["ts"],
                        "size": self.capital,
                        "pnl_pct": 0.0
                    }

                    self.strategy.on_buy(self.position)
                continue

            # =============================
            # EXIT
            # =============================
            current_price = market["close"]

            pnl_pct = (
                (current_price - self.position["entry_price"])
                / self.position["entry_price"]
            ) * 100

            self.position["pnl_pct"] = pnl_pct

            sell, reason = self.strategy.should_sell(
                self.position,
                market=market
            )

            if sell:
                exit_price = current_price * (1 - self.slippage_pct / 100)
                exit_price *= (1 - self.fee_pct / 100)

                pnl = (
                    (exit_price - self.position["entry_price"])
                    / self.position["entry_price"]
                )

                self.capital *= (1 + pnl)

                self.trades.append(
                    Trade(
                        entry_ts=self.position["entry_ts"],
                        exit_ts=market["ts"],
                        entry_price=self.position["entry_price"],
                        exit_price=exit_price,
                        pnl_pct=pnl * 100,
                        reason=reason
                    )
                )

                self.strategy.on_sell(self.position)
                self.position = None

        return self.stats()

    def stats(self) -> Dict:
        wins = [t for t in self.trades if t.pnl_pct > 0]
        losses = [t for t in self.trades if t.pnl_pct <= 0]

        return {
            "trades": len(self.trades),
            "winrate": len(wins) / len(self.trades) * 100 if self.trades else 0,
            "avg_win": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
            "avg_loss": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
            "final_capital": self.capital,
            "roi_pct": (self.capital - self.initial_capital) * 100
        }
