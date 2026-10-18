# File: backend/paper_trading.py
"""
PAPER TRADING ENGINE
Simulasi trading dengan uang virtual (monopoli) di pasar real-time.
"""

import asyncio
from datetime import datetime
from typing import Dict, Optional, List
import json
import os

class PaperTradingAccount:
    """
    Virtual trading account untuk paper trading
    """
    
    def __init__(self, initial_balance=1000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions = {}  # {symbol: {'amount': float, 'entry_price': float, 'timestamp': str}}
        self.orders_history = []
        self.trades_history = []
        self.trading_fee = 0.001  # 0.1% default
        self.slippage_pct = 0.0005  # 0.05%
        
        # Load from file if exists
        self.load_state()
    
    def get_total_equity(self, current_prices: Dict[str, float]) -> float:
        """
        Calculate total equity (balance + positions value)
        
        Args:
            current_prices: Dict of {symbol: current_price}
        
        Returns:
            Total equity in USDT
        """
        equity = self.balance
        
        for symbol, position in self.positions.items():
            current_price = current_prices.get(symbol, 0)
            if current_price > 0:
                equity += position['amount'] * current_price
        
        return equity
    
    def get_pnl(self, current_prices: Dict[str, float]) -> Dict:
        """
        Get current Profit & Loss
        """
        total_equity = self.get_total_equity(current_prices)
        total_pnl = total_equity - self.initial_balance
        pnl_pct = (total_pnl / self.initial_balance) * 100
        
        return {
            'total_equity': round(total_equity, 2),
            'balance': round(self.balance, 2),
            'total_pnl': round(total_pnl, 2),
            'pnl_pct': round(pnl_pct, 2),
            'initial_balance': self.initial_balance
        }
    
    def place_market_buy(self, symbol: str, amount_usdt: float, current_price: float) -> Dict:
        """
        Place market buy order (virtual)
        
        Fee/Slippage structure (matches real exchange):
        1. Slippage: Affects execution price (buy higher)
        2. Fee: Taken from gross trade value
        
        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            amount_usdt: Amount in USDT to buy
            current_price: Current market price
        
        Returns:
            Order result dictionary
        """
        if amount_usdt > self.balance:
            return {
                'status': 'failed',
                'reason': 'Insufficient balance',
                'required': amount_usdt,
                'available': self.balance
            }
        
        # Step 1: Apply slippage to execution price (buy at slightly higher price)
        execution_price = current_price * (1 + self.slippage_pct)
        slippage_cost = amount_usdt * self.slippage_pct  # For logging
        
        # Step 2: Calculate fee from gross amount (standard exchange behavior)
        fee = amount_usdt * self.trading_fee
        
        # Step 3: Calculate actual asset received
        # Gross USDT buys at execution_price, minus fee
        gross_asset_amount = amount_usdt / execution_price
        fee_in_asset = fee / execution_price  # Fee deducted from received asset
        asset_amount = gross_asset_amount - fee_in_asset
        
        # Update balance (full amount deducted)
        self.balance -= amount_usdt
        
        # Update or create position
        if symbol in self.positions:
            # Average up
            old_position = self.positions[symbol]
            old_value = old_position['amount'] * old_position['entry_price']
            new_value = asset_amount * execution_price
            total_amount = old_position['amount'] + asset_amount
            avg_price = (old_value + new_value) / total_amount if total_amount > 0 else execution_price
            
            self.positions[symbol] = {
                'amount': total_amount,
                'entry_price': avg_price,
                'timestamp': datetime.now().isoformat()
            }
        else:
            self.positions[symbol] = {
                'amount': asset_amount,
                'entry_price': execution_price,
                'timestamp': datetime.now().isoformat()
            }
        
        # Record order with detailed breakdown
        order = {
            'id': len(self.orders_history) + 1,
            'type': 'MARKET_BUY',
            'symbol': symbol,
            'amount_usdt': amount_usdt,
            'market_price': current_price,
            'execution_price': execution_price,
            'asset_amount': asset_amount,
            'fee': fee,
            'fee_pct': self.trading_fee * 100,
            'slippage_cost': slippage_cost,
            'slippage_pct': self.slippage_pct * 100,
            'timestamp': datetime.now().isoformat(),
            'status': 'filled'
        }
        
        self.orders_history.append(order)
        self.save_state()
        
        return {
            'status': 'success',
            'order': order,
            'new_balance': self.balance,
            'position': self.positions[symbol]
        }
    
    def place_market_sell(self, symbol: str, percentage: float, current_price: float) -> Dict:
        """
        Place market sell order (virtual)
        
        Fee/Slippage structure (matches real exchange):
        1. Slippage: Affects execution price (sell lower)
        2. Fee: Taken from gross proceeds
        
        Args:
            symbol: Trading pair
            percentage: Percentage of position to sell (0-100)
            current_price: Current market price
        
        Returns:
            Order result dictionary
        """
        if symbol not in self.positions:
            return {
                'status': 'failed',
                'reason': 'No position found',
                'symbol': symbol
            }
        
        position = self.positions[symbol]
        sell_amount = position['amount'] * (percentage / 100)
        
        if sell_amount <= 0:
            return {
                'status': 'failed',
                'reason': 'Invalid sell amount',
                'amount': sell_amount
            }
        
        # Step 1: Apply slippage to execution price (sell at slightly lower price)
        execution_price = current_price * (1 - self.slippage_pct)
        slippage_cost = sell_amount * current_price * self.slippage_pct  # For logging
        
        # Step 2: Calculate gross proceeds (before fee)
        gross_proceeds = sell_amount * execution_price
        
        # Step 3: Calculate fee from gross proceeds (standard exchange behavior)
        fee = gross_proceeds * self.trading_fee
        net_proceeds = gross_proceeds - fee
        
        # Calculate profit (using entry cost vs net proceeds)
        cost_basis = sell_amount * position['entry_price']
        profit = net_proceeds - cost_basis
        profit_pct = (profit / cost_basis) * 100 if cost_basis > 0 else 0
        
        # Update balance
        self.balance += net_proceeds
        
        # Update or remove position
        if percentage >= 100:
            del self.positions[symbol]
        else:
            self.positions[symbol]['amount'] -= sell_amount
        
        # Record order with detailed breakdown
        order = {
            'id': len(self.orders_history) + 1,
            'type': 'MARKET_SELL',
            'symbol': symbol,
            'asset_amount': sell_amount,
            'market_price': current_price,
            'execution_price': execution_price,
            'gross_proceeds': gross_proceeds,
            'proceeds': net_proceeds,
            'fee': fee,
            'fee_pct': self.trading_fee * 100,
            'slippage_cost': slippage_cost,
            'slippage_pct': self.slippage_pct * 100,
            'timestamp': datetime.now().isoformat(),
            'status': 'filled'
        }
        
        # Record trade (completed buy-sell cycle)
        trade = {
            'id': len(self.trades_history) + 1,
            'symbol': symbol,
            'entry_price': position['entry_price'],
            'exit_price': execution_price,
            'amount': sell_amount,
            'profit': profit,
            'profit_pct': profit_pct,
            'entry_time': position['timestamp'],
            'exit_time': datetime.now().isoformat(),
            'fees_paid': fee
        }
        
        self.orders_history.append(order)
        self.trades_history.append(trade)
        self.save_state()
        
        return {
            'status': 'success',
            'order': order,
            'trade': trade,
            'new_balance': self.balance,
            'position': self.positions.get(symbol)
        }
    
    def close_all_positions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """
        Close all open positions
        """
        results = []
        symbols_to_close = list(self.positions.keys())
        
        for symbol in symbols_to_close:
            price = current_prices.get(symbol, 0)
            if price > 0:
                result = self.place_market_sell(symbol, 100, price)
                results.append(result)
        
        return results
    
    def get_account_summary(self, current_prices: Dict[str, float]) -> Dict:
        """
        Get complete account summary
        """
        pnl_data = self.get_pnl(current_prices)
        
        # Calculate per-position PnL
        positions_detail = []
        for symbol, position in self.positions.items():
            current_price = current_prices.get(symbol, 0)
            if current_price > 0:
                current_value = position['amount'] * current_price
                cost_basis = position['amount'] * position['entry_price']
                pnl = current_value - cost_basis
                pnl_pct = (pnl / cost_basis) * 100
                
                positions_detail.append({
                    'symbol': symbol,
                    'amount': position['amount'],
                    'entry_price': position['entry_price'],
                    'current_price': current_price,
                    'current_value': round(current_value, 2),
                    'pnl': round(pnl, 2),
                    'pnl_pct': round(pnl_pct, 2),
                    'entry_time': position['timestamp']
                })
        
        # Trading statistics
        winning_trades = [t for t in self.trades_history if t['profit'] > 0]
        losing_trades = [t for t in self.trades_history if t['profit'] <= 0]
        
        win_rate = (len(winning_trades) / len(self.trades_history) * 100) if self.trades_history else 0
        total_profit = sum([t['profit'] for t in self.trades_history])
        total_fees = sum([o['fee'] for o in self.orders_history])
        
        return {
            **pnl_data,
            'open_positions': len(self.positions),
            'positions_detail': positions_detail,
            'total_trades': len(self.trades_history),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate_pct': round(win_rate, 2),
            'total_fees_paid': round(total_fees, 2),
            'total_orders': len(self.orders_history)
        }
    
    def reset_account(self, new_balance: Optional[float] = None):
        """
        Reset account to initial state
        """
        self.balance = new_balance if new_balance else self.initial_balance
        self.initial_balance = self.balance
        self.positions = {}
        self.orders_history = []
        self.trades_history = []
        self.save_state()
    
    def save_state(self):
        """
        Save account state to file
        """
        state = {
            'initial_balance': self.initial_balance,
            'balance': self.balance,
            'positions': self.positions,
            'orders_history': self.orders_history,
            'trades_history': self.trades_history,
            'last_updated': datetime.now().isoformat()
        }
        
        with open('paper_trading_state.json', 'w') as f:
            json.dump(state, f, indent=2)
    
    def load_state(self):
        """
        Load account state from file
        """
        if os.path.exists('paper_trading_state.json'):
            try:
                with open('paper_trading_state.json', 'r') as f:
                    state = json.load(f)
                    self.initial_balance = state.get('initial_balance', self.initial_balance)
                    self.balance = state.get('balance', self.balance)
                    self.positions = state.get('positions', {})
                    self.orders_history = state.get('orders_history', [])
                    self.trades_history = state.get('trades_history', [])
                    print(f"📂 Paper Trading state loaded. Balance: ${self.balance:.2f}")
            except Exception as e:
                print(f"⚠️ Could not load paper trading state: {e}")

# Global paper trading account instance
paper_account = PaperTradingAccount(initial_balance=1000.0)
