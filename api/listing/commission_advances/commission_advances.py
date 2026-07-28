from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, Optional
from math import ceil
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from common.pagination import PaginationData, PaginationResponseWithCount
from common.response import Response, FilterResponse
from fastapi.responses import JSONResponse
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

@router.get("/status-dropdown", response_model=FilterResponse)
def get_commission_advance_status_dropdown(db: Session = Depends(get_db)):
    try:
        statuses = [
            row["status"]
            for row in db.execute(text("""
                SELECT DISTINCT status
                FROM commission_advances
                WHERE status IS NOT NULL AND TRIM(status) <> ''
                ORDER BY status ASC
            """)).mappings().all()
        ]

        return FilterResponse(
            success=True,
            filters={
                "status": statuses,
            },
        )

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "message": "Failed to fetch commission advance status dropdown data",
                "details": [{"error": str(e)}],
            },
        )

@router.get("/listing", response_model=PaginationResponseWithCount[Dict[str, Any]])
def get_commission_advances_listing(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: Optional[str] = Query(
        None,
        description="Filter by status: Pending, Paid, Wage Garnishment"
    ),
    search: Optional[str] = Query(
        None,
        description="Search by agent name"
    ),
    db: Session = Depends(get_db),
):
    try:
        offset = (page - 1) * page_size

        allowed_statuses = {"Pending", "Paid", "Wage Garnishment", "Left ROA"}
        if status is not None and status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Allowed values: {', '.join(sorted(allowed_statuses))}"
            )

        where_clauses = []
        params: Dict[str, Any] = {
            "limit": page_size,
            "offset": offset,
        }

        if status:
            where_clauses.append("status = :status")
            params["status"] = status

        if search and search.strip():
            where_clauses.append("agent_name ILIKE :search")
            params["search"] = f"%{search.strip()}%"

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        total_count_query = text(f"""
            SELECT COUNT(*) AS total_count
            FROM (
                SELECT agent_name
                FROM commission_advances
                {where_sql}
                GROUP BY agent_name
            ) grouped_agents
        """)

        total_count = db.execute(total_count_query, params).scalar() or 0

        listing_query = text(f"""
            WITH filtered_data AS (
                SELECT
                    agent_name,
                    status,
                    amount
                FROM commission_advances
                {where_sql}
            ),
            agent_totals AS (
                SELECT
                    agent_name,
                    COALESCE(
                        SUM(amount) FILTER (
                            WHERE status IN ('Pending', 'Wage Garnishment')
                        ),
                        0
                    ) AS total_outstanding
                FROM filtered_data
                GROUP BY agent_name
            ),
            status_counts AS (
                SELECT
                    agent_name,
                    status,
                    COUNT(*) AS status_count
                FROM filtered_data
                WHERE status IS NOT NULL AND TRIM(status) <> ''
                GROUP BY agent_name, status
            ),
            status_breakdowns AS (
                SELECT
                    agent_name,
                    jsonb_object_agg(status, status_count ORDER BY status) AS status_breakdown
                FROM status_counts
                GROUP BY agent_name
            )
            SELECT
                at.agent_name,
                at.total_outstanding,
                COALESCE(sb.status_breakdown, '{{}}'::jsonb) AS status_breakdown
            FROM agent_totals at
            LEFT JOIN status_breakdowns sb
                ON at.agent_name = sb.agent_name
            ORDER BY at.total_outstanding DESC, at.agent_name ASC
            LIMIT :limit OFFSET :offset
        """)

        rows = db.execute(listing_query, params).mappings().all()

        items = []
        for row in rows:
            items.append({
                "agent_name": row["agent_name"],
                "total_outstanding": float(row["total_outstanding"] or 0),
                "status_breakdown": dict(row["status_breakdown"] or {}),
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

    except HTTPException:
        raise
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