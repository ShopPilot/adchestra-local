"""Safety guards — budget caps, bid limits, and blocked operation enforcement."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adchestra_local.config import SafetyConfig


class SafetyViolation(Exception):
    """Raised when a proposed change violates safety constraints."""


def check_budget_cap(daily_budget: float, config: SafetyConfig) -> None:
    """Reject if proposed daily budget exceeds configured maximum."""
    if daily_budget > config.max_daily_budget:
        raise SafetyViolation(
            f"Daily budget {daily_budget:.2f} exceeds maximum {config.max_daily_budget:.2f}"
        )


def check_bid_increase(current_bid: float, proposed_bid: float, config: SafetyConfig) -> None:
    """Reject if bid increase percentage exceeds configured maximum."""
    if current_bid <= 0:
        return
    increase_pct = ((proposed_bid - current_bid) / current_bid) * 100
    if increase_pct > config.max_bid_increase_pct:
        raise SafetyViolation(
            f"Bid increase {increase_pct:.0f}% exceeds maximum {config.max_bid_increase_pct}%"
        )


def check_blocked_operation(operation: str, config: SafetyConfig) -> None:
    """Reject if operation is in the blocked list."""
    if operation in config.blocked_operations:
        raise SafetyViolation(f"Operation '{operation}' is blocked by configuration")


def requires_double_confirmation(operation: str, **kwargs: object) -> bool:
    """Return True if this operation is destructive enough to need double confirmation.

    Triggers on:
    - Any delete or remove operation
    - Budget increases >50%
    """
    if "delete" in operation or "remove" in operation:
        return True

    current = kwargs.get("current_budget")
    proposed = kwargs.get("proposed_budget")
    if isinstance(current, (int, float)) and isinstance(proposed, (int, float)) and current > 0:
        if ((proposed - current) / current) > 0.5:
            return True

    return False
