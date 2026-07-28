from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict
from math import ceil

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from common.pagination import PaginationData, PaginationResponseWithCount
from common.response import Response
from db import get_db


class CommissionAdvanceSummary(BaseModel):
    pending_advances: int
    commission_advance_received: int
    agents_with_active_advances: int


router = APIRouter(prefix="/commission-advances")


@router.get("/summary", response_model=Response[CommissionAdvanceSummary])
def get_commission_advances_summary(db: Session = Depends(get_db)):
    try:
        pending_advances = db.execute(
            text("""
                SELECT COUNT(*) AS pending_advances
                FROM commission_advances
                WHERE status = 'Pending'
            """)
        ).scalar() or 0

        commission_advance_received = db.execute(
            text("""
                SELECT COUNT(*) AS commission_advance_received
                FROM commission_advances
                WHERE status = 'Paid'
            """)
        ).scalar() or 0

        agents_with_active_advances = db.execute(
            text("""
                SELECT COUNT(DISTINCT agent_name) AS agents_with_active_advances
                FROM commission_advances
                WHERE status = 'Pending'
            """)
        ).scalar() or 0

        summary = CommissionAdvanceSummary(
            pending_advances=pending_advances,
            commission_advance_received=commission_advance_received,
            agents_with_active_advances=agents_with_active_advances,
        )

        return Response[CommissionAdvanceSummary](
            success=True,
            data=summary
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/listing", response_model=PaginationResponseWithCount[Dict[str, Any]])
def get_commission_advances_listing(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        offset = (page - 1) * page_size

        total_count = db.execute(
            text("""
                SELECT COUNT(*) AS total_count
                FROM (
                    SELECT agent_name
                    FROM commission_advances
                    GROUP BY agent_name
                ) grouped_agents
            """)
        ).scalar() or 0

        rows = db.execute(
            text("""
                SELECT
                    agent_name,
                    COALESCE(SUM(amount), 0) AS total_outstanding,
                    COUNT(*) FILTER (WHERE status = 'Pending') AS pending_count,
                    COUNT(*) FILTER (WHERE status = 'Paid') AS paid_count,
                    COUNT(*) FILTER (WHERE status = 'Wage Garnishment') AS wage_garnishment_count
                FROM commission_advances
                GROUP BY agent_name
                ORDER BY agent_name ASC
                LIMIT :limit OFFSET :offset
            """),
            {"limit": page_size, "offset": offset},
        ).mappings().all()

        items = []
        for row in rows:
            items.append({
                "agent_name": row["agent_name"],
                "total_outstanding": int(row["total_outstanding"] or 0),
                "status_breakdown": {
                    "pending": row["pending_count"],
                    "paid": row["paid_count"],
                    "wage_garnishment": row["wage_garnishment_count"],
                },
            })

        total_pages = max(1, ceil(total_count / page_size)) if total_count else 1
        has_next = page < total_pages

        return PaginationResponseWithCount[Dict[str, Any]](
            success=True,
            data=PaginationData[Dict[str, Any]](
                total_count=total_count,
                items=items,
            ),
            page=page,
            page_size=page_size,
            count=len(items),
            total_pages=total_pages,
            has_next=has_next,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail", response_model=PaginationResponseWithCount[Dict[str, Any]])
def get_commission_advances_detail(
    agent_name: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        offset = (page - 1) * page_size

        total_count = db.execute(
            text("""
                SELECT COUNT(*) AS total_count
                FROM commission_advances
                WHERE agent_name = :agent_name
            """),
            {"agent_name": agent_name},
        ).scalar() or 0

        rows = db.execute(
            text("""
                SELECT
                    agent_name,
                    state,
                    amount,
                    address,
                    company,
                    paid_date,
                    notes,
                    status
                FROM commission_advances
                WHERE agent_name = :agent_name
                ORDER BY paid_date DESC NULLS LAST, status ASC
                LIMIT :limit OFFSET :offset
            """),
            {
                "agent_name": agent_name,
                "limit": page_size,
                "offset": offset,
            },
        ).mappings().all()

        items = []
        for row in rows:
            items.append({
                "agent_name": row["agent_name"],
                "state": row["state"],
                "amount": int(row["amount"]) if row["amount"] is not None else 0,
                "address": row["address"],
                "company": row["company"],
                "paid_date": row["paid_date"],
                "notes": row["notes"],
                "status": row["status"],
            })

        total_pages = max(1, ceil(total_count / page_size)) if total_count else 1
        has_next = page < total_pages

        return PaginationResponseWithCount[Dict[str, Any]](
            success=True,
            data=PaginationData[Dict[str, Any]](
                total_count=total_count,
                items=items,
            ),
            page=page,
            page_size=page_size,
            count=len(items),
            total_pages=total_pages,
            has_next=has_next,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))