from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from api.listing.commission_advances.utils import CommissionAdvanceStatus
from models.commisison_advances import CommissionAdvance, CommissionAdvanceHistory
from common.response import Response
from db import get_db


router = APIRouter(prefix="/commission-advances")


class CommissionAdvanceUpdateRequest(BaseModel):
    status: Optional[CommissionAdvanceStatus] = Field(default=None, description="Pending, Pending Partial, Wage Garnishment, Paid, Cancelled, Left ROA")
    notes: Optional[str] = None
    amount: Optional[Decimal] = Field(default=None, ge=0)
    paid_date: Optional[date] = None
    approved_date: Optional[date] = None
    edited_by: Optional[str] = Field(default=None, max_length=255)


def serialize_value(value: Any) -> str | None:
    return None if value is None else str(value)


def serialize_commission_advance(transaction: CommissionAdvance) -> Dict[str, Any]:
    return {
        "id": transaction.id,
        "agent_name": transaction.agent_name,
        "state": transaction.state,
        "address": transaction.address,
        "company": transaction.company,
        "original_amount": float(transaction.original_amount or 0),
        "amount_paid": float(transaction.amount_paid or 0),
        "outstanding_amount": float(transaction.outstanding_amount or 0),
        "paid_date": transaction.paid_date,
        "approved_date": transaction.approved_date,
        "notes": transaction.notes,
        "status": transaction.status,
    }


@router.patch("/transaction/{transaction_id}", response_model=Response[Dict[str, Any]])
def update_commission_advance_transaction(transaction_id: int, payload: CommissionAdvanceUpdateRequest, db: Session = Depends(get_db)):
    try:
        update_data = payload.model_dump(exclude_unset=True, exclude={"edited_by"})

        if not update_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields provided for update")

        transaction = db.execute(
            select(CommissionAdvance)
            .where(CommissionAdvance.id == transaction_id)
            .with_for_update()
        ).scalar_one_or_none()

        if transaction is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

        history_fields = ["status", "notes", "original_amount", "amount_paid", "paid_date", "approved_date"]

        old_values = {
            field_name: getattr(transaction, field_name)
            for field_name in history_fields
        }

        requested_status = update_data.get("status", transaction.status)

        if isinstance(requested_status, CommissionAdvanceStatus):
            requested_status = requested_status.value

        original_amount = Decimal(transaction.original_amount or 0)
        existing_amount_paid = Decimal(transaction.amount_paid or 0)
        input_amount = update_data.get("amount")

        if requested_status == CommissionAdvanceStatus.WAGE_GARNISHMENT.value:
            disallowed_fields = {"amount", "paid_date"} & set(update_data.keys())

            if disallowed_fields:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="For status 'Wage Garnishment', only notes and approved_date can be modified",
                )

            transaction.status = CommissionAdvanceStatus.WAGE_GARNISHMENT.value

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "approved_date" in update_data:
                transaction.approved_date = update_data["approved_date"]

        elif requested_status == CommissionAdvanceStatus.PENDING_PARTIAL.value:
            if input_amount is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="amount is required when changing status to 'Pending Partial'",
                )

            additional_amount = Decimal(input_amount)

            if transaction.status == CommissionAdvanceStatus.PENDING_PARTIAL.value:
                partial_paid_amount = existing_amount_paid + additional_amount
            else:
                partial_paid_amount = additional_amount

            if partial_paid_amount >= original_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="For 'Pending Partial', cumulative amount_paid must be less than original_amount",
                )

            transaction.status = CommissionAdvanceStatus.PENDING_PARTIAL.value
            transaction.amount_paid = partial_paid_amount

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "paid_date" in update_data:
                transaction.paid_date = update_data["paid_date"]

            if "approved_date" in update_data:
                transaction.approved_date = update_data["approved_date"]

        elif requested_status == CommissionAdvanceStatus.PAID.value:
            if "amount" in update_data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Do not send amount when status is 'Paid'; amount_paid is set automatically",
                )

            transaction.status = CommissionAdvanceStatus.PAID.value
            transaction.amount_paid = original_amount

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "paid_date" in update_data:
                transaction.paid_date = update_data["paid_date"]

            if "approved_date" in update_data:
                transaction.approved_date = update_data["approved_date"]

        else:
            if "status" in update_data:
                transaction.status = requested_status

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "paid_date" in update_data:
                transaction.paid_date = update_data["paid_date"]

            if "approved_date" in update_data:
                transaction.approved_date = update_data["approved_date"]

            if "amount" in update_data:
                transaction.original_amount = input_amount

        db.flush()
        db.refresh(transaction)

        new_values = {
            field_name: getattr(transaction, field_name)
            for field_name in history_fields
        }

        edited_by = (
            payload.edited_by.strip()
            if payload.edited_by and payload.edited_by.strip()
            else "System"
        )

        history_records = []

        for field_name in history_fields:
            old_value = serialize_value(old_values[field_name])
            new_value = serialize_value(new_values[field_name])

            if old_value != new_value:
                history_records.append(
                    CommissionAdvanceHistory(
                        ca_id=transaction.id,
                        field=field_name,
                        old_value=old_value,
                        new_value=new_value,
                        action="UPDATE",
                        edited_by=edited_by,
                    )
                )

        if history_records:
            db.add_all(history_records)

        db.commit()
        db.refresh(transaction)

        return Response[Dict[str, Any]](
            success=True,
            data=serialize_commission_advance(transaction),
            message="Commission advance transaction updated successfully",
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc