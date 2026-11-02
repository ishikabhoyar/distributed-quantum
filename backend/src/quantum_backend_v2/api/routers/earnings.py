"""Worker earnings balance and Connect off-ramp withdrawals (#111)."""

from __future__ import annotations

import hmac
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, status
from pydantic import BaseModel, Field

from quantum_backend_v2.api.deps.auth import CurrentUser
from quantum_backend_v2.api.errors.models import ErrorCode, PlatformException, conflict
from quantum_backend_v2.payments.earnings import (
    EarningsCredit,
    WithdrawBegin,
    WithdrawIntent,
    begin_withdraw,
    confirm_withdraw,
    credit_earnings,
    earnings_account_id,
    fail_withdraw,
    mark_withdraw_uncertain,
)
from quantum_backend_v2.payments.errors import (
    IdempotencyConflictError,
    InsufficientCreditError,
    InvalidStatusTransitionError,
    PaymentEventNotFoundError,
    PaymentLedgerError,
)
from quantum_backend_v2.payments.ledger import LedgerService
from quantum_backend_v2.payments.models import CreditBalance, PaymentEvent

_BILLING_SECRET_HEADER = "X-Billing-Internal-Secret"


class EarningsBalanceResponse(BaseModel):
    """Available worker earnings (separate from prepaid job credits)."""

    balance: CreditBalance
    account_id: str


class WithdrawBeginBody(BaseModel):
    intent_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    amount: Decimal = Field(gt=0, le=Decimal("100000"))
    method: Literal["bank", "stablecoin"] = "bank"
    connected_account_id: str = Field(min_length=1, max_length=255)


class WithdrawSettleBody(BaseModel):
    intent_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    transfer_id: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=500)


def build_earnings_router(
    *,
    ledger_service: LedgerService,
    billing_internal_secret: str | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["earnings"])

    @router.get(
        "/earnings/balance",
        response_model=EarningsBalanceResponse,
        summary="Your accrued worker earnings balance",
    )
    async def get_balance(current_user: CurrentUser) -> EarningsBalanceResponse:
        account_id = earnings_account_id(current_user.user_id)
        return EarningsBalanceResponse(
            balance=await ledger_service.balance(account_id),
            account_id=account_id,
        )

    @router.post(
        "/earnings/withdraw/begin",
        response_model=WithdrawIntent,
        summary="Hold earnings and open a Connect withdrawal intent",
    )
    async def withdraw_begin(
        body: WithdrawBeginBody,
        current_user: CurrentUser,
    ) -> WithdrawIntent:
        try:
            return await begin_withdraw(
                ledger_service,
                user_id=current_user.user_id,
                request=WithdrawBegin(
                    intent_id=body.intent_id,
                    amount=body.amount,
                    method=body.method,
                    connected_account_id=body.connected_account_id,
                ),
            )
        except InsufficientCreditError as exc:
            raise PlatformException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                error=ErrorCode.INSUFFICIENT_CREDITS,
                message=str(exc),
            ) from None
        except IdempotencyConflictError as exc:
            raise conflict(str(exc)) from None
        except ValueError as exc:
            raise PlatformException(
                status_code=status.HTTP_400_BAD_REQUEST,
                error=ErrorCode.VALIDATION_ERROR,
                message=str(exc),
            ) from None

    @router.post(
        "/earnings/withdraw/confirm",
        response_model=PaymentEvent,
        summary="Confirm a Connect Transfer and capture the earnings hold",
    )
    async def withdraw_confirm(
        body: WithdrawSettleBody,
        current_user: CurrentUser,
    ) -> PaymentEvent:
        if not body.transfer_id:
            raise PlatformException(
                status_code=status.HTTP_400_BAD_REQUEST,
                error=ErrorCode.VALIDATION_ERROR,
                message="transfer_id is required",
            )
        try:
            return await confirm_withdraw(
                ledger_service,
                user_id=current_user.user_id,
                intent_id=body.intent_id,
                transfer_id=body.transfer_id,
            )
        except (PaymentEventNotFoundError, PaymentLedgerError, InvalidStatusTransitionError) as exc:
            raise PlatformException(
                status_code=status.HTTP_400_BAD_REQUEST,
                error=ErrorCode.CONFLICT,
                message=str(exc),
            ) from None

    @router.post(
        "/earnings/withdraw/fail",
        response_model=PaymentEvent,
        summary="Fail a Connect Transfer and release the earnings hold",
    )
    async def withdraw_fail(
        body: WithdrawSettleBody,
        current_user: CurrentUser,
    ) -> PaymentEvent:
        try:
            return await fail_withdraw(
                ledger_service,
                user_id=current_user.user_id,
                intent_id=body.intent_id,
                reason=body.reason or "stripe transfer failed",
            )
        except (PaymentEventNotFoundError, PaymentLedgerError, InvalidStatusTransitionError) as exc:
            raise PlatformException(
                status_code=status.HTTP_400_BAD_REQUEST,
                error=ErrorCode.CONFLICT,
                message=str(exc),
            ) from None

    @router.post(
        "/earnings/withdraw/uncertain",
        response_model=PaymentEvent,
        summary="Mark a Connect Transfer outcome unknown; keep the hold",
    )
    async def withdraw_uncertain(
        body: WithdrawSettleBody,
        current_user: CurrentUser,
    ) -> PaymentEvent:
        try:
            return await mark_withdraw_uncertain(
                ledger_service,
                user_id=current_user.user_id,
                intent_id=body.intent_id,
                reason=body.reason or "stripe transfer outcome unknown",
            )
        except (PaymentEventNotFoundError, PaymentLedgerError, InvalidStatusTransitionError) as exc:
            raise PlatformException(
                status_code=status.HTTP_400_BAD_REQUEST,
                error=ErrorCode.CONFLICT,
                message=str(exc),
            ) from None

    if billing_internal_secret is not None:

        async def require_billing_secret(
            supplied: Annotated[str | None, Header(alias=_BILLING_SECRET_HEADER)] = None,
        ) -> None:
            if supplied is None or not hmac.compare_digest(supplied, billing_internal_secret):
                raise PlatformException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    error=ErrorCode.UNAUTHORIZED,
                    message="Invalid billing credentials.",
                )

        @router.post(
            "/internal/billing/earnings-credits",
            include_in_schema=False,
        )
        async def record_earnings_credit(
            body: EarningsCredit,
            _: None = Depends(require_billing_secret),
        ) -> dict[str, object]:
            try:
                result = await credit_earnings(ledger_service, body)
            except IdempotencyConflictError as exc:
                raise conflict(str(exc)) from None
            balance = await ledger_service.balance(earnings_account_id(body.user_id))
            return {
                "idempotency_key": result.event.idempotency_key,
                "created": result.created,
                "balance": balance,
            }

    return router
