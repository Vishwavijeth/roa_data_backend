from datetime import date
from pydantic import BaseModel, field_validator
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session
from db import get_db
from common.response import Response, FilterResponse
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse


router = APIRouter(prefix="/commission-advances")

class CommissionAdvanceLogRequest(BaseModel):
    agent_name: str
    state: str
    amount: int
    address: str
    company: str
    paid_date: date
    notes: str | None = None
    status: str

    @field_validator("agent_name", "state", "address", "company", "status")
    @classmethod
    def validate_not_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be empty")
        return value

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Amount must be greater than or equal to 0")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        value = value.strip()
        allowed = {"Pending", "Paid", "Wage Garnishment"}
        if value not in allowed:
            raise ValueError("Status must be one of: Pending, Paid, Wage Garnishment")
        return value

@router.post("/log", response_model=Response[dict[str, Any]], status_code=status.HTTP_201_CREATED)
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
                    amount,
                    address,
                    company,
                    paid_date,
                    notes,
                    status
                )
                VALUES (
                    :agent_name,
                    :state,
                    :amount,
                    :address,
                    :company,
                    :paid_date,
                    :notes,
                    :status
                )
                RETURNING
                    agent_name,
                    state,
                    amount,
                    address,
                    company,
                    paid_date,
                    notes,
                    status
            """),
            payload.model_dump(),
        )

        created_row = result.mappings().one()
        db.commit()

        return Response[dict[str, Any]](
            success=True,
            data=dict(created_row),
            message="Commission advance transaction created successfully",
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/log-dropdown", response_model=FilterResponse)
def get_commission_advance_log_dropdowns(db: Session = Depends(get_db)):
    try:
        states = [
            row["state"]
            for row in db.execute(text("""
                SELECT DISTINCT UPPER(state) AS state
                FROM sale_property
                WHERE state IS NOT NULL AND TRIM(state) <> ''
                ORDER BY state ASC
            """)).mappings().all()
        ]

        companies = [
            row["company"]
            for row in db.execute(text("""
                SELECT DISTINCT company
                FROM commission_advances
                WHERE company IS NOT NULL AND TRIM(company) <> ''
                ORDER BY company ASC
            """)).mappings().all()
        ]

        return FilterResponse(
            success=True,
            filters={
                "state": states,
                "company": companies,
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
            row["agent_name"]
            for row in db.execute(
                text("""
                    SELECT DISTINCT agent_name
                    FROM commission_advances
                    WHERE agent_name IS NOT NULL
                      AND TRIM(agent_name) <> ''
                      AND agent_name ILIKE :search
                    ORDER BY agent_name ASC
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
            message="Agent name suggestions fetched successfully",
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Failed to fetch agent name suggestions",
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
            row["address"]
            for row in db.execute(
                text("""
                    SELECT DISTINCT
                        CONCAT_WS(
                            ', ',
                            CONCAT_WS(' ', streetNumber::text, streetAddress),
                            city,
                            state,
                            zip
                        ) AS address
                    FROM sale_property
                    WHERE CONCAT_WS(
                            ', ',
                            CONCAT_WS(' ', streetNumber::text, streetAddress),
                            city,
                            state,
                            zip
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