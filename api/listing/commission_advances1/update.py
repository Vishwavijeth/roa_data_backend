from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from models.commission_advances.commission_advances1 import CommissionAdvance, CommissionAdvanceTransaction, CommissionAdvanceGarnishment
from models.roa_data_users import RoaDataUser
from api.auth.authentication import get_current_user
from api.listing.commission_advances1.utils import CommissionAdvanceOperation, CommissionAdvanceStatus, CommissionAdvanceTransactionType, CommissionAdvanceGarnishmentStatus
from api.listing.commission_advances1.base import UpdateCommissionAdvanceResponse, UpdateCommissionAdvanceRequest


router = APIRouter()

ZERO = Decimal("0")


def get_transaction_type(
    operation: CommissionAdvanceOperation,
    transaction_type: CommissionAdvanceTransactionType | None,
):
    if operation == CommissionAdvanceOperation.PAYMENT:
        return CommissionAdvanceTransactionType.CREDIT

    if operation in {
        CommissionAdvanceOperation.INTEREST,
        CommissionAdvanceOperation.FEE,
    }:
        return CommissionAdvanceTransactionType.DEBIT

    if operation in {
        CommissionAdvanceOperation.ADJUSTMENT,
        CommissionAdvanceOperation.AMENDMENT,
    }:
        if transaction_type is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Type is required for Adjustment and Amendment",
            )

        return transaction_type

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid operation",
    )


def get_commission_advance_garnishment(
    db: Session,
    ca_id: int,
):
    garnishment_id = db.scalar(
        select(CommissionAdvanceTransaction.garnishment_id)
        .where(
            CommissionAdvanceTransaction.ca_id == ca_id,
            CommissionAdvanceTransaction.garnishment_id.is_not(None),
        )
        .order_by(
            CommissionAdvanceTransaction.id.desc()
        )
        .limit(1)
    )

    if garnishment_id is None:
        return None

    return db.scalar(
        select(CommissionAdvanceGarnishment)
        .where(
            CommissionAdvanceGarnishment.id == garnishment_id
        )
        .with_for_update()
    )


def validate_current_garnishment_transaction(
    db: Session,
    commission_advance: CommissionAdvance,
    garnishment: CommissionAdvanceGarnishment,
):
    latest_ca_id = db.scalar(
        select(CommissionAdvanceTransaction.ca_id)
        .where(
            CommissionAdvanceTransaction.garnishment_id == garnishment.id,
            CommissionAdvanceTransaction.operation == CommissionAdvanceOperation.GARNISHMENT_BALANCE.value,
        )
        .order_by(
            CommissionAdvanceTransaction.id.desc()
        )
        .limit(1)
    )

    if (
        latest_ca_id is not None
        and latest_ca_id != commission_advance.id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This garnishment has already moved to another advance",
        )


def update_garnishment_balance(
    garnishment: CommissionAdvanceGarnishment,
    new_outstanding: Decimal,
):
    if new_outstanding < ZERO:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Garnishment balance cannot be negative",
        )

    garnishment.outstanding_amount = new_outstanding

    if new_outstanding == ZERO:
        garnishment.status = CommissionAdvanceGarnishmentStatus.SETTLED.value
        garnishment.settled_at = datetime.now(timezone.utc)


@router.patch(
    "/commission-advance-transactions/{transaction_id}",
    response_model=UpdateCommissionAdvanceResponse,
)
def update_commission_advance(
    transaction_id: int,
    payload: UpdateCommissionAdvanceRequest,
    db: Session = Depends(get_db),
    current_user: RoaDataUser = Depends(get_current_user),
):
    commission_advance = db.scalar(
        select(CommissionAdvance)
        .where(
            CommissionAdvance.id == transaction_id
        )
        .with_for_update()
    )

    if not commission_advance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Commission advance not found",
        )

    if (
        commission_advance.status
        == CommissionAdvanceStatus.WAGE_GARNISHMENT.value
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wage Garnishment advances cannot be updated",
        )

    created_transaction = None

    try:
        current_outstanding = (
            commission_advance.outstanding_amount
            or ZERO
        )

        original_amount = (
            commission_advance.original_amount
        )

        garnishment = get_commission_advance_garnishment(
            db,
            commission_advance.id,
        )

        if garnishment:
            if (
                garnishment.status
                == CommissionAdvanceGarnishmentStatus.SETTLED.value
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Wage Garnishment is already settled",
                )

            validate_current_garnishment_transaction(
                db,
                commission_advance,
                garnishment,
            )

        # ========================================================
        # COMMON FIELD UPDATES
        # ========================================================

        if payload.approved_date is not None:
            commission_advance.approved_date = (
                payload.approved_date
            )

        if payload.paid_date is not None:
            commission_advance.paid_date = (
                payload.paid_date
            )

        if payload.notes is not None:
            commission_advance.notes = payload.notes

        # ========================================================
        # STATUS = WAGE GARNISHMENT
        # ========================================================

        if (
            payload.status
            == CommissionAdvanceStatus.WAGE_GARNISHMENT
        ):
            if commission_advance.agent_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Agent ID is required",
                )

            if current_outstanding <= ZERO:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Outstanding balance must be greater than 0",
                )

            if payload.operation is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Operation is not allowed for Wage Garnishment",
                )

            # ====================================================
            # EXISTING GARNISHMENT
            #
            # This advance is already carrying a garnishment.
            # Marking it Wage Garnishment closes this advance and
            # allows the remaining balance to move forward.
            # ====================================================

            if garnishment:
                garnishment.outstanding_amount = (
                    current_outstanding
                )

                garnishment.status = (
                    CommissionAdvanceGarnishmentStatus.ACTIVE.value
                )

                created_transaction = (
                    CommissionAdvanceTransaction(
                        ca_id=commission_advance.id,
                        garnishment_id=garnishment.id,
                        operation=CommissionAdvanceOperation.WAGE_GARNISHMENT.value,
                        type=CommissionAdvanceTransactionType.STATUS.value,
                        amount=current_outstanding,
                        transaction_date=payload.transaction_date,
                        notes=payload.notes,
                        created_by=current_user.email,
                        outstanding_amount=current_outstanding,
                    )
                )

                db.add(created_transaction)

                commission_advance.status = (
                    CommissionAdvanceStatus.WAGE_GARNISHMENT.value
                )

            # ====================================================
            # FIRST WAGE GARNISHMENT
            # ====================================================

            else:
                existing_garnishment = db.scalar(
                    select(
                        CommissionAdvanceGarnishment
                    )
                    .where(
                        CommissionAdvanceGarnishment.agent_id == commission_advance.agent_id,
                        CommissionAdvanceGarnishment.status == CommissionAdvanceGarnishmentStatus.ACTIVE.value,
                        CommissionAdvanceGarnishment.outstanding_amount > ZERO,
                    )
                    .with_for_update()
                )

                if existing_garnishment:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Agent already has an active Wage Garnishment",
                    )

                garnishment = (
                    CommissionAdvanceGarnishment(
                        agent_id=commission_advance.agent_id,
                        agent_name=commission_advance.agent_name,
                        source_ca_id=commission_advance.id,
                        original_amount=current_outstanding,
                        outstanding_amount=current_outstanding,
                        status=CommissionAdvanceGarnishmentStatus.ACTIVE.value,
                        notes=payload.notes,
                    )
                )

                db.add(garnishment)
                db.flush()

                created_transaction = (
                    CommissionAdvanceTransaction(
                        ca_id=commission_advance.id,
                        garnishment_id=garnishment.id,
                        operation=CommissionAdvanceOperation.WAGE_GARNISHMENT.value,
                        type=CommissionAdvanceTransactionType.STATUS.value,
                        amount=current_outstanding,
                        transaction_date=payload.transaction_date,
                        notes=payload.notes,
                        created_by=current_user.email,
                        outstanding_amount=current_outstanding,
                    )
                )

                db.add(created_transaction)

                commission_advance.status = (
                    CommissionAdvanceStatus.WAGE_GARNISHMENT.value
                )

        # ========================================================
        # STATUS = REPLACEMENT
        #
        # Replace the associated SkySlope transaction.
        # Balance and ledger stay unchanged.
        # ========================================================

        elif (
            payload.status
            == CommissionAdvanceStatus.REPLACEMENT
        ):
            if payload.address is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Address is required for Replacement",
                )

            if payload.saleguid is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Sale GUID is required for Replacement",
                )

            if payload.operation is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Operation is not allowed for Replacement",
                )

            commission_advance.address = (
                payload.address
            )

            commission_advance.saleguid = (
                payload.saleguid
            )

            commission_advance.status = (
                CommissionAdvanceStatus.REPLACEMENT.value
            )

        # ========================================================
        # STATUS = PAID
        # ========================================================

        elif (
            payload.status
            == CommissionAdvanceStatus.PAID
        ):
            if current_outstanding > ZERO:
                if payload.amount is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Amount is required",
                    )

                if payload.amount <= ZERO:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Amount must be greater than 0",
                    )

                if payload.amount != current_outstanding:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "Paid amount must equal the "
                            "current outstanding balance"
                        ),
                    )

                if (
                    payload.operation is not None
                    and payload.operation
                    != CommissionAdvanceOperation.PAYMENT
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Operation must be Payment",
                    )

                current_amount_paid = (
                    commission_advance.amount_paid
                    or ZERO
                )

                commission_advance.amount_paid = (
                    current_amount_paid
                    + payload.amount
                )

                new_outstanding = (
                    current_outstanding
                    - payload.amount
                )

                if new_outstanding < ZERO:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Outstanding balance cannot be negative",
                    )

                commission_advance.outstanding_amount = (
                    new_outstanding
                )

                if garnishment:
                    update_garnishment_balance(
                        garnishment,
                        new_outstanding,
                    )

                created_transaction = (
                    CommissionAdvanceTransaction(
                        ca_id=commission_advance.id,
                        garnishment_id=garnishment.id if garnishment else None,
                        operation=CommissionAdvanceOperation.PAYMENT.value,
                        type=CommissionAdvanceTransactionType.CREDIT.value,
                        amount=payload.amount,
                        transaction_date=payload.transaction_date,
                        notes=payload.notes,
                        created_by=current_user.email,
                        outstanding_amount=new_outstanding,
                    )
                )

                db.add(created_transaction)

            else:
                commission_advance.outstanding_amount = (
                    ZERO
                )

                if garnishment:
                    update_garnishment_balance(
                        garnishment,
                        ZERO,
                    )

            commission_advance.status = (
                CommissionAdvanceStatus.PAID.value
            )

        # ========================================================
        # ALL OTHER STATUSES
        # ========================================================

        else:
            commission_advance.status = (
                payload.status.value
            )

            if payload.operation is not None:
                if payload.amount is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Amount is required",
                    )

                if payload.amount <= ZERO:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Amount must be greater than 0",
                    )

                transaction_type = (
                    get_transaction_type(
                        payload.operation,
                        payload.type,
                    )
                )

                new_outstanding = (
                    current_outstanding
                )

                # ====================================================
                # PAYMENT
                # ====================================================

                if (
                    payload.operation
                    == CommissionAdvanceOperation.PAYMENT
                ):
                    if (
                        payload.amount
                        > current_outstanding
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Payment exceeds outstanding balance",
                        )

                    new_outstanding = (
                        current_outstanding
                        - payload.amount
                    )

                    current_amount_paid = (
                        commission_advance.amount_paid
                        or ZERO
                    )

                    commission_advance.amount_paid = (
                        current_amount_paid
                        + payload.amount
                    )

                # ====================================================
                # INTEREST / FEE
                # ====================================================

                elif payload.operation in {
                    CommissionAdvanceOperation.INTEREST,
                    CommissionAdvanceOperation.FEE,
                }:
                    transaction_type = (
                        CommissionAdvanceTransactionType.DEBIT
                    )

                    if original_amount is None:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Original amount is required",
                        )

                    new_outstanding = (
                        current_outstanding
                        + payload.amount
                    )

                    if (
                        new_outstanding
                        > original_amount
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"{payload.operation.value} exceeds the original amount",
                        )

                # ====================================================
                # ADJUSTMENT
                # ====================================================

                elif (
                    payload.operation
                    == CommissionAdvanceOperation.ADJUSTMENT
                ):
                    if (
                        transaction_type
                        == CommissionAdvanceTransactionType.CREDIT
                    ):
                        new_outstanding = (
                            current_outstanding
                            - payload.amount
                        )

                    elif (
                        transaction_type
                        == CommissionAdvanceTransactionType.DEBIT
                    ):
                        new_outstanding = (
                            current_outstanding
                            + payload.amount
                        )

                # ====================================================
                # AMENDMENT
                # ====================================================

                elif (
                    payload.operation
                    == CommissionAdvanceOperation.AMENDMENT
                ):
                    if (
                        transaction_type
                        == CommissionAdvanceTransactionType.CREDIT
                    ):
                        new_outstanding = (
                            current_outstanding
                            - payload.amount
                        )

                    elif (
                        transaction_type
                        == CommissionAdvanceTransactionType.DEBIT
                    ):
                        new_outstanding = (
                            current_outstanding
                            + payload.amount
                        )

                # ====================================================
                # OUTSTANDING VALIDATION
                # ====================================================

                if new_outstanding < ZERO:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Outstanding balance cannot be negative",
                    )

                commission_advance.outstanding_amount = (
                    new_outstanding
                )

                # ====================================================
                # GLOBAL GARNISHMENT BALANCE
                # ====================================================

                if garnishment:
                    update_garnishment_balance(
                        garnishment,
                        new_outstanding,
                    )

                # ====================================================
                # LEDGER
                # ====================================================

                created_transaction = (
                    CommissionAdvanceTransaction(
                        ca_id=commission_advance.id,
                        garnishment_id=garnishment.id if garnishment else None,
                        operation=payload.operation.value,
                        type=transaction_type.value,
                        amount=payload.amount,
                        transaction_date=payload.transaction_date,
                        notes=payload.notes,
                        created_by=current_user.email,
                        outstanding_amount=new_outstanding,
                    )
                )

                db.add(created_transaction)

                # ====================================================
                # AUTO PAID
                # ====================================================

                if new_outstanding == ZERO:
                    commission_advance.status = (
                        CommissionAdvanceStatus.PAID.value
                    )

        db.commit()

        db.refresh(
            commission_advance
        )

        if created_transaction:
            db.refresh(
                created_transaction
            )

        return UpdateCommissionAdvanceResponse(
            commission_advance=commission_advance,
            transaction=created_transaction,
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as error:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error