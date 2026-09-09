from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from models.commission_advances.commission_advances import CommissionAdvance, CommissionAdvanceTransaction, CommissionAdvanceLegalHold
from models.roa_data_users import RoaDataUser
from api.auth.authentication import get_current_user
from api.listing.commission_advances.utils import CommissionAdvanceOperation, CommissionAdvanceStatus, CommissionAdvanceTransactionType, CommissionAdvanceLegalHoldStatus
from api.listing.commission_advances.base import UpdateCommissionAdvanceResponse, UpdateCommissionAdvanceRequest


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


def get_commission_advance_legal_hold(db: Session, ca_id: int):
    legal_hold_id = db.scalar(
        select(CommissionAdvanceTransaction.legal_hold_id)
        .where(CommissionAdvanceTransaction.ca_id == ca_id, CommissionAdvanceTransaction.legal_hold_id.is_not(None))
        .order_by(CommissionAdvanceTransaction.id.desc())
        .limit(1)
    )

    if legal_hold_id is None:
        return None

    return db.scalar(
        select(CommissionAdvanceLegalHold)
        .where(CommissionAdvanceLegalHold.id == legal_hold_id)
        .with_for_update()
    )


def validate_current_legal_hold_transaction(db: Session, commission_advance: CommissionAdvance, legal_hold: CommissionAdvanceLegalHold):
    latest_ca_id = db.scalar(
        select(CommissionAdvanceTransaction.ca_id)
        .where(
            CommissionAdvanceTransaction.legal_hold_id == legal_hold.id,
            CommissionAdvanceTransaction.operation == CommissionAdvanceOperation.LEGAL_HOLD_BALANCE.value,
        )
        .order_by(CommissionAdvanceTransaction.id.desc())
        .limit(1)
    )

    if latest_ca_id is not None and latest_ca_id != commission_advance.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This legal hold has already moved to another advance")


def update_legal_hold_balance(legal_hold: CommissionAdvanceLegalHold, new_outstanding: Decimal):
    if new_outstanding < ZERO:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Legal hold balance cannot be negative")

    legal_hold.outstanding_amount = new_outstanding

    if new_outstanding == ZERO:
        legal_hold.status = CommissionAdvanceLegalHoldStatus.SETTLED.value
        legal_hold.settled_at = datetime.now(timezone.utc)


@router.patch("/commission-advance-transactions/{transaction_id}", response_model=UpdateCommissionAdvanceResponse)
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

    if commission_advance.status == CommissionAdvanceStatus.LEGAL_HOLD.value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Legal Hold advances cannot be updated")

    created_transaction = None

    try:
        current_outstanding = commission_advance.outstanding_amount or ZERO
        original_amount = commission_advance.original_amount
        legal_hold = get_commission_advance_legal_hold(db, commission_advance.id)

        if legal_hold:
            if legal_hold.status == CommissionAdvanceLegalHoldStatus.SETTLED.value:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Legal Hold is already settled")

            validate_current_legal_hold_transaction(db, commission_advance, legal_hold)

        if payload.approved_date is not None:
            commission_advance.approved_date = payload.approved_date

        if payload.paid_date is not None:
            commission_advance.paid_date = payload.paid_date

        if payload.notes is not None:
            commission_advance.notes = payload.notes

        if payload.status == CommissionAdvanceStatus.LEGAL_HOLD:
            if commission_advance.agent_id is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent ID is required")

            if current_outstanding <= ZERO:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Outstanding balance must be greater than 0")

            if payload.operation is not None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operation is not allowed for Legal Hold")

            if legal_hold:
                legal_hold.outstanding_amount = current_outstanding
                legal_hold.status = CommissionAdvanceLegalHoldStatus.ACTIVE.value

                created_transaction = CommissionAdvanceTransaction(
                    ca_id=commission_advance.id,
                    legal_hold_id=legal_hold.id,
                    operation=CommissionAdvanceOperation.LEGAL_HOLD_BALANCE.value,
                    type=CommissionAdvanceTransactionType.STATUS.value,
                    amount=current_outstanding,
                    transaction_date=payload.transaction_date,
                    notes=payload.notes,
                    created_by=current_user.email,
                    outstanding_amount=current_outstanding,
                )

                db.add(created_transaction)
                commission_advance.status = CommissionAdvanceStatus.LEGAL_HOLD.value

            else:
                existing_legal_hold = db.scalar(
                    select(CommissionAdvanceLegalHold)
                    .where(
                        CommissionAdvanceLegalHold.agent_id == commission_advance.agent_id,
                        CommissionAdvanceLegalHold.status == CommissionAdvanceLegalHoldStatus.ACTIVE.value,
                        CommissionAdvanceLegalHold.outstanding_amount > ZERO,
                    )
                    .with_for_update()
                )

                if existing_legal_hold:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent already has an active Legal Hold")

                legal_hold = CommissionAdvanceLegalHold(
                    agent_id=commission_advance.agent_id,
                    agent_name=commission_advance.agent_name,
                    source_ca_id=commission_advance.id,
                    original_amount=current_outstanding,
                    outstanding_amount=current_outstanding,
                    status=CommissionAdvanceLegalHoldStatus.ACTIVE.value,
                    notes=payload.notes,
                )

                db.add(legal_hold)
                db.flush()

                created_transaction = CommissionAdvanceTransaction(
                    ca_id=commission_advance.id,
                    legal_hold_id=legal_hold.id,
                    operation=CommissionAdvanceOperation.LEGAL_HOLD_BALANCE.value,
                    type=CommissionAdvanceTransactionType.STATUS.value,
                    amount=current_outstanding,
                    transaction_date=payload.transaction_date,
                    notes=payload.notes,
                    created_by=current_user.email,
                    outstanding_amount=current_outstanding,
                )

                db.add(created_transaction)
                commission_advance.status = CommissionAdvanceStatus.LEGAL_HOLD.value

        elif payload.status == CommissionAdvanceStatus.REPLACEMENT:
            if payload.address is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Address is required for Replacement")

            if payload.saleguid is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Sale GUID is required for Replacement")

            if payload.operation is not None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operation is not allowed for Replacement")

            commission_advance.address = payload.address
            commission_advance.saleguid = payload.saleguid
            commission_advance.status = CommissionAdvanceStatus.REPLACEMENT.value

        elif payload.status == CommissionAdvanceStatus.PAID:
            if current_outstanding > ZERO:
                if payload.amount is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount is required")

                if payload.amount <= ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount must be greater than 0")

                if payload.amount != current_outstanding:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Paid amount must equal the current outstanding balance")

                if payload.operation is not None and payload.operation != CommissionAdvanceOperation.PAYMENT:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Operation must be Payment")

                current_amount_paid = commission_advance.amount_paid or ZERO
                commission_advance.amount_paid = current_amount_paid + payload.amount
                new_outstanding = current_outstanding - payload.amount

                if new_outstanding < ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Outstanding balance cannot be negative")

                commission_advance.outstanding_amount = new_outstanding

                if legal_hold:
                    update_legal_hold_balance(legal_hold, new_outstanding)

                created_transaction = CommissionAdvanceTransaction(
                    ca_id=commission_advance.id,
                    legal_hold_id=legal_hold.id if legal_hold else None,
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

                if legal_hold:
                    update_legal_hold_balance(legal_hold, ZERO)

            commission_advance.status = CommissionAdvanceStatus.PAID.value

        else:
            commission_advance.status = payload.status.value

            if payload.operation is not None:
                if payload.amount is None:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount is required")

                if payload.amount <= ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount must be greater than 0")

                transaction_type = get_transaction_type(payload.operation, payload.type)
                new_outstanding = current_outstanding

                if payload.operation == CommissionAdvanceOperation.PAYMENT:
                    if payload.amount > current_outstanding:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment exceeds outstanding balance")

                    new_outstanding = current_outstanding - payload.amount
                    current_amount_paid = commission_advance.amount_paid or ZERO
                    commission_advance.amount_paid = current_amount_paid + payload.amount

                elif payload.operation in {CommissionAdvanceOperation.INTEREST, CommissionAdvanceOperation.FEE}:
                    transaction_type = CommissionAdvanceTransactionType.DEBIT

                    if original_amount is None:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Original amount is required")

                    new_outstanding = current_outstanding + payload.amount

                    if new_outstanding > original_amount:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{payload.operation.value} exceeds the original amount")

                elif payload.operation == CommissionAdvanceOperation.ADJUSTMENT:
                    if transaction_type == CommissionAdvanceTransactionType.CREDIT:
                        new_outstanding = current_outstanding - payload.amount
                    elif transaction_type == CommissionAdvanceTransactionType.DEBIT:
                        new_outstanding = current_outstanding + payload.amount

                elif payload.operation == CommissionAdvanceOperation.AMENDMENT:
                    if transaction_type == CommissionAdvanceTransactionType.CREDIT:
                        new_outstanding = current_outstanding - payload.amount
                    elif transaction_type == CommissionAdvanceTransactionType.DEBIT:
                        new_outstanding = current_outstanding + payload.amount

                if new_outstanding < ZERO:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Outstanding balance cannot be negative")

                commission_advance.outstanding_amount = new_outstanding

                if legal_hold:
                    update_legal_hold_balance(legal_hold, new_outstanding)

                created_transaction = CommissionAdvanceTransaction(
                    ca_id=commission_advance.id,
                    legal_hold_id=legal_hold.id if legal_hold else None,
                    operation=payload.operation.value,
                    type=transaction_type.value,
                    amount=payload.amount,
                    transaction_date=payload.transaction_date,
                    notes=payload.notes,
                    created_by=current_user.email,
                    outstanding_amount=new_outstanding,
                )

                db.add(created_transaction)

                if new_outstanding == ZERO:
                    commission_advance.status = CommissionAdvanceStatus.PAID.value

        db.commit()
        db.refresh(commission_advance)

        if created_transaction:
            db.refresh(created_transaction)

        return UpdateCommissionAdvanceResponse(commission_advance=commission_advance, transaction=created_transaction)

    except HTTPException:
        db.rollback()
        raise

    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)) from error
