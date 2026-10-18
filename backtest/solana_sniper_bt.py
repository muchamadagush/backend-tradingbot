import pandas as pd
from backtest.engine import BacktestEngine


def backtest_sniper(df: pd.DataFrame):
    engine = BacktestEngine(
        fee_pct=0.004,
        slippage_pct=0.005
    )

    for i in range(5, len(df)):
        candle = df.iloc[i]

        # ===== ENTRY: PUMP EVENT =====
        pump = (candle["close"] - candle["open"]) / candle["open"] * 100

        if pump > 15 and not engine.positions:
            engine.buy(candle["close"], candle["ts"], meta={"event": "pump"})

        # ===== EXIT =====
        for pos in engine.positions:
            if not pos["open"]:
                continue

            entry = pos["entry_price"]
            pnl = (candle["close"] - entry) / entry * 100

            if pnl >= 30:
                engine.sell(pos, candle["close"], candle["ts"], "TP")
            elif pnl <= -20:
                engine.sell(pos, candle["close"], candle["ts"], "SL")

        engine.mark_equity(candle["close"])

    return engine.results()
