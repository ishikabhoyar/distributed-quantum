"""Worker earnings ledger and Connect off-ramp withdrawals (#111).

Earnings use a dedicated account per worker (``earnings:{user_id}``), separate
from prepaid job credits. Withdrawals hold the balance, record a payment event,
then capture on a successful Stripe Transfer or release on a definite failure.
On-chain fragment payouts remain the default settlement rail (#8).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quantum_backend_v2.payments.errors import (
    IdempotencyConflictError,
    InsufficientCreditError,
    InvalidStatusTransitionError,
    PaymentEventNotFoundError,
    PaymentLedgerError,
)
from quantum_backend_v2.payments.ledger import LedgerService, RecordResult
from quantum_backend_v2.payments.models import (
    CreditBalance,
    PaymentEvent,
    PaymentEventStatus,
    PaymentPurpose,
    PaymentRail,
)

WithdrawMethod = Literal["bank", "stablecoin"]

_CENTS = Decimal("0.01")
_MAX_WITHDRAW = Decimal("100000")


def earnings_account_id(user_id: str) -> str:
    """Ledger account that holds accrued worker earnings (not prepaid credits)."""
    if not user_id.strip():
        raise ValueError("user_id is required")
    return f"earnings:{user_id.strip()}"


def withdraw_event_key(intent_id: str) -> str:
    return f"connect:withdraw:{intent_id}"


def withdraw_hold_id(intent_id: str) -> str:
    return f"withdraw:{intent_id}"


def parse_withdraw_amount(raw: str | Decimal) -> Decimal:
    """Parse a positive USDC amount with at most two decimal places (Stripe cents)."""
    try:
        amount = raw if isinstance(raw, Decimal) else Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("amount must be a decimal string") from exc
    if amount <= 0 or amount > _MAX_WITHDRAW:
        raise ValueError("amount must be greater than 0 and at most 100000")
    quantized = amount.quantize(_CENTS)
    if quantized != amount:
        raise ValueError("amount may have at most two decimal places")
    return quantized


def amount_to_stripe_cents(amount: Decimal) -> int:
    """Convert a two-decimal USDC amount to an integer Stripe Transfer amount."""
    cents = int(amount.quantize(_CENTS) * 100)
    if cents <= 0:
        raise ValueError("Stripe Transfer amount must be at least 1 cent")
    return cents


class EarningsCredit(BaseModel):
    """Credit accrued earnings onto a worker's earnings account (ops / #8 bridge)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(gt=0, le=_MAX_WITHDRAW, decimal_places=6)
    idempotency_key: str = Field(min_length=1, max_length=256)
    memo: str | None = Field(default=None, max_length=500)


class WithdrawBegin(BaseModel):
    """Start an idempotent Connect withdrawal intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    amount: Decimal = Field(gt=0, le=_MAX_WITHDRAW)
    method: WithdrawMethod = "bank"
    connected_account_id: str = Field(min_length=1, max_length=255)


class WithdrawIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str
    event_key: str
    hold_id: str
    amount: Decimal
    amount_cents: int
    method: WithdrawMethod
    connected_account_id: str
    created: bool
    balance: CreditBalance


async def credit_earnings(ledger: LedgerService, credit: EarningsCredit) -> RecordResult:
    """Record accrued earnings into the worker earnings account once per key."""
    account_id = earnings_account_id(credit.user_id)
    result = await ledger.record_event(
        idempotency_key=credit.idempotency_key,
        rail=PaymentRail.ONCHAIN,
        purpose=PaymentPurpose.WORKER_PAYOUT,
        amount=credit.amount,
        status=PaymentEventStatus.CONFIRMED,
        payee_ref=credit.user_id,
        ledger_account_id=account_id,
        metadata={"memo": credit.memo, "kind": "earnings_accrual"},
    )
    await ledger.confirm(result.event.idempotency_key)
    return result


async def begin_withdraw(
    ledger: LedgerService,
    *,
    user_id: str,
    request: WithdrawBegin,
) -> WithdrawIntent:
    """Open a pending Connect withdrawal event, then hold earnings.

    Replaying the same ``intent_id`` with the same fields returns the existing
    intent. A different amount or method for the same id is a conflict.
    """
    amount = parse_withdraw_amount(request.amount)
    account_id = earnings_account_id(user_id)
    event_key = withdraw_event_key(request.intent_id)
    hold_id = withdraw_hold_id(request.intent_id)

    result = await ledger.record_event(
        idempotency_key=event_key,
        rail=PaymentRail.CONNECT,
        purpose=PaymentPurpose.WORKER_PAYOUT,
        amount=amount,
        payer_ref=user_id,
        payee_ref=request.connected_account_id,
        metadata={
            "intent_id": request.intent_id,
            "hold_id": hold_id,
            "method": request.method,
            "kind": "connect_offramp",
        },
    )
    event = result.event
    if (
        event.amount != amount
        or event.payee_ref != request.connected_account_id
        or (event.metadata or {}).get("method") != request.method
    ):
        raise IdempotencyConflictError(event_key)

    if event.status is PaymentEventStatus.CONFIRMED:
        raise ValueError(f"Withdrawal {request.intent_id} already paid")
    if event.status is PaymentEventStatus.FAILED:
        raise ValueError(f"Withdrawal {request.intent_id} already failed; use a new intent id")
    if event.status is PaymentEventStatus.UNCERTAIN:
        raise ValueError(f"Withdrawal {request.intent_id} is uncertain; reconcile before retry")

    try:
        await ledger.hold(
            account_id,
            amount,
            hold_id=hold_id,
            memo=f"connect withdraw ({request.method})",
        )
    except InsufficientCreditError:
        if result.created:
            await ledger.fail(
                event_key,
                reason="insufficient earnings available when creating withdrawal",
            )
        raise

    return WithdrawIntent(
        intent_id=request.intent_id,
        event_key=event_key,
        hold_id=hold_id,
        amount=amount,
        amount_cents=amount_to_stripe_cents(amount),
        method=request.method,
        connected_account_id=request.connected_account_id,
        created=result.created,
        balance=await ledger.balance(account_id),
    )


async def confirm_withdraw(
    ledger: LedgerService,
    *,
    user_id: str,
    intent_id: str,
    transfer_id: str,
) -> PaymentEvent:
    """Confirm the event then capture the hold after Stripe reports a Transfer id."""
    event = await _require_owned_event(ledger, user_id, intent_id)
    hold_id = withdraw_hold_id(intent_id)
    capture_key = f"capture:{event.idempotency_key}"
    if event.status is not PaymentEventStatus.CONFIRMED:
        event = await ledger.confirm(event.idempotency_key, external_ref=transfer_id)
    await ledger.capture(
        hold_id,
        event.amount,
        idempotency_key=capture_key,
        memo="connect withdraw captured",
    )
    return event


async def fail_withdraw(
    ledger: LedgerService,
    *,
    user_id: str,
    intent_id: str,
    reason: str,
) -> PaymentEvent:
    """Mark a definite Transfer failure and return held earnings to available."""
    event = await _require_owned_event(ledger, user_id, intent_id)
    hold_id = withdraw_hold_id(intent_id)
    release_key = f"release:{event.idempotency_key}"
    if event.status is PaymentEventStatus.CONFIRMED:
        raise InvalidStatusTransitionError("confirmed withdrawals cannot fail")
    if event.status is not PaymentEventStatus.FAILED:
        event = await ledger.fail(event.idempotency_key, reason=reason[:500])
    await ledger.release(
        hold_id,
        event.amount,
        idempotency_key=release_key,
        memo="connect withdraw released",
    )
    return event


async def mark_withdraw_uncertain(
    ledger: LedgerService,
    *,
    user_id: str,
    intent_id: str,
    reason: str,
) -> PaymentEvent:
    """Keep the hold when the Transfer outcome is unknown (e.g. network timeout)."""
    event = await _require_owned_event(ledger, user_id, intent_id)
    if event.status is PaymentEventStatus.UNCERTAIN:
        return event
    return await ledger.mark_uncertain(event.idempotency_key, reason=reason[:500])


async def _require_owned_event(
    ledger: LedgerService,
    user_id: str,
    intent_id: str,
) -> PaymentEvent:
    event = await ledger.get_event(withdraw_event_key(intent_id))
    if event is None:
        raise PaymentEventNotFoundError(withdraw_event_key(intent_id))
    if event.payer_ref != user_id:
        raise PaymentLedgerError("withdrawal does not belong to this user")
    return event
