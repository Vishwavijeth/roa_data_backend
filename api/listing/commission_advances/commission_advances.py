from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, Optional
from math import ceil
from decimal import Decimal
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
                WHERE status IN ('Pending', 'Pending Partial', 'Wage Garnishment')
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
                WHERE status IN ('Pending', 'Pending Partial', 'Wage Garnishment')
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
                ORDER BY
                    CASE status
                        WHEN 'Pending' THEN 1
                        WHEN 'Pending Partial' THEN 2
                        WHEN 'Wage Garnishment' THEN 3
                        WHEN 'Paid' THEN 4
                        WHEN 'Cancelled' THEN 5
                        ELSE 6
                    END,
                    status ASC
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
        description="Filter by status: Pending, Pending Partial, Wage Garnishment, Paid, Cancelled"
    ),
    search: Optional[str] = Query(
        None,
        description="Search by agent name"
    ),
    db: Session = Depends(get_db),
):
    try:
        offset = (page - 1) * page_size

        allowed_statuses = {
            "Pending",
            "Pending Partial",
            "Wage Garnishment",
            "Paid",
            "Cancelled",
            "Left ROA",
        }
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
            where_clauses.append("ca.status = :status")
            params["status"] = status

        if search and search.strip():
            where_clauses.append("ca.agent_name ILIKE :search")
            params["search"] = f"%{search.strip()}%"

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        total_count_query = text(f"""
            SELECT COUNT(*) AS total_count
            FROM (
                SELECT ca.agent_name
                FROM commission_advances ca
                {where_sql}
                GROUP BY ca.agent_name
            ) grouped_agents
        """)

        total_count = db.execute(total_count_query, params).scalar() or 0

        listing_query = text(f"""
            WITH filtered_data AS (
                SELECT
                    ca.agent_name,
                    ca.status,
                    ca.outstanding_amount
                FROM commission_advances ca
                {where_sql}
            ),
            agent_totals AS (
                SELECT
                    fd.agent_name,
                    COALESCE(
                        SUM(fd.outstanding_amount) FILTER (
                            WHERE fd.status IN ('Pending', 'Pending Partial', 'Wage Garnishment')
                        ),
                        0
                    ) AS total_outstanding
                FROM filtered_data fd
                GROUP BY fd.agent_name
            ),
            status_counts AS (
                SELECT
                    fd.agent_name,
                    fd.status,
                    COUNT(*) AS status_count
                FROM filtered_data fd
                WHERE fd.status IS NOT NULL AND TRIM(fd.status) <> ''
                GROUP BY fd.agent_name, fd.status
            ),
            status_breakdowns AS (
                SELECT
                    sc.agent_name,
                    jsonb_object_agg(sc.status, sc.status_count) AS status_breakdown
                FROM status_counts sc
                GROUP BY sc.agent_name
            ),
            agent_status_priority AS (
                SELECT
                    fd.agent_name,
                    SUM(CASE WHEN fd.status = 'Wage Garnishment' THEN 1 ELSE 0 END) AS wage_garnishment_count,
                    SUM(CASE WHEN fd.status = 'Pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN fd.status = 'Pending Partial' THEN 1 ELSE 0 END) AS pending_partial_count,
                    SUM(CASE WHEN fd.status = 'Paid' THEN 1 ELSE 0 END) AS paid_count,
                    SUM(CASE WHEN fd.status = 'Cancelled' THEN 1 ELSE 0 END) AS cancelled_count
                FROM filtered_data fd
                GROUP BY fd.agent_name
            ),
            agent_user_status AS (
                SELECT
                    beu.display_name AS agent_name,
                    MAX(beu.agent_status) AS agent_status
                FROM brokerage_engine_users beu
                GROUP BY beu.display_name
            )
            SELECT
                at.agent_name,
                at.total_outstanding,
                COALESCE(sb.status_breakdown, '{{}}'::jsonb) AS status_breakdown,
                aus.agent_status,
                asp.wage_garnishment_count,
                asp.pending_count,
                asp.pending_partial_count,
                asp.paid_count,
                asp.cancelled_count
            FROM agent_totals at
            LEFT JOIN status_breakdowns sb
                ON at.agent_name = sb.agent_name
            LEFT JOIN agent_status_priority asp
                ON at.agent_name = asp.agent_name
            LEFT JOIN agent_user_status aus
                ON at.agent_name = aus.agent_name
            ORDER BY
                CASE
                    WHEN COALESCE(asp.wage_garnishment_count, 0) > 0 THEN 1
                    WHEN COALESCE(asp.pending_count, 0) > 0 THEN 2
                    WHEN COALESCE(asp.pending_partial_count, 0) > 0 THEN 3
                    WHEN COALESCE(asp.paid_count, 0) > 0 THEN 4
                    WHEN COALESCE(asp.cancelled_count, 0) > 0 THEN 5
                    ELSE 6
                END ASC,
                at.total_outstanding DESC,
                at.agent_name ASC
            LIMIT :limit OFFSET :offset
        """)

        rows = db.execute(listing_query, params).mappings().all()

        items = []
        for row in rows:
            raw_breakdown = dict(row["status_breakdown"] or {})

            ordered_status_breakdown = {}
            for key in ["Wage Garnishment", "Pending", "Pending Partial", "Paid", "Cancelled"]:
                if key in raw_breakdown:
                    ordered_status_breakdown[key] = raw_breakdown[key]

            items.append({
                "agent_name": row["agent_name"],
                "total_outstanding": float(row["total_outstanding"] or 0),
                "status_breakdown": ordered_status_breakdown,
                "agent_status": row["agent_status"],
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
                FROM commission_advances
                WHERE agent_name = :agent_name
                ORDER BY paid_date DESC NULLS LAST, status ASC, id DESC
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
                "id": row["id"],
                "agent_name": row["agent_name"],
                "state": row["state"],
                "address": row["address"],
                "company": row["company"],
                "original_amount": float(row["original_amount"] or 0),
                "amount_paid": float(row["amount_paid"] or 0),
                "outstanding_amount": float(row["outstanding_amount"] or 0),
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