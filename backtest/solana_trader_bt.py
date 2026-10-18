import pandas as pd
from backtest.engine import BacktestEngine
from strategies.solana_trader import SolanaTraderStrategy


def backtest_trader(df: pd.DataFrame):
    engine = BacktestEngine()
    strategy = SolanaTraderStrategy()

    for i in range(30, len(df)):
        candle = df.iloc[i]
        history = df.iloc[:i+1]

        # ===== ENTRY =====
        if not engine.positions:
            ok, _ = strategy.should_buy({"df": history})
            if ok:
                engine.buy(candle["close"], candle["ts"])

        # ===== EXIT =====
        for pos in engine.positions:
            if not pos["open"]:
                continue

            entry = pos["entry_price"]
            pnl_high = (candle["high"] - entry) / entry * 100
            pnl_low = (candle["low"] - entry) / entry * 100

            pos_state = {"pnl_pct": pnl_high}
            sell, reason = strategy.should_sell(pos_state, {})

            if sell:
                engine.sell(pos, candle["close"], candle["ts"], reason)

        engine.mark_equity(candle["close"])

    return engine.results()
