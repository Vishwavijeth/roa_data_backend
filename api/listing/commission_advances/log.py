from datetime import date as DateType
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from common.response import Response, FilterResponse
from db import get_db
from models.commission_advances.commission_advances import CommissionAdvance, CommissionAdvanceTransaction, CommissionAdvanceLegalHold
from models.roa_data_users import RoaDataUser
from models.skyslope.sale import Sale
from models.skyslope.property import SaleProperty
from models.brokerage_engine_users import BrokerageEngineUser
from api.auth.authentication import get_current_user
from api.listing.commission_advances.utils import CommissionAdvanceStatus, CommissionAdvanceLegalHoldStatus, CommissionAdvanceOperation, CommissionAdvanceTransactionType

router = APIRouter(prefix="/commission-advances")

ZERO = Decimal("0")


class CommissionAdvanceLogRequest(BaseModel):
    agent_id: UUID
    agent_name: str
    address: str
    company: str
    amount: Decimal | None = None
    approved_date: DateType | None = None
    saleguid: str | None = None
    notes: str | None = None

    @field_validator("agent_name", "company", "address")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field is required")
        return value

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value < ZERO:
            raise ValueError("Amount cannot be negative")
        return value

    @field_validator("saleguid", "notes")
    @classmethod
    def strip_optional_values(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def get_current_legal_hold_ca(db: Session, legal_hold: CommissionAdvanceLegalHold):
    current_ca_id = db.scalar(
        select(CommissionAdvanceTransaction.ca_id)
        .where(
            CommissionAdvanceTransaction.legal_hold_id == legal_hold.id,
            CommissionAdvanceTransaction.operation == CommissionAdvanceOperation.LEGAL_HOLD_BALANCE.value,
        )
        .order_by(CommissionAdvanceTransaction.id.desc())
        .limit(1)
    )

    if current_ca_id is None:
        current_ca_id = legal_hold.source_ca_id

    return db.scalar(select(CommissionAdvance).where(CommissionAdvance.id == current_ca_id).with_for_update())


@router.post("/log", response_model=Response[dict[str, Any]], status_code=status.HTTP_201_CREATED)
def create_commission_advance_log(
    payload: CommissionAdvanceLogRequest,
    db: Session = Depends(get_db),
    current_user: RoaDataUser = Depends(get_current_user),
):
    try:
        active_legal_hold = db.scalar(
            select(CommissionAdvanceLegalHold)
            .where(
                CommissionAdvanceLegalHold.agent_id == payload.agent_id,
                CommissionAdvanceLegalHold.status == CommissionAdvanceLegalHoldStatus.ACTIVE.value,
                CommissionAdvanceLegalHold.outstanding_amount > ZERO,
            )
            .order_by(CommissionAdvanceLegalHold.id.desc())
            .with_for_update()
        )

        if active_legal_hold:
            current_legal_hold_ca = get_current_legal_hold_ca(db=db, legal_hold=active_legal_hold)

            if not current_legal_hold_ca:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current legal hold advance not found")

            if current_legal_hold_ca.status != CommissionAdvanceStatus.LEGAL_HOLD.value:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot create a new advance. Another advance is still active.")

            if payload.amount is not None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount should not be entered for a legal hold advance")

            legal_hold_amount = active_legal_hold.outstanding_amount or ZERO

            if legal_hold_amount <= ZERO:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No legal hold balance available")

            commission_advance = CommissionAdvance(
                agent_id=payload.agent_id,
                agent_name=payload.agent_name,
                state="NA",
                address=payload.address,
                company=payload.company,
                original_amount=legal_hold_amount,
                amount_paid=ZERO,
                outstanding_amount=legal_hold_amount,
                approved_date=payload.approved_date,
                saleguid=payload.saleguid,
                notes=payload.notes,
                status=CommissionAdvanceStatus.PENDING.value,
            )

            db.add(commission_advance)
            db.flush()

            ledger_transaction = CommissionAdvanceTransaction(
                ca_id=commission_advance.id,
                legal_hold_id=active_legal_hold.id,
                operation=CommissionAdvanceOperation.LEGAL_HOLD_BALANCE.value,
                type=CommissionAdvanceTransactionType.DEBIT.value,
                amount=legal_hold_amount,
                transaction_date=payload.approved_date,
                notes=payload.notes,
                created_by=current_user.email,
                outstanding_amount=legal_hold_amount,
            )

            db.add(ledger_transaction)
            db.commit()
            db.refresh(commission_advance)
            db.refresh(ledger_transaction)

            return Response[dict[str, Any]](
                success=True,
                data={
                    "id": commission_advance.id,
                    "agent_id": commission_advance.agent_id,
                    "agent_name": commission_advance.agent_name,
                    "state": commission_advance.state,
                    "address": commission_advance.address,
                    "company": commission_advance.company,
                    "original_amount": float(commission_advance.original_amount or 0),
                    "amount_paid": float(commission_advance.amount_paid or 0),
                    "outstanding_amount": float(commission_advance.outstanding_amount or 0),
                    "approved_date": commission_advance.approved_date,
                    "saleguid": commission_advance.saleguid,
                    "notes": commission_advance.notes,
                    "status": commission_advance.status,
                    "has_legal_hold": True,
                    "legal_hold_id": active_legal_hold.id,
                    "legal_hold_source_ca_id": active_legal_hold.source_ca_id,
                    "legal_hold_outstanding_amount": float(active_legal_hold.outstanding_amount or 0),
                    "previous_ca_id": current_legal_hold_ca.id,
                    "ledger_transaction_id": ledger_transaction.id,
                },
                message="Legal hold advance created",
            )

        if payload.amount is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount is required")

        if payload.amount <= ZERO:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Amount must be greater than 0")

        commission_advance = CommissionAdvance(
            agent_id=payload.agent_id,
            agent_name=payload.agent_name,
            state="NA",
            address=payload.address,
            company=payload.company,
            original_amount=payload.amount,
            amount_paid=ZERO,
            outstanding_amount=payload.amount,
            approved_date=payload.approved_date,
            saleguid=payload.saleguid,
            notes=payload.notes,
            status=CommissionAdvanceStatus.PENDING.value,
        )

        db.add(commission_advance)
        db.flush()

        ledger_transaction = CommissionAdvanceTransaction(
            ca_id=commission_advance.id,
            legal_hold_id=None,
            operation=CommissionAdvanceOperation.BASE_AMOUNT.value,
            type=CommissionAdvanceTransactionType.DEBIT.value,
            amount=payload.amount,
            transaction_date=payload.approved_date,
            notes=payload.notes,
            created_by=current_user.email,
            outstanding_amount=payload.amount,
        )

        db.add(ledger_transaction)
        db.commit()
        db.refresh(commission_advance)
        db.refresh(ledger_transaction)

        return Response[dict[str, Any]](
            success=True,
            data={
                "id": commission_advance.id,
                "agent_id": commission_advance.agent_id,
                "agent_name": commission_advance.agent_name,
                "state": commission_advance.state,
                "address": commission_advance.address,
                "company": commission_advance.company,
                "original_amount": float(commission_advance.original_amount or 0),
                "amount_paid": float(commission_advance.amount_paid or 0),
                "outstanding_amount": float(commission_advance.outstanding_amount or 0),
                "approved_date": commission_advance.approved_date,
                "saleguid": commission_advance.saleguid,
                "notes": commission_advance.notes,
                "status": commission_advance.status,
                "has_legal_hold": False,
                "legal_hold_id": None,
                "ledger_transaction_id": ledger_transaction.id,
            },
            message="Advance created",
        )

    except HTTPException:
        db.rollback()
        raise

    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create advance") from exc


@router.get("/legal-holds/{legal_hold_id}/detail", response_model=Response[dict[str, Any]])
def get_legal_hold_detail(legal_hold_id: int, db: Session = Depends(get_db)):
    try:
        ca = CommissionAdvance
        transaction = CommissionAdvanceTransaction
        legal_hold = CommissionAdvanceLegalHold

        source_row = db.execute(
            select(ca.address, ca.company, ca.original_amount, ca.outstanding_amount)
            .join(legal_hold, legal_hold.source_ca_id == ca.id)
            .where(legal_hold.id == legal_hold_id)
        ).mappings().first()

        if not source_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Legal hold not found")

        involved_ca_ids = select(transaction.ca_id).where(transaction.legal_hold_id == legal_hold_id).distinct().subquery()

        rows = db.execute(
            select(
                ca.id.label("ca_id"),
                ca.address,
                ca.company,
                ca.original_amount,
                ca.outstanding_amount,
                transaction.id.label("transaction_id"),
                transaction.legal_hold_id,
                transaction.operation,
                transaction.type.label("transaction_type"),
                transaction.amount.label("transaction_amount"),
                transaction.transaction_date,
                transaction.notes.label("transaction_notes"),
                transaction.outstanding_amount.label("transaction_outstanding_amount"),
                transaction.created_by,
                transaction.updated_at,
            )
            .select_from(ca)
            .join(involved_ca_ids, involved_ca_ids.c.ca_id == ca.id)
            .outerjoin(transaction, transaction.ca_id == ca.id)
            .order_by(ca.id.asc(), transaction.id.asc().nullslast())
        ).mappings().all()

        commission_advances_by_id: dict[int, dict[str, Any]] = {}

        for row in rows:
            ca_id = row["ca_id"]

            if ca_id not in commission_advances_by_id:
                commission_advances_by_id[ca_id] = {
                    "address": row["address"],
                    "company": row["company"],
                    "original_amount": float(row["original_amount"] or 0),
                    "outstanding_amount": float(row["outstanding_amount"] or 0),
                    "transactions": [],
                }

            if row["transaction_id"] is None:
                continue

            commission_advances_by_id[ca_id]["transactions"].append({
                "id": row["transaction_id"],
                "legal_hold_id": row["legal_hold_id"],
                "operation": row["operation"],
                "type": row["transaction_type"],
                "amount": float(row["transaction_amount"] or 0),
                "transaction_date": row["transaction_date"],
                "notes": row["transaction_notes"],
                "outstanding_amount": float(row["transaction_outstanding_amount"] or 0),
                "created_by": row["created_by"],
                "updated_at": row["updated_at"],
            })

        return Response[dict[str, Any]](
            success=True,
            data={
                "source_transaction": {
                    "address": source_row["address"],
                    "company": source_row["company"],
                    "original_amount": float(source_row["original_amount"] or 0),
                    "outstanding_amount": float(source_row["outstanding_amount"] or 0),
                },
                "commission_advances": list(commission_advances_by_id.values()),
            },
            message="Legal hold details fetched",
        )

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch legal hold details") from exc


@router.get("/agent-suggestions", response_model=FilterResponse)
def get_agent_name_suggestions(
    q: str = Query(..., min_length=2, description="Agent name search text"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    query_text = q.strip()

    if not query_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search text is required")

    beu = BrokerageEngineUser
    calh = CommissionAdvanceLegalHold

    active_legal_hold = (
        select(
            calh.id.label("legal_hold_id"),
            calh.agent_id.label("legal_hold_agent_id"),
            calh.source_ca_id,
            calh.original_amount.label("legal_hold_original_amount"),
            calh.outstanding_amount.label("legal_hold_outstanding_amount"),
            calh.status.label("legal_hold_status"),
        )
        .where(calh.status == CommissionAdvanceLegalHoldStatus.ACTIVE.value, calh.outstanding_amount > ZERO)
        .subquery()
    )

    statement = (
        select(
            beu.agent_identifier.label("agent_id"),
            beu.display_name,
            beu.agent_status,
            beu.general_notes,
            beu.internal_notes,
            beu.office,
            active_legal_hold.c.legal_hold_id,
            active_legal_hold.c.source_ca_id,
            active_legal_hold.c.legal_hold_original_amount,
            active_legal_hold.c.legal_hold_outstanding_amount,
            active_legal_hold.c.legal_hold_status,
        )
        .outerjoin(active_legal_hold, active_legal_hold.c.legal_hold_agent_id == beu.agent_identifier)
        .where(beu.display_name.isnot(None), func.trim(beu.display_name) != "", beu.display_name.ilike(f"%{query_text}%"))
        .distinct(beu.display_name)
        .order_by(beu.display_name.asc())
        .limit(limit)
    )

    try:
        rows = db.execute(statement).mappings().all()

        agent_names = [
            {
                "agent_id": row["agent_id"],
                "display_name": row["display_name"],
                "agent_status": row["agent_status"],
                "general_notes": row["general_notes"],
                "internal_notes": row["internal_notes"],
                "office": row["office"],
                "has_legal_hold": row["legal_hold_id"] is not None,
                "legal_hold": {
                    "id": row["legal_hold_id"],
                    "source_ca_id": row["source_ca_id"],
                    "original_amount": float(row["legal_hold_original_amount"] or 0),
                    "outstanding_amount": float(row["legal_hold_outstanding_amount"] or 0),
                    "status": row["legal_hold_status"],
                } if row["legal_hold_id"] is not None else None,
            }
            for row in rows
        ]

        return FilterResponse(success=True, filters={"agent_name": agent_names}, message="Agents fetched")

    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch agents") from exc


@router.get("/address-suggestions", response_model=FilterResponse)
def get_address_suggestions(
    q: str = Query(..., min_length=2, description="Address search text"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    query_text = q.strip()

    if not query_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search text is required")

    sp = SaleProperty
    sale = Sale

    address_expression = func.concat_ws(
        " ",
        cast(sp.streetnumber, String),
        sp.streetaddress,
        sp.unit,
        sp.city,
        sp.state,
        cast(sp.zip, String),
    ).label("address")

    statement = (
        select(address_expression, sale.escrowclosingdate.label("close_date"), sale.status.label("ss_status"), sale.saleguid)
        .select_from(sp)
        .outerjoin(sale, sp.saleguid == sale.saleguid)
        .where(address_expression.ilike(f"%{query_text}%"))
        .distinct()
        .order_by(address_expression.asc())
        .limit(limit)
    )

    try:
        rows = db.execute(statement).mappings().all()

        addresses = [
            {
                "address": row["address"],
                "close_date": row["close_date"],
                "ss_status": row["ss_status"],
                "saleguid": row["saleguid"],
            }
            for row in rows
        ]

        return FilterResponse(success=True, filters={"address": addresses}, message="Addresses fetched")

    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch addresses") from exc
