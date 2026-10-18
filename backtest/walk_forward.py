import pandas as pd
from typing import Callable, Dict, List


def walk_forward_validation(
    df: pd.DataFrame,
    backtest_fn: Callable,
    train_size: int,
    test_size: int,
    step: int
) -> List[Dict]:
    """
    backtest_fn: function(df) -> dict result
    """
    results = []

    start = 0
    total_len = len(df)

    while start + train_size + test_size <= total_len:
        train_df = df.iloc[start : start + train_size]
        test_df = df.iloc[start + train_size : start + train_size + test_size]

        # NOTE: kita TIDAK optimize parameter di sini (next phase)
        test_result = backtest_fn(test_df)

        results.append({
            "train_range": (start, start + train_size),
            "test_range": (
                start + train_size,
                start + train_size + test_size
            ),
            "result": test_result
        })

        start += step

    return results
