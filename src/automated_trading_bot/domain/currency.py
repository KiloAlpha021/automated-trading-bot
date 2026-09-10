from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Currency:
    """A canonical alphabetic currency code; membership is validated elsewhere."""

    code: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise TypeError("code must be a str")
        if len(self.code) != 3 or not all("A" <= char <= "Z" for char in self.code):
            raise ValueError("code must be exactly three ASCII uppercase letters")

    def to_string(self) -> str:
        return self.code
