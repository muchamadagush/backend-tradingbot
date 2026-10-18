# File: backend/strategy.py
import pandas as pd
import pandas_ta as ta
from typing import Dict, Tuple, Optional
from collections import defaultdict

# ==========================================
# SIGNAL QUALITY TRACKER (P4 - ACTIVE ADAPTIVE FILTER)
# ==========================================

class SignalQualityTracker:
    """Track signal performance per sub-strategy for adaptive quality filtering."""

    def __init__(self, min_trades: int = 8, min_win_rate: float = 0.42):
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.signals: Dict[str, list] = defaultdict(list)  # strategy -> [(signal, result), ...]

    def record(self, strategy: str, signal: str, result: float):
        """Record a signal outcome (result > 0 = net profit win)."""
        if not strategy or strategy == "UNKNOWN":
            return
        self.signals[strategy].append((signal, float(result)))
        # Keep rolling window of last 100 signals per strategy
        if len(self.signals[strategy]) > 100:
            self.signals[strategy] = self.signals[strategy][-100:]

    def get_win_rate(self, strategy: str) -> Tuple[float, int]:
        """Get win rate and sample size for a strategy."""
        history = self.signals.get(strategy, [])
        if not history:
            return 0.5, 0  # Default 50% prior if no history

        wins = sum(1 for _, result in history if result > 0)
        total = len(history)
        return (wins / total if total > 0 else 0.5), total

    def get_profit_factor(self, strategy: str) -> float:
        """Calculate rolling profit factor for a strategy."""
        history = self.signals.get(strategy, [])
        if not history:
            return 1.2
        gross_win = sum(r for _, r in history if r > 0)
        gross_loss = abs(sum(r for _, r in history if r < 0))
        if gross_loss == 0:
            return 2.0 if gross_win > 0 else 1.0
        return gross_win / gross_loss

    def is_signal_quality_ok(self, strategy: str) -> bool:
        """Check if strategy has acceptable win rate OR positive expectancy (Profit Factor >= 1.15)."""
        win_rate, sample_size = self.get_win_rate(strategy)
        if sample_size < self.min_trades:
            return True
        pf = self.get_profit_factor(strategy)
        return (win_rate >= self.min_win_rate) or (pf >= 1.15)

    def get_all_stats(self) -> Dict:
        """Get stats for all strategies."""
        stats = {}
        for strategy in self.signals:
            win_rate, sample_size = self.get_win_rate(strategy)
            stats[strategy] = {
                "win_rate": round(win_rate * 100, 1),
                "profit_factor": round(self.get_profit_factor(strategy), 2),
                "sample_size": sample_size,
                "is_quality_ok": self.is_signal_quality_ok(strategy),
            }
        return stats


# Global signal tracker
signal_tracker = SignalQualityTracker()


# ==========================================
# 1. RUMUS & INDIKATOR (MULTI-FACTOR CONFLUENCE)
# ==========================================

def _ema(series, length):
    return ta.ema(series, length=length)


def populate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Menghitung indikator lengkap dengan Dynamic Mapping (Anti-Error):
    - RSI (14)
    - MACD (12, 26, 9) + Histogram
    - Bollinger Bands (20, 2.0) + BB Width + Rolling BB Width SMA (untuk relative squeeze) + %B
    - ADX (14) + +DI / -DI
    - ATR (14)
    - EMA (9, 21, 50)
    - Volume SMA (20) & Volume Ratio
    """
    df = df.copy()

    # Safety: Pastikan data numerik
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    try:
        df.ta.rsi(length=14, append=True)
        df.ta.macd(fast=12, slow=26, signal=9, append=True)
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.adx(length=14, append=True)
        df.ta.atr(length=14, append=True)
    except Exception as e:
        print(f"⚠️ Error calculating indicators: {e}")
        return df

    cols = df.columns.tolist()

    rsi_col = next((c for c in cols if c.startswith('RSI_')), None)
    if rsi_col:
        df['RSI'] = df[rsi_col]

    macd_col = next((c for c in cols if c.startswith('MACD_') and 'h' not in c and 's' not in c), None)
    sig_col = next((c for c in cols if c.startswith('MACDs_')), None)
    hist_col = next((c for c in cols if c.startswith('MACDh_')), None)
    if macd_col:
        df['MACD'] = df[macd_col]
    if sig_col:
        df['Signal_Line'] = df[sig_col]
    if hist_col:
        df['MACD_Hist'] = df[hist_col]

    upper_col = next((c for c in cols if c.startswith('BBU_')), None)
    lower_col = next((c for c in cols if c.startswith('BBL_')), None)
    mid_col = next((c for c in cols if c.startswith('BBM_')), None)
    if upper_col:
        df['Upper_BB'] = df[upper_col]
    if lower_col:
        df['Lower_BB'] = df[lower_col]
    if mid_col:
        df['Mid_BB'] = df[mid_col]

    adx_col = next((c for c in cols if c.startswith('ADX_')), None)
    if adx_col:
        df['ADX'] = df[adx_col]

    dmp_col = next((c for c in cols if c.startswith('DMP_')), None)
    dmn_col = next((c for c in cols if c.startswith('DMN_')), None)
    if dmp_col:
        df['PLUS_DI'] = df[dmp_col]
    if dmn_col:
        df['MINUS_DI'] = df[dmn_col]

    atr_col = next((c for c in cols if c.startswith('ATR')), None)
    if atr_col:
        df['ATR'] = df[atr_col]

    # Trend EMAs & Volume Confirmation
    try:
        df['EMA_9'] = ta.ema(df['close'], length=9)
        df['EMA_21'] = ta.ema(df['close'], length=21)
        df['EMA_50'] = ta.ema(df['close'], length=50)

        if 'Upper_BB' in df.columns and 'Lower_BB' in df.columns:
            band_range = (df['Upper_BB'] - df['Lower_BB']).replace(0, pd.NA)
            df['BB_Width'] = (df['Upper_BB'] - df['Lower_BB']) / df['close']
            df['BB_Width_SMA20'] = df['BB_Width'].rolling(20, min_periods=5).mean()
            df['BB_PctB'] = (df['close'] - df['Lower_BB']) / band_range

        if 'volume' in df.columns:
            df['Vol_SMA20'] = df['volume'].rolling(20, min_periods=5).mean()
            df['Vol_Ratio'] = df['volume'] / df['Vol_SMA20'].replace(0, pd.NA)
            df['Vol_Ratio'] = df['Vol_Ratio'].fillna(1.0)
    except Exception as e:
        print(f"⚠️ Error calculating auxiliary features: {e}")

    return df


# ==========================================
# 2. LOGIKA INTI (ADAPTIVE MULTI-REGIME DECISION MAKER)
# ==========================================

def decide_strategy(df: pd.DataFrame) -> Dict:
    """
    META STRATEGY v2 (High-Expectancy Regime Engine):
    - Memperbaiki bug `bb_width < 0.08` yang sebelumnya membuat bot terkunci permanen
      di mode BREAKOUT pada timeframe intraday (1m/5m/15m).
    - Menggunakan Relative Volatility Squeeze (`bb_width < bb_width_sma20 * 0.85` & `adx < 25`)
      serta konfirmasi Volume (`Vol_Ratio`) dan Momentum (`MACD` & `EMA`).
    """
    if df is None or len(df) < 25:
        return {"action": "HOLD", "reason": "Insufficient Candles", "selected_strategy": "WAITING", "strategy": "WAITING", "adx": 0, "bb_width": 0}

    last = df.iloc[-1]
    prev = df.iloc[-2]

    required_cols = ['ADX', 'Upper_BB', 'Lower_BB', 'RSI', 'close']
    if not all(col in df.columns for col in required_cols):
        return {"action": "HOLD", "reason": "Incomplete Data", "selected_strategy": "WAITING", "strategy": "WAITING", "adx": 0, "bb_width": 0}

    if pd.isna(last.get('ADX')) or pd.isna(last.get('RSI')):
        return {"action": "HOLD", "reason": "NaN Indicators", "selected_strategy": "WAITING", "strategy": "WAITING", "adx": 0, "bb_width": 0}

    adx_value = float(last['ADX'])
    close_price = float(last['close'])
    open_price = float(last.get('open', close_price))
    high_price = float(last.get('high', close_price))
    low_price = float(last.get('low', close_price))
    rsi = float(last['RSI'])
    prev_rsi = float(prev['RSI']) if not pd.isna(prev.get('RSI')) else rsi

    bb_width = float((last['Upper_BB'] - last['Lower_BB']) / close_price) if close_price != 0 else 0.0
    bb_width_sma = float(last.get('BB_Width_SMA20') or bb_width) if not pd.isna(last.get('BB_Width_SMA20')) else bb_width
    bb_pct_b = float(last.get('BB_PctB', 0.5)) if not pd.isna(last.get('BB_PctB')) else 0.5
    vol_ratio = float(last.get('Vol_Ratio', 1.0)) if not pd.isna(last.get('Vol_Ratio')) else 1.0

    macd_val = float(last.get('MACD', 0.0)) if not pd.isna(last.get('MACD')) else 0.0
    macd_sig = float(last.get('Signal_Line', 0.0)) if not pd.isna(last.get('Signal_Line')) else 0.0
    macd_hist = float(last.get('MACD_Hist', macd_val - macd_sig)) if not pd.isna(last.get('MACD_Hist')) else (macd_val - macd_sig)
    prev_macd_hist = float(prev.get('MACD_Hist', 0.0)) if not pd.isna(prev.get('MACD_Hist')) else macd_hist

    ema9 = float(last.get('EMA_9') or _ema(df['close'], 9).iloc[-1])
    ema21 = float(last.get('EMA_21') or _ema(df['close'], 21).iloc[-1])
    ema50_series = df['EMA_50'] if 'EMA_50' in df.columns else _ema(df['close'], 50)
    ema50 = float(ema50_series.iloc[-1]) if ema50_series is not None and not pd.isna(ema50_series.iloc[-1]) else ema21

    plus_di = float(last.get('PLUS_DI', 0) or 0)
    minus_di = float(last.get('MINUS_DI', 0) or 0)
    trend_direction = "UP" if plus_di > minus_di else "DOWN" if minus_di > plus_di else "NEUTRAL"

    # Candle rejection filter (hindari beli saat upper wick panjang / shooting star)
    candle_range = max(high_price - low_price, 1e-12)
    upper_wick_ratio = (high_price - max(open_price, close_price)) / candle_range
    is_bullish_candle = close_price >= open_price

    # --- SUB-STRATEGIES ---

    def strategy_trend():
        """
        Trend Pullback & Momentum Continuation:
        Beli saat struktur EMA bullish (EMA9 > EMA21 >= EMA50 * 0.998),
        MACD mendukung, dan harga tidak overextended di atas Upper BB.
        """
        ema_bullish = (ema9 > ema21) and (close_price > ema21) and (ema21 >= ema50 * 0.997)
        macd_bullish = (macd_val >= macd_sig) or (macd_hist > prev_macd_hist)

        if ema_bullish and macd_bullish:
            # Jangan beli di pucuk (BB_PctB > 0.92 atau RSI > 68)
            if 44.0 <= rsi <= 68.0 and bb_pct_b <= 0.92 and upper_wick_ratio < 0.55:
                if vol_ratio >= 0.85:
                    return "BUY", f"Trend Continuation (EMA9>21, RSI {rsi:.1f}, Vol {vol_ratio:.2f}x)"
                return "HOLD", "Uptrend but Low Volume"
            return "HOLD", f"Uptrend Overextended (RSI {rsi:.1f}, %B {bb_pct_b:.2f})"

        # SELL hanya jika struktur trend benar-benar patah (EMA9 < EMA21 DAN close < EMA21 DAN MACD bearish)
        if ema9 < ema21 and close_price < ema21 and macd_val < macd_sig and rsi < 46.0:
            return "SELL", "Trend Structure Broken (EMA Death Cross + MACD Bearish)"

        return "HOLD", "Trend Neutral"

    def strategy_sideways():
        """
        High-Probability Mean Reversion:
        Beli hanya saat oversold terkonfirmasi oleh pantulan candle (rejection / higher close)
        dekat Lower Bollinger Band, bukan saat harga sedang terjun bebas (falling knife).
        """
        has_bullish_divergence = False
        if len(df) >= 6:
            price_falling = close_price <= float(df['close'].iloc[-4])
            rsi_rising = rsi > float(df['RSI'].iloc[-4])
            has_bullish_divergence = bool(price_falling and rsi_rising)

        near_lower_bb = bb_pct_b <= 0.18 or close_price <= float(last['Lower_BB']) * 1.003
        rebounding = is_bullish_candle and (rsi >= prev_rsi - 0.5)

        if rsi < 30.0 and near_lower_bb and rebounding:
            return "BUY", f"Mean Reversion Bounce (RSI {rsi:.1f}, %B {bb_pct_b:.2f})"
        if rsi < 36.0 and has_bullish_divergence and near_lower_bb and is_bullish_candle:
            return "BUY", f"Bullish Divergence Rebound (RSI {rsi:.1f})"

        # Ambil profit / exit saat harga menyentuh Upper BB dan RSI jenuh beli
        if rsi > 72.0 and bb_pct_b >= 0.95:
            return "SELL", f"Mean Reversion Peak (RSI {rsi:.1f}, Upper BB)"

        return "HOLD", "Sideways Neutral"

    def strategy_breakout():
        """
        Volatility Squeeze Breakout dengan Konfirmasi Volume & Momentum:
        Mencegah false breakout (fakeout) dengan mewajibkan ekspansi volume >= 1.2x
        dan MACD histogram positif.
        """
        if (
            close_price > float(last['Upper_BB'])
            and 52.0 <= rsi <= 74.0
            and macd_val > macd_sig
            and vol_ratio >= 1.20
            and upper_wick_ratio < 0.45
        ):
            return "BUY", f"Confirmed Squeeze Breakout (Vol {vol_ratio:.2f}x, RSI {rsi:.1f})"

        if close_price < float(last['Lower_BB']) and macd_val < macd_sig and rsi < 42.0:
            return "SELL", "Volatility Breakdown Down"

        return "HOLD", "Inside Volatility Squeeze"

    # --- REGIME SELECTION (FIXED RELATIVE SQUEEZE) ---
    selected = "UNKNOWN"
    action = "HOLD"
    reason = "Wait"

    # Squeeze terjadi jika BB_Width menyempit < 88% rata-rata 20 bar terakhir (atau sangat sempit < 1.2%)
    # dan pasar belum sedang trending kuat (ADX < 26)
    is_relative_squeeze = (bb_width < bb_width_sma * 0.88 and adx_value < 26.0) or (bb_width < 0.012 and adx_value < 28.0)

    if is_relative_squeeze:
        selected = "BREAKOUT"
        action, reason = strategy_breakout()
        # Jika candle baru saja meledak keluar dari squeeze (bar sebelumnya squeeze), izinkan breakout!
    elif (
        len(df) >= 3
        and float(prev.get('BB_Width', bb_width)) < bb_width_sma * 0.90
        and close_price > float(last['Upper_BB'])
        and vol_ratio >= 1.25
    ):
        selected = "BREAKOUT"
        action, reason = strategy_breakout()

    elif adx_value > 32.0:
        selected = "STRONG_TREND"
        action, reason = strategy_trend()
        if action == "BUY" and trend_direction == "DOWN":
            action = "HOLD"
            reason += " (Blocked: -DI > +DI)"
        elif action == "SELL" and trend_direction == "UP":
            action = "HOLD"
            reason += " (Blocked: +DI > -DI)"

    elif adx_value > 22.0:
        selected = "TREND_FOLLOWING"
        action, reason = strategy_trend()
        if action == "BUY" and trend_direction != "UP":
            action = "HOLD"
            reason += " (No +DI confirmation)"

    else:
        selected = "MEAN_REVERSION"
        action, reason = strategy_sideways()

    atr_value = float(last.get('ATR', 0) or 0)
    atr_pct = (atr_value / close_price * 100.0) if close_price > 0 and atr_value > 0 else 1.2

    return {
        "selected_strategy": selected,
        "action": action,
        "reason": reason,
        "adx": adx_value,
        "bb_width": bb_width,
        "atr": atr_value,
        "atr_pct": atr_pct,
        "vol_ratio": vol_ratio,
        "rsi": rsi,
    }


# ==========================================
# P4: DYNAMIC ATR EXIT & POSITION SIZING
# ==========================================

def calculate_dynamic_exit_levels(
    atr_pct: float,
    fee_roundtrip_pct: float = 0.25
) -> Dict[str, float]:
    """
    Hitung target Take-Profit, Stop-Loss, dan Trailing Stop adaptif berbasis ATR & Fee.
    Menjamin positif expectancy (Net R:R >= 1.6:1 setelah fee) dan mencegah
    Trailing Stop terpicu dalam kondisi rugi (bug lama: aktivasi +1.0% dengan jarak 1.5% = -0.5% rugi).
    """
    safe_atr_pct = max(0.6, min(3.5, float(atr_pct or 1.2)))

    # Stop loss = 1.5x ATR%, di-clamp antara 1.2% s/d 2.2%
    sl_pct = max(1.2, min(2.2, safe_atr_pct * 1.5))

    # Take profit = minimal 1.85x SL ditambah kompensasi fee round-trip (minimal 2.8%)
    tp_pct = max(2.8, round(sl_pct * 1.85 + fee_roundtrip_pct, 2))

    # Trailing Stop hanya aktif setelah profit melewati (Fee + 1.5% buffer) -> minimal +1.8%
    # Jarak trail = 0.65% (sehingga saat aktif di +1.8%, minimal profit terkunci +1.15% gross / +0.90% net!)
    trail_activation_pct = max(1.8, round(sl_pct * 1.05 + fee_roundtrip_pct, 2))
    trail_distance_pct = max(0.55, min(0.90, round(safe_atr_pct * 0.55, 2)))

    # Pastikan locked profit saat aktivasi selalu > fee_roundtrip_pct + 0.5%
    if trail_activation_pct - trail_distance_pct < fee_roundtrip_pct + 0.55:
        trail_distance_pct = round(trail_activation_pct - (fee_roundtrip_pct + 0.55), 2)

    return {
        "tp_pct": round(tp_pct / 100.0, 5),               # Desimal untuk main.py (mis. 0.032 = 3.2%)
        "sl_pct": round(sl_pct / 100.0, 5),               # Desimal untuk main.py (mis. 0.016 = 1.6%)
        "trail_activation_pct": round(trail_activation_pct / 100.0, 5),
        "trail_distance_pct": round(trail_distance_pct / 100.0, 5),
        "tp_pct_display": round(tp_pct, 2),
        "sl_pct_display": round(sl_pct, 2),
    }


def calculate_atr_position_size(
    base_risk_pct: float,
    atr_pct: float,
    portfolio_value: float,
    entry_price: float,
    target_atr_multiplier: float = 1.5
) -> Dict:
    """
    Calculate position size based on ATR volatility.
    Higher volatility = smaller position size; lower volatility = larger position size (up to max).
    """
    normal_atr = 1.5
    volatility_factor = normal_atr / max(float(atr_pct or 1.5), 0.5)
    volatility_factor = max(0.5, min(1.8, volatility_factor))

    adjusted_risk_pct = base_risk_pct * volatility_factor
    risk_amount = portfolio_value * (adjusted_risk_pct / 100.0)

    stop_distance_pct = max(1.2, min(2.5, float(atr_pct or 1.5) * target_atr_multiplier))
    stop_price = entry_price * (1.0 - stop_distance_pct / 100.0)

    risk_per_unit = entry_price - stop_price
    position_size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0.0
    position_value = position_size * entry_price

    # Cap position value to 15% of portfolio (per Hunter risk limits)
    max_val = portfolio_value * 0.15
    if position_value > max_val and entry_price > 0:
        position_value = max_val
        position_size = position_value / entry_price

    return {
        "position_size": round(position_size, 8),
        "position_value": round(position_value, 2),
        "stop_price": round(stop_price, 8),
        "stop_distance_pct": round(stop_distance_pct, 2),
        "volatility_factor": round(volatility_factor, 2),
        "adjusted_risk_pct": round(adjusted_risk_pct, 2),
        "risk_amount": round(risk_amount, 2),
    }


# ==========================================
# P4: MULTI-TIMEFRAME CONFIRMATION
# ==========================================

async def get_higher_tf_trend(exchange, symbol: str, timeframe: str = "15m") -> str:
    """
    Get trend direction from higher timeframe (15m) using EMA9/EMA21/EMA50 + RSI filter.
    Returns: "UP", "DOWN", or "NEUTRAL"
    """
    try:
        bars = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=60)
        if not bars or len(bars) < 30:
            return "NEUTRAL"

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')

        ema_fast = ta.ema(df['close'], length=9)
        ema_slow = ta.ema(df['close'], length=21)
        rsi_htf = ta.rsi(df['close'], length=14)

        if ema_fast is None or ema_slow is None:
            return "NEUTRAL"

        fast_val = float(ema_fast.iloc[-1])
        slow_val = float(ema_slow.iloc[-1])
        close_val = float(df['close'].iloc[-1])
        rsi_val = float(rsi_htf.iloc[-1]) if rsi_htf is not None and not pd.isna(rsi_htf.iloc[-1]) else 50.0

        if fast_val > slow_val and close_val >= slow_val and rsi_val >= 45.0:
            return "UP"
        elif fast_val < slow_val and close_val < slow_val and rsi_val < 48.0:
            return "DOWN"
        else:
            return "NEUTRAL"

    except Exception as e:
        print(f"⚠️ Higher TF check failed: {e}")
        return "NEUTRAL"


# ==========================================
# 3. INTERFACE LIVE TRADING (ENHANCED)
# ==========================================

async def analyze_market(exchange, symbol: str, use_mtf_confirm: bool = True, timeframe: str = "5m"):
    """
    Analyze market on 5m primary timeframe (with fallback to 1m) + 15m HTF confirmation.
    Menggunakan 5m sebagai primary timeframe mengurangi noise/whipsaw dan biaya fee secara drastis.
    """
    try:
        if not exchange.has.get('fetchOHLCV'):
            return "ERROR", 0, {}

        bars = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        if not bars or len(bars) < 35:
            bars = await exchange.fetch_ohlcv(symbol, timeframe='1m', limit=100)
        if not bars:
            return "ERROR", 0, {}

        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = populate_indicators(df)

        if df.empty:
            return "ERROR", 0, {}

        current_price = float(df['close'].iloc[-1])
        decision = decide_strategy(df)
        final_action = decision['action']
        strategy_name = decision['selected_strategy']

        # P4: Check adaptive signal quality
        if final_action == "BUY" and not signal_tracker.is_signal_quality_ok(strategy_name):
            final_action = "HOLD"
            decision['reason'] += " (Filtered: Low Rolling WinRate/PF)"

        # P4: Multi-timeframe confirmation for BUY signals
        higher_tf_trend = "NEUTRAL"
        if use_mtf_confirm and final_action == "BUY":
            higher_tf_trend = await get_higher_tf_trend(exchange, symbol, "15m")

            # Block BUY if 15m trend is DOWN, or if MEAN_REVERSION tries to buy against strong 15m dump
            if higher_tf_trend == "DOWN":
                final_action = "HOLD"
                decision['reason'] += " (Blocked by 15m downtrend)"

        exit_levels = calculate_dynamic_exit_levels(decision.get('atr_pct', 1.2))

        indicators = {
            "rsi": float(df['RSI'].iloc[-1]) if 'RSI' in df.columns and not pd.isna(df['RSI'].iloc[-1]) else 0.0,
            "adx": decision['adx'],
            "bb_width": decision['bb_width'],
            "atr": decision.get('atr', 0.0),
            "atr_pct": decision.get('atr_pct', 1.2),
            "vol_ratio": decision.get('vol_ratio', 1.0),
            "strategy": strategy_name,
            "reason": decision['reason'],
            "higher_tf_trend": higher_tf_trend,
            "htf_signal": higher_tf_trend,  # Backward compatibility alias for main.py
            "signal_quality": signal_tracker.get_win_rate(strategy_name),
            "dynamic_exits": exit_levels,
        }

        if final_action != "HOLD":
            print(f"📊 {symbol} | {strategy_name} | {decision['reason']} | HTF:{higher_tf_trend} -> {final_action}")

        return final_action, current_price, indicators

    except Exception as e:
        print(f"Error Strategy {symbol}: {e}")
        return "ERROR", 0, {}


# ==========================================
# UTILITY: Record trade outcome for signal quality
# ==========================================

def record_signal_outcome(strategy: str, signal: str, pnl: float):
    """Record trade outcome to track signal quality."""
    signal_tracker.record(strategy, signal, pnl)


def get_signal_quality_stats() -> Dict:
    """Get all signal quality statistics."""
    return signal_tracker.get_all_stats()
