from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reported: bool = False

    @classmethod
    def from_api(cls, value: dict | None) -> "TokenUsage":
        if not value:
            return cls()
        return cls(
            prompt_tokens=value.get("prompt_tokens", 0),
            completion_tokens=value.get("completion_tokens", 0),
            total_tokens=value.get("total_tokens", 0),
            reported=True,
        )


@dataclass(frozen=True)
class CostRates:
    input_per_million: Decimal
    output_per_million: Decimal

    def calculate(self, usage: TokenUsage) -> Decimal | None:
        if not usage.reported:
            return None
        return (
            Decimal(usage.prompt_tokens) * self.input_per_million
            + Decimal(usage.completion_tokens) * self.output_per_million
        ) / Decimal(1_000_000)
