from dataclasses import dataclass
from decimal import Decimal

from automated_trading_bot.domain.currency import Currency


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("amount must be a Decimal")
        if not isinstance(self.currency, Currency):
            raise TypeError("currency must be a Currency")
