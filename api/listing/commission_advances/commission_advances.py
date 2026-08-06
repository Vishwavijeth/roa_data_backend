from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, Optional
from math import ceil
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy import JSON, and_, case, cast, distinct, func, literal, select
from common.pagination import PaginationData, PaginationResponseWithCount
from common.response import Response, FilterResponse
from fastapi.responses import JSONResponse
from api.listing.commission_advances.base import CommissionAdvanceSummary
from models.commisison_advances import CommissionAdvance, CommissionAdvanceHistory
from api.listing.commission_advances.utils import CommissionAdvanceStatus
from models.brokerage_engine_users import BrokerageEngineUser
from db import get_db

router = APIRouter(prefix="/commission-advances")

def build_listing_filters(
    status: Optional[CommissionAdvanceStatus],
    search: Optional[str],
):
    filters = []

    if status:
        filters.append(CommissionAdvance.status == status.value)

    if search and search.strip():
        filters.append(
            CommissionAdvance.agent_name.ilike(f"%{search.strip()}%")
        )

    return filters


@router.get("/summary", response_model=Response[CommissionAdvanceSummary])
def get_commission_advances_summary(
    db: Session = Depends(get_db),
):
    try:
        ca = CommissionAdvance

        pending_advances = (
            db.execute(
                select(func.count(ca.id)).where(
                    ca.status.in_(
                        CommissionAdvanceStatus.active_values()
                    )
                )
            ).scalar()
            or 0
        )

        commission_advance_received = (
            db.execute(
                select(func.count(ca.id)).where(
                    ca.status == CommissionAdvanceStatus.PAID.value
                )
            ).scalar()
            or 0
        )

        agents_with_active_advances = (
            db.execute(
                select(func.count(distinct(ca.agent_name))).where(
                    ca.outstanding_amount > 0
                )
            ).scalar()
            or 0
        )

        return Response[CommissionAdvanceSummary](
            success=True,
            data=CommissionAdvanceSummary(
                pending_advances=pending_advances,
                commission_advance_received=commission_advance_received,
                agents_with_active_advances=agents_with_active_advances,
            ),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.get(
    "/status-dropdown",
    response_model=FilterResponse,
)
def get_commission_advance_status_dropdown():
    return FilterResponse(
        success=True,
        filters={
            "status": CommissionAdvanceStatus.values(),
        },
    )


@router.get("/listing", response_model=PaginationResponseWithCount[Dict[str, Any]])
def get_commission_advances_listing(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: Optional[CommissionAdvanceStatus] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    try:
        ca = CommissionAdvance
        beu = BrokerageEngineUser
        offset = (page - 1) * page_size
        filters = build_listing_filters(status, search)

        total_count = (
            db.execute(
                select(func.count(distinct(ca.agent_name))).where(*filters)
            ).scalar()
            or 0
        )

        filtered_data = (
            select(
                ca.agent_name,
                ca.status,
                ca.outstanding_amount,
            )
            .where(*filters)
            .cte("filtered_data")
        )

        agent_totals = (
            select(
                filtered_data.c.agent_name,
                func.coalesce(
                    func.sum(filtered_data.c.outstanding_amount).filter(
                        filtered_data.c.status.in_(
                            CommissionAdvanceStatus.active_values()
                        )
                    ),
                    0,
                ).label("total_outstanding"),
            )
            .group_by(filtered_data.c.agent_name)
            .cte("agent_totals")
        )

        status_counts = (
            select(
                filtered_data.c.agent_name,
                filtered_data.c.status,
                func.count().label("status_count"),
            )
            .where(
                filtered_data.c.status.isnot(None),
                func.trim(filtered_data.c.status) != "",
            )
            .group_by(
                filtered_data.c.agent_name,
                filtered_data.c.status,
            )
            .cte("status_counts")
        )

        status_breakdowns = (
            select(
                status_counts.c.agent_name,
                func.jsonb_object_agg(
                    status_counts.c.status,
                    status_counts.c.status_count,
                ).label("status_breakdown"),
            )
            .group_by(status_counts.c.agent_name)
            .cte("status_breakdowns")
        )

        priority_expression = case(
            (
                filtered_data.c.status
                == CommissionAdvanceStatus.WAGE_GARNISHMENT.value,
                1,
            ),
            (
                filtered_data.c.status
                == CommissionAdvanceStatus.PENDING.value,
                2,
            ),
            (
                filtered_data.c.status
                == CommissionAdvanceStatus.PENDING_PARTIAL.value,
                3,
            ),
            (
                filtered_data.c.status
                == CommissionAdvanceStatus.PAID.value,
                4,
            ),
            (
                filtered_data.c.status
                == CommissionAdvanceStatus.CANCELLED.value,
                5,
            ),
            (
                filtered_data.c.status
                == CommissionAdvanceStatus.LEFT_ROA.value,
                6,
            ),
            else_=7,
        )

        agent_status_priority = (
            select(
                filtered_data.c.agent_name,
                func.min(priority_expression).label("status_priority"),
            )
            .group_by(filtered_data.c.agent_name)
            .cte("agent_status_priority")
        )

        agent_user_status = (
            select(
                beu.display_name.label("agent_name"),
                func.max(beu.agent_status).label("agent_status"),
            )
            .group_by(beu.display_name)
            .cte("agent_user_status")
        )

        empty_json_object = cast(literal({}), JSON)

        listing_stmt = (
            select(
                agent_totals.c.agent_name,
                agent_totals.c.total_outstanding,
                func.coalesce(
                    status_breakdowns.c.status_breakdown,
                    empty_json_object,
                ).label("status_breakdown"),
                agent_user_status.c.agent_status,
            )
            .select_from(agent_totals)
            .outerjoin(
                status_breakdowns,
                agent_totals.c.agent_name
                == status_breakdowns.c.agent_name,
            )
            .outerjoin(
                agent_status_priority,
                agent_totals.c.agent_name
                == agent_status_priority.c.agent_name,
            )
            .outerjoin(
                agent_user_status,
                agent_totals.c.agent_name
                == agent_user_status.c.agent_name,
            )
            .order_by(
                agent_status_priority.c.status_priority.asc(),
                agent_totals.c.total_outstanding.desc(),
                agent_totals.c.agent_name.asc(),
            )
            .limit(page_size)
            .offset(offset)
        )

        rows = db.execute(listing_stmt).mappings().all()

        status_order = [
            CommissionAdvanceStatus.WAGE_GARNISHMENT.value,
            CommissionAdvanceStatus.PENDING.value,
            CommissionAdvanceStatus.PENDING_PARTIAL.value,
            CommissionAdvanceStatus.PAID.value,
            CommissionAdvanceStatus.CANCELLED.value,
            CommissionAdvanceStatus.LEFT_ROA.value,
        ]

        items = []

        for row in rows:
            raw_breakdown = row["status_breakdown"] or {}

            ordered_breakdown = {
                status_name: raw_breakdown[status_name]
                for status_name in status_order
                if status_name in raw_breakdown
            }

            items.append(
                {
                    "agent_name": row["agent_name"],
                    "total_outstanding": float(
                        row["total_outstanding"] or 0
                    ),
                    "status_breakdown": ordered_breakdown,
                    "agent_status": row["agent_status"],
                }
            )

        total_pages = (
            max(1, ceil(total_count / page_size))
            if total_count
            else 1
        )

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
            has_next=page < total_pages,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.get("/detail", response_model=PaginationResponseWithCount[Dict[str, Any]])
def get_commission_advances_detail(
    agent_name: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        ca = CommissionAdvance
        history = CommissionAdvanceHistory
        offset = (page - 1) * page_size

        total_count = db.execute(
            select(func.count(ca.id)).where(
                ca.agent_name == agent_name
            )
        ).scalar() or 0

        status_order = case(
            (ca.status == CommissionAdvanceStatus.WAGE_GARNISHMENT.value, 1),
            (ca.status == CommissionAdvanceStatus.PENDING.value, 2),
            (ca.status == CommissionAdvanceStatus.PENDING_PARTIAL.value, 3),
            (ca.status == CommissionAdvanceStatus.PAID.value, 4),
            (ca.status == CommissionAdvanceStatus.LEFT_ROA.value, 5),
            (ca.status == CommissionAdvanceStatus.CANCELLED.value, 6),
            else_=7,
        )

        detail_stmt = (
            select(
                ca.id.label("ca_id"),
                ca.agent_name,
                ca.state,
                ca.address,
                ca.company,
                ca.original_amount,
                ca.amount_paid,
                ca.outstanding_amount,
                ca.approved_date,
                ca.paid_date,
                ca.notes,
                ca.status,
                history.id.label("history_id"),
                history.field.label("history_field"),
                history.old_value.label("history_old_value"),
                history.new_value.label("history_new_value"),
                history.edited_at.label("history_edited_at"),
            )
            .select_from(ca)
            .outerjoin(history, history.ca_id == ca.id)
            .where(ca.agent_name == agent_name)
            .order_by(
                status_order.asc(),
                ca.paid_date.desc().nullslast(),
                ca.id.desc(),
                history.edited_at.desc().nullslast(),
                history.id.desc().nullslast(),
            )
            .limit(page_size)
            .offset(offset)
        )

        rows = db.execute(detail_stmt).mappings().all()

        items_by_id: Dict[int, Dict[str, Any]] = {}
        history_by_transaction: Dict[int, Dict[Any, Dict[str, Any]]] = {}

        for row in rows:
            transaction_id = row["ca_id"]

            if transaction_id not in items_by_id:
                items_by_id[transaction_id] = {
                    "id": row["ca_id"],
                    "agent_name": row["agent_name"],
                    "state": row["state"],
                    "address": row["address"],
                    "company": row["company"],
                    "original_amount": float(row["original_amount"] or 0),
                    "amount_paid": float(row["amount_paid"] or 0),
                    "outstanding_amount": float(row["outstanding_amount"] or 0),
                    "approved_date": row["approved_date"],
                    "paid_date": row["paid_date"],
                    "notes": row["notes"],
                    "status": row["status"],
                    "history": [],
                }

                history_by_transaction[transaction_id] = {}

            if row["history_id"] is None:
                continue

            edited_at = row["history_edited_at"]
            transaction_history = history_by_transaction[transaction_id]

            if edited_at not in transaction_history:
                transaction_history[edited_at] = {
                    "edited_at": edited_at,
                    "changes": [],
                }

            transaction_history[edited_at]["changes"].append(
                {
                    "field": row["history_field"],
                    "old_value": row["history_old_value"],
                    "new_value": row["history_new_value"],
                }
            )

        for transaction_id, grouped_history in history_by_transaction.items():
            items_by_id[transaction_id]["history"] = list(
                grouped_history.values()
            )

        items = list(items_by_id.values())

        total_pages = max(1, ceil(total_count / page_size)) if total_count else 1

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
            has_next=page < total_pages,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc