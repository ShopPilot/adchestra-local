"""Tests for safety guard enforcement."""

import pytest

from adchestra_local.config import SafetyConfig
from adchestra_local.safety.guards import (
    SafetyViolation,
    check_bid_increase,
    check_blocked_operation,
    check_budget_cap,
    requires_double_confirmation,
)


@pytest.fixture
def safety_config():
    return SafetyConfig(
        max_daily_budget=50.0,
        max_bid_increase_pct=100,
        blocked_operations=["delete_campaign"],
    )


class TestBudgetCap:
    def test_allows_within_cap(self, safety_config):
        check_budget_cap(49.99, safety_config)

    def test_rejects_over_cap(self, safety_config):
        with pytest.raises(SafetyViolation, match="exceeds maximum"):
            check_budget_cap(51.0, safety_config)


class TestBidIncrease:
    def test_allows_within_limit(self, safety_config):
        check_bid_increase(1.0, 2.0, safety_config)

    def test_rejects_over_limit(self, safety_config):
        with pytest.raises(SafetyViolation, match="exceeds maximum"):
            check_bid_increase(1.0, 3.0, safety_config)

    def test_handles_zero_current_bid(self, safety_config):
        check_bid_increase(0, 5.0, safety_config)


class TestBlockedOperations:
    def test_allows_unblocked(self, safety_config):
        check_blocked_operation("pause_campaign", safety_config)

    def test_rejects_blocked(self, safety_config):
        with pytest.raises(SafetyViolation, match="blocked"):
            check_blocked_operation("delete_campaign", safety_config)


class TestDoubleConfirmation:
    def test_delete_requires_double(self):
        assert requires_double_confirmation("delete_campaign") is True

    def test_pause_does_not(self):
        assert requires_double_confirmation("pause_campaign") is False

    def test_large_budget_increase_requires_double(self):
        assert requires_double_confirmation(
            "update_budget", current_budget=10.0, proposed_budget=20.0
        ) is True

    def test_small_budget_increase_does_not(self):
        assert requires_double_confirmation(
            "update_budget", current_budget=10.0, proposed_budget=14.0
        ) is False
