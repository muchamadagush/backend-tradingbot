# File: backend/backtesting.py
import ccxt.async_support as ccxt
import pandas as pd
import asyncio
from datetime import datetime
import strategy
import json

class BacktestEngine:
    def __init__(self, exchange_id='tokocrypto', initial_capital=1000.0, 
                 enable_risk_manager=True, trade_amount_usdt=12.0):
        self.exchange_id = exchange_id
        self.initial_capital = initial_capital
        self.enable_risk_manager = enable_risk_manager
        self.trade_amount_usdt = trade_amount_usdt
        self.trading_fee = 0.001
        self.slippage_pct = 0.0005
        
    async def fetch_historical_data(self, symbol, timeframe='1h', days_back=30):
        exchange = getattr(ccxt, self.exchange_id)({'enableRateLimit': True})
        try:
            await exchange.load_markets()
            tf_minutes = {'1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440}
            minutes = tf_minutes.get(timeframe, 60)
            limit = int((days_back * 24 * 60) / minutes)
            limit = min(limit, 1000) 
            
            print(f"📊 Fetching {limit} candles of {symbol}...")
            ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not ohlcv: return None

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            print(f"✅ Loaded {len(df)} candles.")
            await exchange.close()
            return df
        except Exception as e:
            print(f"❌ Error fetching data: {e}")
            await exchange.close()
            return None
    
    async def run_backtest(self, symbol, timeframe='1h', days_back=30, 
                          take_profit_pct=5, stop_loss_pct=2.5):
        print(f"\n{'='*60}")
        print(f"🔬 BACKTESTING: {symbol} (Strategy: ADX Switch)")
        print(f"💰 Capital: ${self.initial_capital} | TP: {take_profit_pct}% | SL: {stop_loss_pct}%")
        print(f"{'='*60}\n")
        
        df = await self.fetch_historical_data(symbol, timeframe, days_back)
        if df is None or len(df) < 50: return {"error": "Insufficient data"}
        
        # 1. HITUNG INDIKATOR
        try:
            df = strategy.populate_indicators(df)
        except Exception as e:
            return {"error": f"Indicator Error: {str(e)}"}
        
        capital = self.initial_capital
        position = None 
        trades = []
        equity_curve = [capital]
        
        # 2. LOOP DATA
        start_idx = 50 
        
        # --- DEBUG: Print Data Sample (ANTI-CRASH) ---
        print("🔍 DEBUG: Checking Indicators...")
        cols_to_check = ['timestamp', 'close', 'RSI', 'ADX', 'Upper_BB', 'Lower_BB']
        existing_cols = [c for c in cols_to_check if c in df.columns]
        print(df[existing_cols].iloc[start_idx:start_idx+3])
        
        if len(existing_cols) < len(cols_to_check):
            missing = list(set(cols_to_check) - set(existing_cols))
            print(f"⚠️ WARNING: Missing columns: {missing}")
            # Jangan return error, lanjut saja agar kita tau performanya
        print("-" * 30)

        for i in range(start_idx, len(df)):
            current_row = df.iloc[:i+1]
            current_bar = current_row.iloc[-1]
            current_price = current_bar['close']
            current_time = current_bar['timestamp']
            
            # --- CEK EXIT ---
            if position:
                entry_price = position['entry_price']
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                
                exit_reason = None
                if pnl_pct >= take_profit_pct: exit_reason = "TAKE_PROFIT"
                elif pnl_pct <= -stop_loss_pct: exit_reason = "STOP_LOSS"
                
                if not exit_reason:
                    decision = strategy.decide_strategy(current_row)
                    if decision['action'] == "SELL":
                        exit_reason = f"STRATEGY_SELL ({decision['selected_strategy']})"

                if exit_reason:
                    sell_price = current_price * (1 - self.slippage_pct)
                    profit_raw = (sell_price - entry_price) * position['amount']
                    fee = sell_price * position['amount'] * self.trading_fee
                    net_profit = profit_raw - fee
                    capital += (position['amount'] * sell_price) - fee
                    
                    trades.append({
                        'entry_time': position['entry_time'],
                        'exit_time': current_time,
                        'entry_price': entry_price,
                        'exit_price': sell_price,
                        'amount': position['amount'],
                        'profit': net_profit,
                        'profit_pct': pnl_pct,
                        'exit_reason': exit_reason,
                        'fees_paid': fee
                    })
                    print(f"🔴 SELL at {current_time} | Reason: {exit_reason} | PnL: {pnl_pct:.2f}%")
                    position = None
                    equity_curve.append(capital)
                    continue
            
            # --- CEK ENTRY ---
            else:
                decision = strategy.decide_strategy(current_row)
                action = decision['action']
                
                if action == "BUY":
                    # Debug print saat sinyal beli muncul
                    # print(f"💡 Signal: {current_time} | {decision['selected_strategy']} | {decision['reason']}")

                    trade_size = self.trade_amount_usdt if self.enable_risk_manager else capital
                    if trade_size > capital: trade_size = capital

                    if capital >= trade_size and trade_size > 5:
                        buy_price = current_price * (1 + self.slippage_pct)
                        fee = trade_size * self.trading_fee
                        cost = trade_size + fee
                        
                        if capital >= cost:
                            amount = trade_size / buy_price
                            position = {
                                'entry_price': buy_price,
                                'amount': amount,
                                'entry_time': current_time
                            }
                            capital -= cost
                            print(f"🟢 BUY at {current_time} | Price: {buy_price:.2f} | Strategy: {decision['selected_strategy']}")
        
        # Tutup posisi akhir
        if position:
            sell_price = df['close'].iloc[-1]
            profit = (sell_price - position['entry_price']) * position['amount']
            capital += (position['amount'] * sell_price)
            trades.append({'profit': profit, 'exit_reason': 'FORCE_CLOSE', 'profit_pct': 0})
            equity_curve.append(capital)

        # Hitung Statistik
        total_trades = len(trades)
        wins = len([t for t in trades if t['profit'] > 0])
        losing = len([t for t in trades if t['profit'] <= 0])
        
        total_profit = sum([t['profit'] for t in trades])
        total_fees = sum([t.get('fees_paid', 0) for t in trades])
        roi = ((capital - self.initial_capital) / self.initial_capital) * 100
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        avg_win = sum([t['profit'] for t in trades if t['profit'] > 0]) / wins if wins > 0 else 0
        avg_loss = sum([t['profit'] for t in trades if t['profit'] <= 0]) / losing if losing > 0 else 0
        
        gross_profit = sum([t['profit'] for t in trades if t['profit'] > 0])
        gross_loss = abs(sum([t['profit'] for t in trades if t['profit'] <= 0]))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

        # Hitung Max Drawdown
        max_dd = 0
        peak = equity_curve[0]
        for val in equity_curve:
            if val > peak: peak = val
            dd = (peak - val) / peak * 100
            if dd > max_dd: max_dd = dd
        
        results = {
            'status': 'completed',
            'symbol': symbol,
            'timeframe': timeframe,
            'period_days': days_back,
            'initial_capital': self.initial_capital,
            'final_capital': round(capital, 2),
            'net_profit': round(total_profit, 2),
            'roi_pct': round(roi, 2),
            'total_trades': total_trades,
            'winning_trades': wins,
            'losing_trades': losing,
            'win_rate_pct': round(win_rate, 2),
            'avg_win': round(avg_win, 2),
            'avg_loss': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
            'total_fees_paid': round(total_fees, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'trades': trades
        }
        
        print(f"📊 RESULT: ROI {roi:.2f}% | Trades: {total_trades} | WinRate: {win_rate:.1f}%")
        return results

async def batch_backtest(symbols, timeframe='1h', days_back=30, enable_risk_manager=True, trade_amount_usdt=12.0):
    engine = BacktestEngine(
        enable_risk_manager=enable_risk_manager, 
        trade_amount_usdt=trade_amount_usdt
    )
    results = []
    for sym in symbols:
        res = await engine.run_backtest(sym, timeframe, days_back)
        results.append(res)
        await asyncio.sleep(1)
    return results


# Wrapper function for API

async def run_backtest(symbol: str, strategy: str = "RSI_MACD", 
                       start_date: str = None, end_date: str = None,
                       initial_capital: float = 1000, timeframe: str = "1m",
                       take_profit_pct: float = 5, stop_loss_pct: float = 2.5):
    """Run backtest - wrapper for API endpoint."""
    engine = BacktestEngine(initial_capital=initial_capital)
    # Calculate days_back from dates if provided
    days_back = 30
    if start_date and end_date:
        try:
            start = datetime.fromisoformat(start_date)
            end = datetime.fromisoformat(end_date)
            days_back = (end - start).days
        except:
            pass
    return await engine.run_backtest(symbol, timeframe, days_back, take_profit_pct, stop_loss_pct)


if __name__ == "__main__":
    asyncio.run(BacktestEngine().run_backtest('BTC/USDT'))