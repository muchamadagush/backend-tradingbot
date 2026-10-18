import pandas as pd
import sqlite3
from pathlib import Path


class CandleDataLoader:
    def __init__(self, base_path="data/candles"):
        self.base_path = Path(base_path)

    # =============================
    # CSV LOADER
    # =============================
    def load_csv(self, pool_id: str) -> pd.DataFrame:
        file = self.base_path / f"{pool_id}.csv"
        if not file.exists():
            raise FileNotFoundError(f"CSV not found: {file}")

        df = pd.read_csv(file)

        # ---- HARD CONTRACT CHECK ----
        required_cols = {"ts", "open", "high", "low", "close", "volume"}
        if not required_cols.issubset(df.columns):
            raise ValueError(
                f"Invalid candle CSV format. "
                f"Required={required_cols}, Found={set(df.columns)}"
            )

        # ---- TIMESTAMP NORMALIZATION ----
        if not pd.api.types.is_datetime64_any_dtype(df["ts"]):
            df["ts"] = pd.to_datetime(df["ts"])

        df.sort_values("ts", inplace=True)
        df.reset_index(drop=True, inplace=True)

        return df

    # =============================
    # SQLITE LOADER
    # =============================
    def load_sqlite(self, db_path: str, table: str) -> pd.DataFrame:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql(f"SELECT * FROM {table}", conn)
        conn.close()

        required_cols = {"ts", "open", "high", "low", "close", "volume"}
        if not required_cols.issubset(df.columns):
            raise ValueError("Invalid candle table format")

        if not pd.api.types.is_datetime64_any_dtype(df["ts"]):
            df["ts"] = pd.to_datetime(df["ts"])

        df.sort_values("ts", inplace=True)
        df.reset_index(drop=True, inplace=True)

        return df
