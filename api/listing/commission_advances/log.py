from datetime import date as DateType
from pydantic import BaseModel, field_validator
from typing import Any
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db
from common.response import Response, FilterResponse
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse


router = APIRouter(prefix="/commission-advances")

class CommissionAdvanceLogRequest(BaseModel):
    agent_name: str
    address: str
    company: str
    amount: float
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
            raise ValueError("Amount must be greater than or equal to 0")
        return value

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
        result = db.execute(
            text("""
                INSERT INTO commission_advances (
                    agent_name,
                    state,
                    address,
                    company,
                    original_amount,
                    approved_date,
                    saleguid,
                    notes,
                    status
                )
                VALUES (
                    :agent_name,
                    :state,
                    :address,
                    :company,
                    :original_amount,
                    :approved_date,
                    :saleguid,
                    :notes,
                    'Pending'
                )
                RETURNING
                    id,
                    agent_name,
                    state,
                    address,
                    company,
                    original_amount,
                    amount_paid,
                    outstanding_amount,
                    approved_date,
                    saleguid,
                    notes,
                    status
            """),
            {
                "agent_name": payload.agent_name.strip(),
                "state": "NA",
                "address": payload.address.strip(),
                "company": payload.company.strip(),
                "original_amount": payload.amount,
                "approved_date": payload.approved_date,  # uses new field name
                "saleguid": payload.saleguid.strip() if payload.saleguid else None,
                "notes": payload.notes.strip() if payload.notes else None,
            },
        )

        created_row = result.mappings().one()
        db.commit()

        return Response[dict[str, Any]](
            success=True,
            data={
                "id": created_row["id"],
                "agent_name": created_row["agent_name"],
                "state": created_row["state"],
                "address": created_row["address"],
                "company": created_row["company"],
                "original_amount": float(created_row["original_amount"] or 0),
                "amount_paid": float(created_row["amount_paid"] or 0),
                "outstanding_amount": float(created_row["outstanding_amount"] or 0),
                "approved_date": created_row["approved_date"],
                "saleguid": created_row["saleguid"],
                "notes": created_row["notes"],
                "status": created_row["status"],
            },
            message="Commission advance logged successfully",
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/log-dropdown", response_model=FilterResponse)
def get_commission_advance_log_dropdowns(db: Session = Depends(get_db)):
    try:
        companies = [
            row["company"]
            for row in db.execute(text("""
                SELECT DISTINCT company
                FROM commission_advances
                WHERE company IS NOT NULL AND TRIM(company) <> ''
                ORDER BY company ASC
            """)).mappings().all()
        ]

        statuses = [
            "Pending",
            "Wage Garnishment",
            "Paid",
            "Cancelled",
            "Left ROA",
            "Pending Partial",
        ]

        return FilterResponse(
            success=True,
            filters={
                "company": companies,
                "status": statuses,
            },
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Failed to fetch commission advance log dropdown data",
                "details": [{"error": str(e)}],
            },
        )


@router.get("/agent-suggestions", response_model=FilterResponse)
def get_agent_name_suggestions(
    q: str = Query(..., min_length=2, description="Agent name search text"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    try:
        query_text = q.strip()

        if not query_text:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "Query text is required",
                    "details": [{"field": "q", "error": "Query cannot be empty"}],
                },
            )

        agent_names = [
            {
                "display_name": row["display_name"],
                "agent_status": row["agent_status"],
                "general_notes": row["general_notes"],
                "internal_notes": row["internal_notes"],
                "office": row["office"],
            }
            for row in db.execute(
                text("""
                    SELECT DISTINCT ON (display_name)
                        display_name,
                        agent_status,
                        general_notes,
                        internal_notes,
                        office
                    FROM brokerage_engine_users
                    WHERE display_name IS NOT NULL
                      AND TRIM(display_name) <> ''
                      AND display_name ILIKE :search
                    ORDER BY display_name ASC
                    LIMIT :limit
                """),
                {"search": f"%{query_text}%", "limit": limit}
            ).mappings().all()
        ]

        return FilterResponse(
            success=True,
            filters={
                "agent_name": agent_names,
            },
            message="Agent suggestions fetched successfully",
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Failed to fetch agent suggestions",
                "details": [{"error": str(e)}],
            },
        )


@router.get("/address-suggestions", response_model=FilterResponse)
def get_address_suggestions(
    q: str = Query(..., min_length=2, description="Address search text"),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
):
    try:
        query_text = q.strip()

        if not query_text:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "Query text is required",
                    "details": [{"field": "q", "error": "Query cannot be empty"}],
                },
            )

        addresses = [
            {
                "address": row["address"],
                "close_date": row["close_date"],
                "ss_status": row["ss_status"],
                "saleguid": row["saleguid"],
            }
            for row in db.execute(
                text("""
                    SELECT DISTINCT
                        CONCAT_WS(
                            ', ',
                            CONCAT_WS(' ', sp.streetnumber::text, sp.streetaddress),
                            sp.city,
                            sp.state,
                            sp.zip
                        ) AS address,
                        s.escrowclosingdate AS close_date,
                        s.status AS ss_status,
                        s.saleguid AS saleguid
                    FROM sale_property sp
                    LEFT JOIN sale s
                        ON sp.saleguid = s.saleguid
                    WHERE CONCAT_WS(
                            ', ',
                            CONCAT_WS(' ', sp.streetnumber::text, sp.streetaddress),
                            sp.city,
                            sp.state,
                            sp.zip
                         ) ILIKE :search
                    ORDER BY address ASC
                    LIMIT :limit
                """),
                {"search": f"%{query_text}%", "limit": limit}
            ).mappings().all()
        ]

        return FilterResponse(
            success=True,
            filters={
                "address": addresses,
            },
            message="Address suggestions fetched successfully",
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Failed to fetch address suggestions",
                "details": [{"error": str(e)}],
            },
        )