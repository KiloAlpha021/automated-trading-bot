from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("value must be a Decimal")
