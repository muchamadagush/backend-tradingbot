import argparse
import sys
from pathlib import Path

# ---- ENSURE PROJECT ROOT IN PATH ----
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from backtest.data_loader import CandleDataLoader
from backtest.backtest_engine import BacktestEngine

from strategies.solana_trader import SolanaTraderStrategy
from strategies.solana_sniper import SolanaSniperStrategy


def main():
    parser = argparse.ArgumentParser(description="Solana Strategy Backtester")
    parser.add_argument(
        "--strategy",
        choices=["TRADER", "SNIPER"],
        required=True,
        help="Strategy type"
    )
    parser.add_argument(
        "--pool",
        required=True,
        help="Pool ID (CSV filename without .csv)"
    )
    args = parser.parse_args()

    # =============================
    # LOAD DATA
    # =============================
    loader = CandleDataLoader()
    df = loader.load_csv(args.pool)

    if df.empty:
        raise RuntimeError("❌ Candle data is empty")

    print(f"📥 Loaded candles: {len(df)} rows")

    # =============================
    # STRATEGY SELECT
    # =============================
    if args.strategy == "TRADER":
        strategy = SolanaTraderStrategy()
    else:
        strategy = SolanaSniperStrategy()

    # =============================
    # BACKTEST ENGINE
    # =============================
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=1.0,   # 1 SOL
        slippage_pct=0.5,      # 0.5%
        fee_pct=0.1            # 0.1%
    )

    result = engine.run(df)

    # =============================
    # OUTPUT
    # =============================
    print("\n📊 BACKTEST RESULT")
    print("-" * 40)
    for k, v in result.items():
        print(f"{k:20}: {v}")


if __name__ == "__main__":
    main()
