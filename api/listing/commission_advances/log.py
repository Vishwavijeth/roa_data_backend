from datetime import date as DateType
from decimal import Decimal
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import String, cast, distinct, func, select
from sqlalchemy.orm import Session
from common.response import FilterResponse, Response
from db import get_db
from api.listing.commission_advances.utils import CommissionAdvanceStatus
from models.brokerage_engine_users import BrokerageEngineUser
from models.commisison_advances import CommissionAdvance
from models.skyslope.sale import Sale
from models.skyslope.property import SaleProperty


router = APIRouter(prefix="/commission-advances")


class CommissionAdvanceLogRequest(BaseModel):
    agent_name: str
    address: str
    company: str
    amount: Decimal
    approved_date: DateType | None = None
    saleguid: str | None = None
    notes: str | None = None

    @field_validator("agent_name", "company", "address")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Field cannot be empty")

        return value

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError(
                "Amount must be greater than or equal to 0"
            )

        return value

    @field_validator("saleguid", "notes")
    @classmethod
    def strip_optional_values(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None


@router.post(
    "/log",
    response_model=Response[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
def create_commission_advance_log(
    payload: CommissionAdvanceLogRequest,
    db: Session = Depends(get_db),
):
    try:
        commission_advance = CommissionAdvance(
            agent_name=payload.agent_name,
            state="NA",
            address=payload.address,
            company=payload.company,
            original_amount=payload.amount,
            approved_date=payload.approved_date,
            saleguid=payload.saleguid,
            notes=payload.notes,
            status=CommissionAdvanceStatus.PENDING.value,
        )

        db.add(commission_advance)
        db.flush()
        db.refresh(commission_advance)
        db.commit()

        return Response[dict[str, Any]](
            success=True,
            data={
                "id": commission_advance.id,
                "agent_name": commission_advance.agent_name,
                "state": commission_advance.state,
                "address": commission_advance.address,
                "company": commission_advance.company,
                "original_amount": float(
                    commission_advance.original_amount or 0
                ),
                "amount_paid": float(
                    commission_advance.amount_paid or 0
                ),
                "outstanding_amount": float(
                    commission_advance.outstanding_amount or 0
                ),
                "approved_date": commission_advance.approved_date,
                "saleguid": commission_advance.saleguid,
                "notes": commission_advance.notes,
                "status": commission_advance.status,
            },
            message="Commission advance logged successfully",
        )

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/log-dropdown",
    response_model=FilterResponse,
)
def get_commission_advance_log_dropdowns(
    db: Session = Depends(get_db),
):
    try:
        ca = CommissionAdvance

        companies = db.execute(
            select(ca.company)
            .where(
                ca.company.isnot(None),
                func.trim(ca.company) != "",
            )
            .distinct()
            .order_by(ca.company.asc())
        ).scalars().all()

        return FilterResponse(
            success=True,
            filters={
                "company": companies,
                "status": CommissionAdvanceStatus.values(),
            },
        )

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/agent-suggestions",
    response_model=FilterResponse,
)
def get_agent_name_suggestions(
    q: str = Query(
        ...,
        min_length=2,
        description="Agent name search text",
    ),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    query_text = q.strip()

    if not query_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query text is required",
        )

    beu = BrokerageEngineUser

    statement = (
        select(
            beu.display_name,
            beu.agent_status,
            beu.general_notes,
            beu.internal_notes,
            beu.office,
        )
        .where(
            beu.display_name.isnot(None),
            func.trim(beu.display_name) != "",
            beu.display_name.ilike(f"%{query_text}%"),
        )
        .distinct(beu.display_name)
        .order_by(beu.display_name.asc())
        .limit(limit)
    )

    try:
        rows = db.execute(statement).mappings().all()

        agent_names = [
            {
                "display_name": row["display_name"],
                "agent_status": row["agent_status"],
                "general_notes": row["general_notes"],
                "internal_notes": row["internal_notes"],
                "office": row["office"],
            }
            for row in rows
        ]

        return FilterResponse(
            success=True,
            filters={
                "agent_name": agent_names,
            },
            message="Agent suggestions fetched successfully",
        )

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.get(
    "/address-suggestions",
    response_model=FilterResponse,
)
def get_address_suggestions(
    q: str = Query(
        ...,
        min_length=2,
        description="Address search text",
    ),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    query_text = q.strip()

    if not query_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query text is required",
        )

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
        select(
            address_expression,
            sale.escrowclosingdate.label("close_date"),
            sale.status.label("ss_status"),
            sale.saleguid,
        )
        .select_from(sp)
        .outerjoin(
            sale,
            sp.saleguid == sale.saleguid,
        )
        .where(
            address_expression.ilike(f"%{query_text}%"),
        )
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

        return FilterResponse(
            success=True,
            filters={
                "address": addresses,
            },
            message="Address suggestions fetched successfully",
        )

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc