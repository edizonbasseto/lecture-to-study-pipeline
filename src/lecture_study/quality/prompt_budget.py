"""Context-window preflight for every model inference."""

from __future__ import annotations

from dataclasses import dataclass


class PromptBudgetExceededError(RuntimeError):
    """Raised before inference when the complete prompt cannot fit."""


@dataclass(frozen=True, slots=True)
class PromptBudget:
    """Exact token accounting for one model invocation."""

    input_tokens: int
    output_reserve_tokens: int
    context_window_tokens: int
    safety_margin_tokens: int = 128

    def __post_init__(self) -> None:
        if self.input_tokens < 0:
            raise ValueError("input_tokens must not be negative")
        if self.output_reserve_tokens <= 0:
            raise ValueError("output_reserve_tokens must be positive")
        if self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        if self.safety_margin_tokens < 0:
            raise ValueError("safety_margin_tokens must not be negative")

    @property
    def required_tokens(self) -> int:
        return (
            self.input_tokens + self.output_reserve_tokens + self.safety_margin_tokens
        )

    @property
    def available_tokens(self) -> int:
        return self.context_window_tokens - self.required_tokens

    @property
    def fits(self) -> bool:
        return self.required_tokens <= self.context_window_tokens

    def require_fits(self) -> None:
        if self.fits:
            return
        raise PromptBudgetExceededError(
            f"Prompt requires {self.required_tokens} tokens but the context window "
            f"contains {self.context_window_tokens}."
        )
