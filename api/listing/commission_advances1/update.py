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


def get_transaction_type(operation: CommissionAdvanceOperation, transaction_type: CommissionAdvanceTransactionType | None):
    if operation == CommissionAdvanceOperation.PAYMENT:
        return CommissionAdvanceTransactionType.CREDIT

    if operation in {CommissionAdvanceOperation.INTEREST, CommissionAdvanceOperation.FEE}:
        return CommissionAdvanceTransactionType.DEBIT

    if operation in {CommissionAdvanceOperation.ADJUSTMENT, CommissionAdvanceOperation.AMENDMENT}:
        if transaction_type is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Type is required for Adjustment and Amendment")

        return transaction_type

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid operation")


def get_commission_advance_garnishment(db: Session, ca_id: int):
    garnishment_id = db.scalar(
        select(CommissionAdvanceTransaction.garnishment_id)
        .where(
            CommissionAdvanceTransaction.ca_id == ca_id,
            CommissionAdvanceTransaction.garnishment_id.is_not(None),
        )
        .order_by(CommissionAdvanceTransaction.id.desc())
        .limit(1)
    )

    if garnishment_id is None:
        return None

    garnishment = db.scalar(
        select(CommissionAdvanceGarnishment)
        .where(CommissionAdvanceGarnishment.id == garnishment_id)
        .with_for_update()
    )

    return garnishment


def validate_current_garnishment_transaction(db: Session, commission_advance: CommissionAdvance, garnishment: CommissionAdvanceGarnishment):
    latest_ca_id = db.scalar(
        select(CommissionAdvanceTransaction.ca_id)
        .where(
            CommissionAdvanceTransaction.garnishment_id == garnishment.id,
            CommissionAdvanceTransaction.operation == CommissionAdvanceOperation.GARNISHMENT_BALANCE.value,
        )
        .order_by(CommissionAdvanceTransaction.id.desc())
        .limit(1)
    )

    if latest_ca_id is not None and latest_ca_id != commission_advance.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This garnishment has already been carried to another commission transaction")


def update_garnishment_balance(garnishment: CommissionAdvanceGarnishment, new_outstanding: Decimal):
    if new_outstanding < ZERO:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Garnishment outstanding balance should not be less than 0")

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
        .where(CommissionAdvance.id == transaction_id)
        .with_for_update()
    )

    if not commission_advance:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Commission advance not found")

    if commission_advance.status == CommissionAdvanceStatus.WAGE_GARNISHMENT.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No further updates can be made to a commission advance after it has been moved to Wage Garnishment")

    created_transaction = None

    try:
        current_outstanding = commission_advance.outstanding_amount or ZERO
        original_amount = commission_advance.original_amount

        garnishment = get_commission_advance_garnishment(db, commission_advance.id)

        if garnishment:
            if garnishment.status == CommissionAdvanceGarnishmentStatus.SETTLED.value:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This Wage Garnishment has already been settled")

            validate_current_garnishment_transaction(db, commission_advance, garnishment)

        if payload.approved_date is not None:
            commission_advance.approved_date = payload.approved_date

        if payload.paid_date is not None:
            commission_advance.paid_date = payload.paid_date

        if payload.notes is not None:
            commission_advance.notes = payload.notes

        # ========================================================
        # STATUS = WAGE GARNISHMENT
        # ========================================================

        if payload.status == CommissionAdvanceStatus.WAGE_GARNISHMENT:
            if commission_advance.agent_id is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent ID is required to create a Wage Garnishment")

            if current_outstanding <= ZERO:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wage Garnishment cannot be created because the current outstanding amount is zero")

            if payload.operation is not None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operation should not be provided when changing status to Wage Garnishment")

            # ====================================================
            # EXISTING GARNISHMENT
            #
            # This CA is already carrying an existing garnishment.
            # Do not create another garnishment.
            #
            # Example:
            # Garnishment #1 = 4000
            # CA #2 collected 3000
            # Remaining = 1000
            #
            # Marking CA #2 Wage Garnishment means:
            # - freeze CA #2
            # - same garnishment_id = 1
            # - global outstanding stays 1000
            # - next /log carries 1000 forward
            # ====================================================

            if garnishment:
                garnishment.outstanding_amount = current_outstanding
                garnishment.status = CommissionAdvanceGarnishmentStatus.ACTIVE.value

                created_transaction = CommissionAdvanceTransaction(
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

                db.add(created_transaction)

                commission_advance.status = CommissionAdvanceStatus.WAGE_GARNISHMENT.value

            # ====================================================
            # FIRST TIME WAGE GARNISHMENT
            #
            # This CA is the source transaction.
            # Create the agent-level garnishment.
            # ====================================================

            else:
                existing_garnishment = db.scalar(
                    select(CommissionAdvanceGarnishment)
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
                        detail="This agent already has an active Wage Garnishment, but this commission advance is not linked to that garnishment",
                    )

                garnishment = CommissionAdvanceGarnishment(
                    agent_id=commission_advance.agent_id,
                    agent_name=commission_advance.agent_name,
                    source_ca_id=commission_advance.id,
                    original_amount=current_outstanding,
                    outstanding_amount=current_outstanding,
                    status=CommissionAdvanceGarnishmentStatus.ACTIVE.value,
                    notes=payload.notes,
                )

                db.add(garnishment)
                db.flush()

                created_transaction = CommissionAdvanceTransaction(
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

                db.add(created_transaction)

                commission_advance.status = CommissionAdvanceStatus.WAGE_GARNISHMENT.value

        # ========================================================
        # STATUS = PAID
        # ========================================================

        elif payload.status == CommissionAdvanceStatus.PAID:
            if current_outstanding > ZERO:
                if payload.amount is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount is required when marking the commission advance as Paid")

                if payload.amount <= ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount must be greater than 0")

                if payload.amount != current_outstanding:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Paid amount must equal the current outstanding amount. Current outstanding: {current_outstanding}, Entered amount: {payload.amount}",
                    )

                if payload.operation is not None and payload.operation != CommissionAdvanceOperation.PAYMENT:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operation must be Payment when marking the commission advance as Paid")

                current_amount_paid = commission_advance.amount_paid or ZERO
                commission_advance.amount_paid = current_amount_paid + payload.amount

                new_outstanding = current_outstanding - payload.amount

                if new_outstanding < ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Outstanding balance should not be less than 0")

                commission_advance.outstanding_amount = new_outstanding

                if garnishment:
                    update_garnishment_balance(garnishment, new_outstanding)

                created_transaction = CommissionAdvanceTransaction(
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

                db.add(created_transaction)

            else:
                commission_advance.outstanding_amount = ZERO

                if garnishment:
                    update_garnishment_balance(garnishment, ZERO)

            commission_advance.status = CommissionAdvanceStatus.PAID.value

        # ========================================================
        # ALL OTHER STATUSES
        # ========================================================

        else:
            commission_advance.status = payload.status.value

            if payload.operation is not None:
                if payload.amount is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount is required when an operation is selected")

                if payload.amount <= ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount must be greater than 0")

                transaction_type = get_transaction_type(payload.operation, payload.type)
                new_outstanding = current_outstanding

                # ====================================================
                # PAYMENT
                # ====================================================

                if payload.operation == CommissionAdvanceOperation.PAYMENT:
                    if payload.amount > current_outstanding:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Payment amount cannot exceed the current outstanding amount. Current outstanding: {current_outstanding}, Entered amount: {payload.amount}",
                        )

                    new_outstanding = current_outstanding - payload.amount

                    current_amount_paid = commission_advance.amount_paid or ZERO
                    commission_advance.amount_paid = current_amount_paid + payload.amount

                # ====================================================
                # INTEREST / FEE
                # ====================================================

                elif payload.operation in {CommissionAdvanceOperation.INTEREST, CommissionAdvanceOperation.FEE}:
                    transaction_type = CommissionAdvanceTransactionType.DEBIT

                    if original_amount is None:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Original amount is required to add Interest or Fee")

                    new_outstanding = current_outstanding + payload.amount

                    if new_outstanding > original_amount:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"{payload.operation.value} cannot be added because the outstanding amount would exceed the original amount. Current outstanding: {current_outstanding}, Entered amount: {payload.amount}, Original amount: {original_amount}",
                        )

                # ====================================================
                # ADJUSTMENT
                # ====================================================

                elif payload.operation == CommissionAdvanceOperation.ADJUSTMENT:
                    if transaction_type == CommissionAdvanceTransactionType.CREDIT:
                        new_outstanding = current_outstanding - payload.amount

                    elif transaction_type == CommissionAdvanceTransactionType.DEBIT:
                        new_outstanding = current_outstanding + payload.amount

                # ====================================================
                # AMENDMENT
                # ====================================================

                elif payload.operation == CommissionAdvanceOperation.AMENDMENT:
                    if transaction_type == CommissionAdvanceTransactionType.CREDIT:
                        new_outstanding = current_outstanding - payload.amount

                    elif transaction_type == CommissionAdvanceTransactionType.DEBIT:
                        new_outstanding = current_outstanding + payload.amount

                # ====================================================
                # VALIDATION
                # ====================================================

                if new_outstanding < ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Outstanding balance should not be less than 0")

                commission_advance.outstanding_amount = new_outstanding

                # ====================================================
                # GLOBAL GARNISHMENT BALANCE
                # ====================================================

                if garnishment:
                    update_garnishment_balance(garnishment, new_outstanding)

                # ====================================================
                # LEDGER
                # ====================================================

                created_transaction = CommissionAdvanceTransaction(
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

                db.add(created_transaction)

                # ====================================================
                # AUTO PAID
                # ====================================================

                if new_outstanding == ZERO:
                    commission_advance.status = CommissionAdvanceStatus.PAID.value

        db.commit()

        db.refresh(commission_advance)

        if created_transaction:
            db.refresh(created_transaction)

        return UpdateCommissionAdvanceResponse(
            commission_advance=commission_advance,
            transaction=created_transaction,
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error))