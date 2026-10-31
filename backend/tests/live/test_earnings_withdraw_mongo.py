"""Opt-in MongoDB checks for Connect earnings off-ramp (#111).

QB2_MONGODB_TEST_URI=mongodb://127.0.0.1:27017 uv run pytest tests/live/test_earnings_withdraw_mongo.py -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient

from quantum_backend_v2.payments.earnings import (
    EarningsCredit,
    WithdrawBegin,
    begin_withdraw,
    confirm_withdraw,
    credit_earnings,
    earnings_account_id,
    fail_withdraw,
)
from quantum_backend_v2.payments.errors import InsufficientCreditError
from quantum_backend_v2.payments.ledger import LedgerService
from quantum_backend_v2.payments.models import PaymentEventStatus

pytestmark = pytest.mark.anyio


@pytest.fixture
async def ledger() -> AsyncIterator[LedgerService]:
    uri = os.getenv("QB2_MONGODB_TEST_URI")
    if not uri:
        pytest.skip("Set QB2_MONGODB_TEST_URI to opt into disposable local MongoDB tests")
    parsed = urlsplit(uri)
    if parsed.scheme != "mongodb" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("Live earnings tests accept only local MongoDB")
    client: AsyncMongoClient = AsyncMongoClient(uri, serverSelectionTimeoutMS=2000)
    database_name = f"qmesh_earnings_test_{uuid4().hex}"
    try:
        await client.admin.command("ping")
        service = LedgerService(client[database_name])
        await service.ensure_indexes()
        yield service
    finally:
        await client.drop_database(database_name)
        await client.close()


async def test_withdraw_reduces_balance_once_and_failed_transfer_restores(
    ledger: LedgerService,
) -> None:
    await credit_earnings(
        ledger,
        EarningsCredit(
            user_id="worker-1",
            amount=Decimal("25.00"),
            idempotency_key="accrual:1",
        ),
    )
    assert (await ledger.balance(earnings_account_id("worker-1"))).available == Decimal("25.00")

    intent = await begin_withdraw(
        ledger,
        user_id="worker-1",
        request=WithdrawBegin(
            intent_id="intent_ok_1",
            amount=Decimal("10.00"),
            method="bank",
            connected_account_id="acct_1",
        ),
    )
    assert intent.amount_cents == 1000
    mid = await ledger.balance(earnings_account_id("worker-1"))
    assert (mid.available, mid.held) == (Decimal("15.00"), Decimal("10.00"))

    confirmed = await confirm_withdraw(
        ledger,
        user_id="worker-1",
        intent_id="intent_ok_1",
        transfer_id="tr_1",
    )
    assert confirmed.status is PaymentEventStatus.CONFIRMED
    after = await ledger.balance(earnings_account_id("worker-1"))
    assert (after.available, after.held, after.spent) == (
        Decimal("15.00"),
        Decimal("0"),
        Decimal("10.00"),
    )

    await confirm_withdraw(
        ledger,
        user_id="worker-1",
        intent_id="intent_ok_1",
        transfer_id="tr_1",
    )
    assert (await ledger.balance(earnings_account_id("worker-1"))).spent == Decimal("10.00")

    await begin_withdraw(
        ledger,
        user_id="worker-1",
        request=WithdrawBegin(
            intent_id="intent_fail_1",
            amount=Decimal("5.00"),
            method="bank",
            connected_account_id="acct_1",
        ),
    )
    failed = await fail_withdraw(
        ledger,
        user_id="worker-1",
        intent_id="intent_fail_1",
        reason="transfer_failed",
    )
    assert failed.status is PaymentEventStatus.FAILED
    restored = await ledger.balance(earnings_account_id("worker-1"))
    assert (restored.available, restored.held) == (Decimal("15.00"), Decimal("0"))


async def test_insufficient_earnings_fails_closed(ledger: LedgerService) -> None:
    with pytest.raises(InsufficientCreditError):
        await begin_withdraw(
            ledger,
            user_id="worker-2",
            request=WithdrawBegin(
                intent_id="intent_empty",
                amount=Decimal("1.00"),
                method="bank",
                connected_account_id="acct_1",
            ),
        )
