from datetime import date
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, Optional
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from common.response import Response
from db import get_db

router = APIRouter(prefix='/commission-advances')

class CommissionAdvanceUpdateRequest(BaseModel):
    status: Optional[str] = Field(
        default=None,
        description="Pending, Pending Partial, Wage Garnishment, Paid, Cancelled"
    )
    notes: Optional[str] = None
    amount: Optional[Decimal] = Field(default=None, ge=0)
    paid_date: Optional[date] = None

@router.patch("/transaction/{transaction_id}", response_model=Response[Dict[str, Any]])
def update_commission_advance_transaction(
    transaction_id: int,
    payload: CommissionAdvanceUpdateRequest,
    db: Session = Depends(get_db),
):
    try:
        allowed_statuses = {
            "Pending",
            "Pending Partial",
            "Wage Garnishment",
            "Paid",
            "Cancelled",
            "Left ROA",
        }

        update_data = payload.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="No fields provided for update"
            )

        if "status" in update_data and update_data["status"] not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Allowed values: {', '.join(sorted(allowed_statuses))}"
            )

        existing = db.execute(
            text("""
                SELECT
                    id,
                    agent_name,
                    state,
                    address,
                    company,
                    original_amount,
                    amount_paid,
                    outstanding_amount,
                    status,
                    notes,
                    paid_date
                FROM commission_advances
                WHERE id = :transaction_id
            """),
            {"transaction_id": transaction_id},
        ).mappings().first()

        if not existing:
            raise HTTPException(status_code=404, detail="Transaction not found")

        original_amount = Decimal(existing["original_amount"] or 0)
        requested_status = update_data.get("status", existing["status"])
        input_amount = update_data.get("amount")
        input_notes = update_data.get("notes") if "notes" in update_data else existing["notes"]
        input_paid_date = update_data.get("paid_date") if "paid_date" in update_data else existing["paid_date"]

        set_clauses = []
        params: Dict[str, Any] = {"transaction_id": transaction_id}

        if requested_status == "Wage Garnishment":
            disallowed_fields = {"amount", "paid_date"} & set(update_data.keys())
            if disallowed_fields:
                raise HTTPException(
                    status_code=400,
                    detail="For status 'Wage Garnishment', only notes can be modified"
                )

            set_clauses.append("status = :status")
            params["status"] = "Wage Garnishment"

            if "notes" in update_data:
                set_clauses.append("notes = :notes")
                params["notes"] = input_notes

        elif requested_status == "Pending Partial":
            if input_amount is None:
                raise HTTPException(
                    status_code=400,
                    detail="amount is required when changing status to 'Pending Partial'"
                )

            try:
                partial_paid_amount = Decimal(input_amount)
            except (InvalidOperation, TypeError):
                raise HTTPException(status_code=400, detail="Invalid amount value")

            if partial_paid_amount < 0:
                raise HTTPException(status_code=400, detail="amount cannot be negative")

            if partial_paid_amount >= original_amount:
                raise HTTPException(
                    status_code=400,
                    detail="For 'Pending Partial', amount must be less than original_amount"
                )

            set_clauses.append("status = :status")
            params["status"] = "Pending Partial"

            set_clauses.append("amount_paid = :amount_paid")
            params["amount_paid"] = partial_paid_amount

            if "notes" in update_data:
                set_clauses.append("notes = :notes")
                params["notes"] = input_notes

            if "paid_date" in update_data:
                set_clauses.append("paid_date = :paid_date")
                params["paid_date"] = input_paid_date

        elif requested_status == "Paid":
            if "amount" in update_data:
                raise HTTPException(
                    status_code=400,
                    detail="Do not send amount when status is 'Paid'; amount_paid will be set to original_amount automatically"
                )

            set_clauses.append("status = :status")
            params["status"] = "Paid"

            set_clauses.append("amount_paid = :amount_paid")
            params["amount_paid"] = original_amount

            if "notes" in update_data:
                set_clauses.append("notes = :notes")
                params["notes"] = input_notes

            if "paid_date" in update_data:
                set_clauses.append("paid_date = :paid_date")
                params["paid_date"] = input_paid_date

        else:
            if "status" in update_data:
                set_clauses.append("status = :status")
                params["status"] = requested_status

            if "notes" in update_data:
                set_clauses.append("notes = :notes")
                params["notes"] = input_notes

            if "paid_date" in update_data:
                set_clauses.append("paid_date = :paid_date")
                params["paid_date"] = input_paid_date

            if "amount" in update_data:
                try:
                    amount_value = Decimal(input_amount)
                except (InvalidOperation, TypeError):
                    raise HTTPException(status_code=400, detail="Invalid amount value")

                if amount_value < 0:
                    raise HTTPException(status_code=400, detail="amount cannot be negative")

                set_clauses.append("original_amount = :original_amount")
                params["original_amount"] = amount_value

        if not set_clauses:
            raise HTTPException(
                status_code=400,
                detail="No valid fields to update for the selected status"
            )

        update_query = text(f"""
            UPDATE commission_advances
            SET {', '.join(set_clauses)}
            WHERE id = :transaction_id
            RETURNING
                id,
                agent_name,
                state,
                address,
                company,
                original_amount,
                amount_paid,
                outstanding_amount,
                paid_date,
                notes,
                status
        """)

        updated_row = db.execute(update_query, params).mappings().first()
        db.commit()

        return Response[Dict[str, Any]](
            success=True,
            data={
                "id": updated_row["id"],
                "agent_name": updated_row["agent_name"],
                "state": updated_row["state"],
                "address": updated_row["address"],
                "company": updated_row["company"],
                "original_amount": float(updated_row["original_amount"] or 0),
                "amount_paid": float(updated_row["amount_paid"] or 0),
                "outstanding_amount": float(updated_row["outstanding_amount"] or 0),
                "paid_date": updated_row["paid_date"],
                "notes": updated_row["notes"],
                "status": updated_row["status"],
            },
            message="Commission advance transaction updated successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))