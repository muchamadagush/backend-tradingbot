# File: backend/fee_calculator.py
"""
FEE CALCULATOR & SLIPPAGE ESTIMATOR
Menghitung biaya trading yang REAL untuk profit calculation yang akurat.
"""

from typing import Dict, Tuple
from dataclasses import dataclass

@dataclass
class FeeStructure:
    """
    Fee structure untuk berbagai exchange
    """
    maker_fee: float  # Fee untuk limit orders (maker)
    taker_fee: float  # Fee untuk market orders (taker)
    withdrawal_fee: float  # Fee untuk withdraw (opsional)
    name: str

# Fee structures untuk berbagai exchange populer
EXCHANGE_FEES = {
    'binance': FeeStructure(
        maker_fee=0.001,  # 0.1%
        taker_fee=0.001,  # 0.1%
        withdrawal_fee=0.0005,
        name='Binance'
    ),
    'tokocrypto': FeeStructure(
        maker_fee=0.001,  # 0.1%
        taker_fee=0.001,  # 0.1%
        withdrawal_fee=0.0005,
        name='Tokocrypto'
    ),
    'bybit': FeeStructure(
        maker_fee=0.001,  # 0.1%
        taker_fee=0.001,  # 0.1%
        withdrawal_fee=0.0005,
        name='Bybit'
    ),
    'okx': FeeStructure(
        maker_fee=0.0008,  # 0.08%
        taker_fee=0.001,   # 0.1%
        withdrawal_fee=0.0005,
        name='OKX'
    ),
    'kraken': FeeStructure(
        maker_fee=0.0016,  # 0.16%
        taker_fee=0.0026,  # 0.26%
        withdrawal_fee=0.0005,
        name='Kraken'
    )
}

class FeeCalculator:
    """
    Calculator untuk menghitung fee, slippage, dan total cost
    """
    
    def __init__(self, exchange_id='tokocrypto', slippage_pct=0.0005):
        """
        Args:
            exchange_id: ID exchange untuk ambil fee structure
            slippage_pct: Estimasi slippage dalam persen (default 0.05%)
        """
        self.exchange_id = exchange_id.lower()
        self.fee_structure = EXCHANGE_FEES.get(
            self.exchange_id, 
            EXCHANGE_FEES['binance']  # Default to Binance if not found
        )
        self.slippage_pct = slippage_pct
    
    def calculate_buy_cost(self, amount_usdt: float, price: float, 
                          order_type='market') -> Dict:
        """
        Calculate total cost untuk BUY order termasuk fee & slippage
        
        Args:
            amount_usdt: Amount in USDT to spend
            price: Current market price
            order_type: 'market' or 'limit'
        
        Returns:
            Dictionary with breakdown
        """
        # Determine fee rate
        fee_rate = self.fee_structure.taker_fee if order_type == 'market' else self.fee_structure.maker_fee
        
        # Calculate slippage (only for market orders)
        slippage_rate = self.slippage_pct if order_type == 'market' else 0
        
        # Execution price (dengan slippage, harga jadi lebih mahal)
        execution_price = price * (1 + slippage_rate)
        
        # Calculate fee
        fee_usdt = amount_usdt * fee_rate
        
        # Net amount after fee
        net_amount_usdt = amount_usdt - fee_usdt
        
        # Amount of asset received
        asset_amount = net_amount_usdt / execution_price
        
        # Effective price per asset (including all costs)
        effective_price = amount_usdt / asset_amount
        
        # Slippage cost
        slippage_cost = amount_usdt * slippage_rate
        
        return {
            'total_cost_usdt': round(amount_usdt, 2),
            'execution_price': round(execution_price, 6),
            'effective_price': round(effective_price, 6),
            'asset_amount': round(asset_amount, 8),
            'fee_usdt': round(fee_usdt, 4),
            'fee_pct': round(fee_rate * 100, 3),
            'slippage_cost': round(slippage_cost, 4),
            'slippage_pct': round(slippage_rate * 100, 3),
            'total_overhead': round(fee_usdt + slippage_cost, 4),
            'order_type': order_type
        }
    
    def calculate_sell_proceeds(self, asset_amount: float, price: float,
                               order_type='market') -> Dict:
        """
        Calculate proceeds dari SELL order termasuk fee & slippage
        
        Args:
            asset_amount: Amount of asset to sell
            price: Current market price
            order_type: 'market' or 'limit'
        
        Returns:
            Dictionary with breakdown
        """
        # Determine fee rate
        fee_rate = self.fee_structure.taker_fee if order_type == 'market' else self.fee_structure.maker_fee
        
        # Calculate slippage (only for market orders)
        slippage_rate = self.slippage_pct if order_type == 'market' else 0
        
        # Execution price (dengan slippage, harga jadi lebih murah)
        execution_price = price * (1 - slippage_rate)
        
        # Gross proceeds
        gross_proceeds = asset_amount * execution_price
        
        # Calculate fee
        fee_usdt = gross_proceeds * fee_rate
        
        # Net proceeds after fee
        net_proceeds = gross_proceeds - fee_usdt
        
        # Effective price per asset (after all costs)
        effective_price = net_proceeds / asset_amount
        
        # Slippage cost
        slippage_cost = asset_amount * price * slippage_rate
        
        return {
            'gross_proceeds': round(gross_proceeds, 2),
            'net_proceeds': round(net_proceeds, 2),
            'execution_price': round(execution_price, 6),
            'effective_price': round(effective_price, 6),
            'asset_amount': round(asset_amount, 8),
            'fee_usdt': round(fee_usdt, 4),
            'fee_pct': round(fee_rate * 100, 3),
            'slippage_cost': round(slippage_cost, 4),
            'slippage_pct': round(slippage_rate * 100, 3),
            'total_overhead': round(fee_usdt + slippage_cost, 4),
            'order_type': order_type
        }
    
    def calculate_round_trip_cost(self, amount_usdt: float, price: float) -> Dict:
        """
        Calculate total cost untuk round-trip (buy + sell)
        Ini adalah "break-even" point yang harus dicapai untuk profit
        
        Args:
            amount_usdt: Initial investment in USDT
            price: Market price
        
        Returns:
            Round-trip cost analysis
        """
        # Simulate buy
        buy_result = self.calculate_buy_cost(amount_usdt, price, 'market')
        asset_amount = buy_result['asset_amount']
        
        # Simulate sell at same price
        sell_result = self.calculate_sell_proceeds(asset_amount, price, 'market')
        net_proceeds = sell_result['net_proceeds']
        
        # Calculate loss
        total_loss = amount_usdt - net_proceeds
        loss_pct = (total_loss / amount_usdt) * 100
        
        # Breakeven price (harga yang harus naik untuk impas)
        breakeven_price = price * (1 + (loss_pct / 100))
        breakeven_rise_pct = (breakeven_price - price) / price * 100
        
        return {
            'initial_capital': round(amount_usdt, 2),
            'asset_bought': round(asset_amount, 8),
            'buy_overhead': round(buy_result['total_overhead'], 4),
            'sell_overhead': round(sell_result['total_overhead'], 4),
            'total_overhead': round(buy_result['total_overhead'] + sell_result['total_overhead'], 4),
            'final_capital': round(net_proceeds, 2),
            'total_loss': round(total_loss, 4),
            'loss_pct': round(loss_pct, 3),
            'breakeven_price': round(breakeven_price, 6),
            'breakeven_rise_pct': round(breakeven_rise_pct, 3),
            'message': f"Price must rise {breakeven_rise_pct:.2f}% to break even"
        }
    
    def calculate_profit_target(self, buy_price: float, amount: float, 
                               target_profit_pct: float) -> Dict:
        """
        Calculate target sell price untuk mencapai profit tertentu (SETELAH FEE)
        
        Args:
            buy_price: Buy price per asset
            amount: Amount of asset owned
            target_profit_pct: Desired profit in % (e.g., 5 for 5%)
        
        Returns:
            Target price analysis
        """
        # Calculate cost basis (berapa yang dikeluarkan saat beli)
        buy_result = self.calculate_buy_cost(amount * buy_price, buy_price, 'market')
        cost_basis = buy_result['total_cost_usdt']
        asset_owned = buy_result['asset_amount']
        
        # Target final capital
        target_capital = cost_basis * (1 + target_profit_pct / 100)
        
        # Reverse calculate: berapa harga jual yang dibutuhkan?
        # net_proceeds = target_capital
        # net_proceeds = gross_proceeds - fee
        # net_proceeds = (asset * sell_price * (1 - slippage)) - fee
        # net_proceeds = (asset * sell_price * (1 - slippage)) * (1 - fee_rate)
        
        fee_rate = self.fee_structure.taker_fee
        slippage_rate = self.slippage_pct
        
        # Simplified formula
        target_sell_price = target_capital / (asset_owned * (1 - slippage_rate) * (1 - fee_rate))
        
        # Verify with actual calculation
        verify_sell = self.calculate_sell_proceeds(asset_owned, target_sell_price, 'market')
        actual_profit = verify_sell['net_proceeds'] - cost_basis
        actual_profit_pct = (actual_profit / cost_basis) * 100
        
        price_rise_pct = ((target_sell_price - buy_price) / buy_price) * 100
        
        return {
            'buy_price': round(buy_price, 6),
            'target_profit_pct': target_profit_pct,
            'target_sell_price': round(target_sell_price, 6),
            'price_rise_needed': round(price_rise_pct, 2),
            'cost_basis': round(cost_basis, 2),
            'target_proceeds': round(target_capital, 2),
            'actual_profit': round(actual_profit, 2),
            'actual_profit_pct': round(actual_profit_pct, 2),
            'asset_amount': round(asset_owned, 8),
            'message': f"Need {price_rise_pct:.2f}% price rise for {target_profit_pct}% net profit"
        }
    
    def calculate_stop_loss_price(self, buy_price: float, amount: float,
                                  max_loss_pct: float) -> Dict:
        """
        Calculate stop-loss price untuk batasi kerugian
        
        Args:
            buy_price: Buy price per asset
            amount: Amount of asset owned
            max_loss_pct: Maximum acceptable loss in % (e.g., 5 for -5%)
        
        Returns:
            Stop-loss price analysis
        """
        # Calculate cost basis
        buy_result = self.calculate_buy_cost(amount * buy_price, buy_price, 'market')
        cost_basis = buy_result['total_cost_usdt']
        asset_owned = buy_result['asset_amount']
        
        # Maximum acceptable final capital
        min_capital = cost_basis * (1 - max_loss_pct / 100)
        
        # Calculate stop-loss price
        fee_rate = self.fee_structure.taker_fee
        slippage_rate = self.slippage_pct
        
        stop_loss_price = min_capital / (asset_owned * (1 - slippage_rate) * (1 - fee_rate))
        
        # Verify
        verify_sell = self.calculate_sell_proceeds(asset_owned, stop_loss_price, 'market')
        actual_loss = verify_sell['net_proceeds'] - cost_basis
        actual_loss_pct = (actual_loss / cost_basis) * 100
        
        price_drop_pct = ((stop_loss_price - buy_price) / buy_price) * 100
        
        return {
            'buy_price': round(buy_price, 6),
            'max_loss_pct': max_loss_pct,
            'stop_loss_price': round(stop_loss_price, 6),
            'price_drop_limit': round(abs(price_drop_pct), 2),
            'cost_basis': round(cost_basis, 2),
            'min_proceeds': round(min_capital, 2),
            'actual_loss': round(actual_loss, 2),
            'actual_loss_pct': round(actual_loss_pct, 2),
            'asset_amount': round(asset_owned, 8),
            'message': f"Set stop-loss at {stop_loss_price:.6f} ({price_drop_pct:.2f}% drop)"
        }

# Convenience function
def get_calculator(exchange_id='tokocrypto', slippage_pct=0.0005):
    """
    Get a fee calculator instance
    """
    return FeeCalculator(exchange_id, slippage_pct)

# Example usage
if __name__ == "__main__":
    calc = FeeCalculator('tokocrypto')
    
    print("=" * 60)
    print("FEE CALCULATOR DEMO")
    print("=" * 60)
    
    # Example 1: Buy cost
    print("\n1. BUY $100 worth of BTC at $50,000")
    buy = calc.calculate_buy_cost(100, 50000)
    print(f"   Asset received: {buy['asset_amount']:.8f} BTC")
    print(f"   Fee: ${buy['fee_usdt']:.2f}")
    print(f"   Total overhead: ${buy['total_overhead']:.2f}")
    
    # Example 2: Round-trip cost
    print("\n2. ROUND-TRIP COST (Buy + Sell at same price)")
    rt = calc.calculate_round_trip_cost(100, 50000)
    print(f"   Total loss: ${rt['total_loss']:.2f} ({rt['loss_pct']:.2f}%)")
    print(f"   {rt['message']}")
    
    # Example 3: Profit target
    print("\n3. PROFIT TARGET (Want 5% profit)")
    pt = calc.calculate_profit_target(50000, 0.002, 5)
    print(f"   {pt['message']}")
    print(f"   Target sell price: ${pt['target_sell_price']:.2f}")
    
    # Example 4: Stop-loss
    print("\n4. STOP-LOSS (Max 3% loss)")
    sl = calc.calculate_stop_loss_price(50000, 0.002, 3)
    print(f"   {sl['message']}")
    
    print("\n" + "=" * 60)
