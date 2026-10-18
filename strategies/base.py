# ==========================================
# File: strategies/base.py
# ==========================================

from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    name = "BASE"

    @abstractmethod
    def should_buy(self, market) -> (bool, str):
        pass

    @abstractmethod
    def should_sell(self, position, market) -> (bool, str):
        pass

    def on_buy(self, position):
        pass

    def on_sell(self, position):
        pass
