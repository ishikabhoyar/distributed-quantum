"""Connect off-ramp helper contracts (#111)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from quantum_backend_v2.payments.earnings import (
    amount_to_stripe_cents,
    earnings_account_id,
    parse_withdraw_amount,
    withdraw_event_key,
    withdraw_hold_id,
)


def test_earnings_account_is_namespaced() -> None:
    assert earnings_account_id("user-1") == "earnings:user-1"
    assert withdraw_event_key("abc") == "connect:withdraw:abc"
    assert withdraw_hold_id("abc") == "withdraw:abc"


def test_parse_withdraw_amount_and_cents() -> None:
    assert parse_withdraw_amount("10.50") == Decimal("10.50")
    assert amount_to_stripe_cents(Decimal("10.50")) == 1050
    with pytest.raises(ValueError):
        parse_withdraw_amount("10.501")
    with pytest.raises(ValueError):
        parse_withdraw_amount("0")
