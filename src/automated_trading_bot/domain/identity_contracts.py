"""Stage-2 domain identities without persistence or execution authority."""

from dataclasses import dataclass
import re
from uuid import UUID


_ROUNDING_POLICY_TOKEN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}",
    flags=re.ASCII,
)


@dataclass(frozen=True, slots=True)
class AggregateId:
    value: UUID

    def __post_init__(self) -> None:
        if type(self.value) is not UUID:
            raise TypeError("value must be a UUID")

    def to_string(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class RoundingPolicyId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise TypeError("value must be a str")
        if _ROUNDING_POLICY_TOKEN.fullmatch(self.value) is None:
            raise ValueError("value must be a canonical rounding-policy token")

    def to_string(self) -> str:
        return self.value
