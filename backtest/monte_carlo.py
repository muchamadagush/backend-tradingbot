import random
import numpy as np


def monte_carlo_simulation(
    trades: list,
    initial_capital: float,
    runs: int = 1000
):
    """
    trades: list of dict with 'pnl' field (absolute PnL)
    """
    if not trades:
        return {}

    pnl_list = [t["pnl"] for t in trades if "pnl" in t]

    results = {
        "final_capitals": [],
        "max_drawdowns": [],
        "ruin_count": 0
    }

    for _ in range(runs):
        capital = initial_capital
        peak = capital
        max_dd = 0

        shuffled = pnl_list[:]
        random.shuffle(shuffled)

        for pnl in shuffled:
            capital += pnl

            if capital <= 0:
                results["ruin_count"] += 1
                break

            if capital > peak:
                peak = capital

            dd = (peak - capital) / peak * 100
            max_dd = max(max_dd, dd)

        results["final_capitals"].append(capital)
        results["max_drawdowns"].append(max_dd)

    return {
        "runs": runs,
        "median_final_capital": np.median(results["final_capitals"]),
        "best_final_capital": max(results["final_capitals"]),
        "worst_final_capital": min(results["final_capitals"]),
        "median_max_drawdown_pct": np.median(results["max_drawdowns"]),
        "worst_drawdown_pct": max(results["max_drawdowns"]),
        "risk_of_ruin_pct": results["ruin_count"] / runs * 100
    }
