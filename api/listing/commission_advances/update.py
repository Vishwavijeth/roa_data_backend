from decimal import Decimal
from enum import Enum
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.listing.commission_advances.base import (
    CommissionAdvanceUpdateRequest,
)
from api.listing.commission_advances.utils import (
    CommissionAdvanceStatus, CommissionAdvanceOperation
)
from common.response import Response
from db import get_db
from models.commisison_advances import (
    CommissionAdvance,
    CommissionAdvanceHistory,
)

router = APIRouter(prefix="/commission-advances")

def serialize_value(value: Any) -> str | None:
    return None if value is None else str(value)


def serialize_commission_advance(
    transaction: CommissionAdvance,
) -> Dict[str, Any]:
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
        "saleguid": (
            str(transaction.saleguid)
            if transaction.saleguid
            else None
        ),
    }


@router.patch(
    "/transaction/{transaction_id}",
    response_model=Response[Dict[str, Any]],
)
def update_commission_advance_transaction(
    transaction_id: int,
    payload: CommissionAdvanceUpdateRequest,
    db: Session = Depends(get_db),
):
    try:
        update_data = payload.model_dump(
            exclude_unset=True,
            exclude={"edited_by"},
        )

        if not update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided for update",
            )

        transaction = db.execute(
            select(CommissionAdvance)
            .where(
                CommissionAdvance.id == transaction_id
            )
            .with_for_update()
        ).scalar_one_or_none()

        if transaction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction not found",
            )

        history_fields = [
            "status",
            "notes",
            "original_amount",
            "amount_paid",
            "paid_date",
            "approved_date",
            "address",
        ]

        old_values = {
            field_name: getattr(
                transaction,
                field_name,
            )
            for field_name in history_fields
        }

        requested_status = update_data.get(
            "status",
            transaction.status,
        )

        if isinstance(
            requested_status,
            CommissionAdvanceStatus,
        ):
            requested_status = requested_status.value

        original_amount = Decimal(
            str(transaction.original_amount or 0)
        )

        existing_amount_paid = Decimal(
            str(transaction.amount_paid or 0)
        )

        input_amount = update_data.get("amount")

        # ============================================================
        # WAGE GARNISHMENT
        # ============================================================
        if (
            requested_status
            == CommissionAdvanceStatus.WAGE_GARNISHMENT.value
        ):
            disallowed_fields = {
                "amount",
                "operation",
                "paid_date",
                "address",
                "saleguid",
            } & set(update_data.keys())

            if disallowed_fields:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "For status 'Wage Garnishment', only "
                        "notes and approved_date can be modified"
                    ),
                )

            transaction.status = (
                CommissionAdvanceStatus.WAGE_GARNISHMENT.value
            )

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "approved_date" in update_data:
                transaction.approved_date = (
                    update_data["approved_date"]
                )

        elif requested_status in {
            CommissionAdvanceStatus.PENDING.value,
            CommissionAdvanceStatus.PENDING_PARTIAL.value,
        }:

            # --------------------------------------------------------
            # AMOUNT IS REQUIRED
            # --------------------------------------------------------
            if input_amount is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "amount is required for status "
                        "'Pending' or 'Pending Partial'"
                    ),
                )

            # --------------------------------------------------------
            # OPERATION IS REQUIRED
            # --------------------------------------------------------
            operation = update_data.get("operation")

            if operation is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "operation is required for status "
                        "'Pending' or 'Pending Partial'. "
                        "Allowed values: 'add', 'sub', 'set'"
                    ),
                )

            if isinstance(
                operation,
                CommissionAdvanceOperation,
            ):
                operation = operation.value

            # --------------------------------------------------------
            # VALIDATE OPERATION
            # --------------------------------------------------------
            if operation not in {
                CommissionAdvanceOperation.ADD.value,
                CommissionAdvanceOperation.SUB.value,
                CommissionAdvanceOperation.SET.value,
            }:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Invalid operation. Allowed values are "
                        "'add', 'sub', 'set'"
                    ),
                )

            amount = Decimal(str(input_amount))

            # --------------------------------------------------------
            # AMOUNT MUST NOT BE NEGATIVE
            # --------------------------------------------------------
            if amount < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="amount cannot be negative",
                )

            # --------------------------------------------------------
            # ADD
            # --------------------------------------------------------
            if operation == CommissionAdvanceOperation.ADD.value:
                new_amount_paid = (
                    existing_amount_paid + amount
                )

            # --------------------------------------------------------
            # SUB
            # --------------------------------------------------------
            elif operation == CommissionAdvanceOperation.SUB.value:
                new_amount_paid = (
                    existing_amount_paid - amount
                )

            # --------------------------------------------------------
            # SET
            # --------------------------------------------------------
            else:
                new_amount_paid = amount

            # --------------------------------------------------------
            # AMOUNT PAID CANNOT BE NEGATIVE
            # --------------------------------------------------------
            if new_amount_paid < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "amount_paid cannot be negative. "
                        f"Current amount_paid: "
                        f"{existing_amount_paid}, "
                        f"entered amount: {amount}, "
                        f"operation: {operation}"
                    ),
                )

            # --------------------------------------------------------
            # AMOUNT PAID CANNOT EXCEED ORIGINAL AMOUNT
            # --------------------------------------------------------
            if new_amount_paid > original_amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "amount_paid cannot exceed "
                        "original_amount. "
                        f"original_amount: "
                        f"{original_amount}, "
                        f"calculated amount_paid: "
                        f"{new_amount_paid}, "
                        f"operation: {operation}, "
                        f"entered amount: {amount}"
                    ),
                )

            transaction.status = requested_status

            # Only amount_paid is updated.
            transaction.amount_paid = new_amount_paid

            # IMPORTANT:
            #
            # original_amount is NEVER changed here.
            #
            # outstanding_amount is NEVER changed here.
            #
            # The database calculates:
            #
            # outstanding_amount =
            #     original_amount - amount_paid

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "paid_date" in update_data:
                transaction.paid_date = (
                    update_data["paid_date"]
                )

            if "approved_date" in update_data:
                transaction.approved_date = (
                    update_data["approved_date"]
                )

        # ============================================================
        # PAID
        #
        # amount and operation are NOT allowed.
        #
        # amount_paid becomes original_amount.
        # ============================================================
        elif (
            requested_status
            == CommissionAdvanceStatus.PAID.value
        ):

            if "amount" in update_data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Do not send amount when status "
                        "is 'Paid'; amount_paid is set "
                        "automatically"
                    ),
                )

            if "operation" in update_data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Do not send operation when "
                        "status is 'Paid'"
                    ),
                )

            transaction.status = (
                CommissionAdvanceStatus.PAID.value
            )

            transaction.amount_paid = original_amount

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "paid_date" in update_data:
                transaction.paid_date = (
                    update_data["paid_date"]
                )

            if "approved_date" in update_data:
                transaction.approved_date = (
                    update_data["approved_date"]
                )

        # ============================================================
        # REPLACEMENT
        # ============================================================
        elif (
            requested_status
            == CommissionAdvanceStatus.REPLACEMENT.value
        ):
            transaction.status = (
                CommissionAdvanceStatus.REPLACEMENT.value
            )

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "paid_date" in update_data:
                transaction.paid_date = (
                    update_data["paid_date"]
                )

            if "approved_date" in update_data:
                transaction.approved_date = (
                    update_data["approved_date"]
                )

            if "amount" in update_data:
                replacement_amount = Decimal(
                    str(update_data["amount"])
                )

                if replacement_amount < 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="amount cannot be negative",
                    )

                if replacement_amount > original_amount:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "amount_paid cannot exceed "
                            "original_amount"
                        ),
                    )

                transaction.amount_paid = (
                    replacement_amount
                )

            if "saleguid" in update_data:
                transaction.saleguid = (
                    update_data["saleguid"]
                )

            if "address" in update_data:
                transaction.address = (
                    update_data["address"]
                )

        # ============================================================
        # OTHER STATUS UPDATES
        # ============================================================
        else:
            if "status" in update_data:
                transaction.status = requested_status

            if "notes" in update_data:
                transaction.notes = update_data["notes"]

            if "paid_date" in update_data:
                transaction.paid_date = (
                    update_data["paid_date"]
                )

            if "approved_date" in update_data:
                transaction.approved_date = (
                    update_data["approved_date"]
                )

            if "amount" in update_data:
                new_payment = Decimal(
                    str(update_data["amount"])
                )

                if new_payment < 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="amount cannot be negative",
                    )

                amount_paid = (
                    existing_amount_paid
                    + new_payment
                )

                if amount_paid > original_amount:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            "Total amount_paid cannot "
                            "be greater than "
                            "original_amount"
                        ),
                    )

                transaction.amount_paid = amount_paid

            if "saleguid" in update_data:
                transaction.saleguid = (
                    update_data["saleguid"]
                )

            if "address" in update_data:
                transaction.address = (
                    update_data["address"]
                )

        db.flush()
        db.refresh(transaction)

        # ============================================================
        # HISTORY
        # ============================================================
        new_values = {
            field_name: getattr(
                transaction,
                field_name,
            )
            for field_name in history_fields
        }

        edited_by = (
            payload.edited_by.strip()
            if payload.edited_by
            and payload.edited_by.strip()
            else "System"
        )

        history_records = []

        for field_name in history_fields:
            old_value = serialize_value(
                old_values[field_name]
            )

            new_value = serialize_value(
                new_values[field_name]
            )

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
            data=serialize_commission_advance(
                transaction
            ),
            message=(
                "Commission advance transaction "
                "updated successfully"
            ),
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