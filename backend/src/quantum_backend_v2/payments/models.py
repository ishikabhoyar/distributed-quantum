"""Rail-agnostic payment events and the internal USDC credit ledger (#3).

Every payment rail (x402, cards, Zcash, swaps, on-chain transfers) records a
``PaymentEvent``. Events that move value into the platform's internal credit
system also append ``LedgerEntry`` rows. Balances are never stored as the
source of truth: they are derived by summing an account's entries.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

LEDGER_ASSET = "USDC"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PaymentRail(str, Enum):
    """How the money moved."""

    X402 = "x402"
    CARD = "card"
    # USDC and other stablecoins paid through Stripe; Stripe settles in USD.
    STABLECOIN = "stablecoin"
    # Stripe Connect Transfer to a worker's Express account (#111 off-ramp).
    CONNECT = "connect"
    ZCASH = "zcash"
    ONCHAIN = "onchain"
    SWAP = "swap"


class PaymentPurpose(str, Enum):
    """What the money was for."""

    JOB = "job"
    PROPOSAL_FUNDING = "proposal_funding"
    STORAGE = "storage"
    CREDIT_PURCHASE = "credit_purchase"
    WORKER_PAYOUT = "worker_payout"
    ESCROW_RELEASE = "escrow_release"
    TRANSFER = "transfer"


class PaymentEventStatus(str, Enum):
    """Lifecycle of a payment event.

    ``uncertain`` means we cannot tell whether value moved. It is only left
    through an explicit admin reconciliation, never by an automatic retry.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    FAILED = "failed"
    REFUNDED = "refunded"


# Statuses an event may move to from each status.
ALLOWED_TRANSITIONS: dict[PaymentEventStatus, frozenset[PaymentEventStatus]] = {
    PaymentEventStatus.PENDING: frozenset(
        {PaymentEventStatus.CONFIRMED, PaymentEventStatus.UNCERTAIN, PaymentEventStatus.FAILED}
    ),
    PaymentEventStatus.UNCERTAIN: frozenset(
        {PaymentEventStatus.CONFIRMED, PaymentEventStatus.FAILED}
    ),
    PaymentEventStatus.CONFIRMED: frozenset({PaymentEventStatus.REFUNDED}),
    PaymentEventStatus.FAILED: frozenset(),
    PaymentEventStatus.REFUNDED: frozenset(),
}


class StatusChange(BaseModel):
    """One recorded status transition of a payment event."""

    model_config = ConfigDict(frozen=True)

    status: PaymentEventStatus
    at: datetime
    reason: str | None = None
    actor: str | None = None


class PaymentEvent(BaseModel):
    """A single payment on any rail, keyed by a caller-chosen idempotency key."""

    model_config = ConfigDict(frozen=True)

    idempotency_key: str = Field(min_length=1, max_length=256)
    rail: PaymentRail
    purpose: PaymentPurpose
    asset: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    status: PaymentEventStatus = PaymentEventStatus.PENDING
    chain_id: str | None = Field(default=None, description="CAIP-2 chain id, when on-chain.")
    payer_ref: str | None = None
    payee_ref: str | None = None
    job_id: str | None = None
    proposal_id: str | None = None
    external_ref: str | None = Field(
        default=None, description="Tx hash, Stripe event or session id, memo, ..."
    )
    ledger_account_id: str | None = Field(
        default=None,
        description="Internal credit account to credit when the event is confirmed.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    status_history: tuple[StatusChange, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    confirmed_at: datetime | None = None


class LedgerEntryType(str, Enum):
    """Kinds of credit-ledger entries.

    credit:  value enters an account (available += amount)
    hold:    value is reserved for a job (available -= amount, held += amount)
    capture: reserved value is spent (held -= amount)
    release: reserved value is returned (held -= amount, available += amount)
    """

    CREDIT = "credit"
    HOLD = "hold"
    CAPTURE = "capture"
    RELEASE = "release"


class LedgerEntry(BaseModel):
    """Append-only credit-ledger row. Entries are never updated or deleted."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    idempotency_key: str = Field(min_length=1, max_length=256)
    account_id: str = Field(min_length=1)
    entry_type: LedgerEntryType
    amount: Decimal = Field(gt=0)
    asset: str = LEDGER_ASSET
    hold_id: str | None = None
    payment_event_key: str | None = None
    job_id: str | None = None
    memo: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CreditBalance(BaseModel):
    """An account's balance, derived from its ledger entries."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    asset: str = LEDGER_ASSET
    available: Decimal = Decimal("0")
    held: Decimal = Decimal("0")
    spent: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.available + self.held


def derive_balance(account_id: str, entries: Iterable[LedgerEntry]) -> CreditBalance:
    """Sum ledger entries into a balance. This is the definition of a balance."""
    available = held = spent = Decimal("0")
    for entry in entries:
        if entry.account_id != account_id:
            raise ValueError("entry belongs to a different account")
        if entry.entry_type is LedgerEntryType.CREDIT:
            available += entry.amount
        elif entry.entry_type is LedgerEntryType.HOLD:
            available -= entry.amount
            held += entry.amount
        elif entry.entry_type is LedgerEntryType.CAPTURE:
            held -= entry.amount
            spent += entry.amount
        elif entry.entry_type is LedgerEntryType.RELEASE:
            held -= entry.amount
            available += entry.amount
    return CreditBalance(account_id=account_id, available=available, held=held, spent=spent)


class BalanceDrift(BaseModel):
    """A balance guard that disagrees with the ledger it caches."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    ledger: CreditBalance
    guard_available: Decimal
    guard_held: Decimal


class ReconciliationReport(BaseModel):
    """Result of one reconciliation sweep."""

    model_config = ConfigDict(frozen=True)

    marked_uncertain: tuple[str, ...] = ()
    drifted_accounts: tuple[BalanceDrift, ...] = ()
    ran_at: datetime = Field(default_factory=utc_now)
