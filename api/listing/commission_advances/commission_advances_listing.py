from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, Optional
from math import ceil

from pydantic import BaseModel
from sqlalchemy import case, cast, exists, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from api.listing.commission_advances.base import CommissionAdvanceSummary
from api.listing.commission_advances.utils import CommissionAdvanceStatus, CommissionAdvanceGarnishmentStatus
from common.pagination import PaginationData, PaginationResponseWithCount
from common.response import Response
from db import get_db
from models.brokerage_engine_users import BrokerageEngineUser
from models.commission_advances.commission_advances import CommissionAdvance, CommissionAdvanceTransaction, CommissionAdvanceGarnishment


router = APIRouter(prefix="/commission-advances")


class CommissionAdvanceDetailData(BaseModel):
    total_count: int
    global_outstanding: float
    items: list[Dict[str, Any]]


class CommissionAdvanceDetailResponse(BaseModel):
    success: bool = True
    data: CommissionAdvanceDetailData
    page: int
    page_size: int
    count: int
    total_pages: int
    has_next: bool
    message: str = "Request successful"


def build_listing_filters(status: Optional[CommissionAdvanceStatus], search: Optional[str]):
    filters = []

    if status:
        filters.append(CommissionAdvance.status == status.value)

    if search and search.strip():
        search_term = search.strip()
        filters.append(
            or_(
                CommissionAdvance.agent_name.ilike(f"%{search_term}%"),
                CommissionAdvance.address.ilike(f"%{search_term}%"),
            )
        )

    return filters


def build_global_outstanding_cte():
    ca = CommissionAdvance
    transaction = CommissionAdvanceTransaction
    garnishment = CommissionAdvanceGarnishment

    # Normal advances only.
    # Any CA having a garnishment_id in its ledger is excluded because
    # that debt is represented by the garnishment master.
    normal_debt = (
        select(
            ca.agent_name.label("agent_name"),
            ca.outstanding_amount.label("outstanding_amount"),
        )
        .where(
            ca.agent_name.isnot(None),
            func.trim(ca.agent_name) != "",
            ca.outstanding_amount > 0,
            ca.status.in_(CommissionAdvanceStatus.active_values()),
            ~exists().where(
                CommissionAdvanceTransaction.ca_id == ca.id,
                CommissionAdvanceTransaction.garnishment_id.is_not(None),
            ),
        )
    )

    # Once an advance enters Wage Garnishment, this becomes the
    # authoritative outstanding balance for that entire lifecycle.
    garnishment_debt = (
        select(
            garnishment.agent_name.label("agent_name"),
            garnishment.outstanding_amount.label("outstanding_amount"),
        )
        .where(
            garnishment.agent_name.isnot(None),
            func.trim(garnishment.agent_name) != "",
            garnishment.status == CommissionAdvanceGarnishmentStatus.ACTIVE.value,
            garnishment.outstanding_amount > 0,
        )
    )

    debt_components = union_all(normal_debt, garnishment_debt).subquery("debt_components")

    return (
        select(
            debt_components.c.agent_name,
            func.coalesce(func.sum(debt_components.c.outstanding_amount), 0).label("total_outstanding"),
        )
        .group_by(debt_components.c.agent_name)
        .cte("global_outstanding")
    )


@router.get(
    "/summary",
    response_model=Response[CommissionAdvanceSummary],
)
def get_commission_advances_summary(db: Session = Depends(get_db)):
    try:
        ca = CommissionAdvance

        pending_advances = db.scalar(
            select(func.count(ca.id)).where(ca.status.in_(CommissionAdvanceStatus.active_values()))
        ) or 0

        commission_advance_received = db.scalar(
            select(func.count(ca.id)).where(ca.status == CommissionAdvanceStatus.PAID.value)
        ) or 0

        global_outstanding = build_global_outstanding_cte()

        agents_with_active_advances = db.scalar(
            select(func.count()).select_from(global_outstanding).where(global_outstanding.c.total_outstanding > 0)
        ) or 0

        return Response[CommissionAdvanceSummary](
            success=True,
            data=CommissionAdvanceSummary(
                pending_advances=pending_advances,
                commission_advance_received=commission_advance_received,
                agents_with_active_advances=agents_with_active_advances,
            ),
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/listing",
    response_model=PaginationResponseWithCount[Dict[str, Any]],
)
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

        # Filters decide which agents appear.
        # They do not control the global outstanding calculation.
        filtered_data = (
            select(ca.agent_name, ca.status)
            .where(*filters)
            .cte("filtered_data")
        )

        filtered_agents = (
            select(filtered_data.c.agent_name)
            .where(
                filtered_data.c.agent_name.isnot(None),
                func.trim(filtered_data.c.agent_name) != "",
            )
            .distinct()
            .cte("filtered_agents")
        )

        total_count = db.scalar(
            select(func.count()).select_from(filtered_agents)
        ) or 0

        global_outstanding = build_global_outstanding_cte()

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
            .group_by(filtered_data.c.agent_name, filtered_data.c.status)
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
            (filtered_data.c.status == CommissionAdvanceStatus.WAGE_GARNISHMENT.value, 1),
            (filtered_data.c.status == CommissionAdvanceStatus.PENDING.value, 2),
            (filtered_data.c.status == CommissionAdvanceStatus.PENDING_PARTIAL.value, 3),
            (filtered_data.c.status == CommissionAdvanceStatus.PAID.value, 4),
            (filtered_data.c.status == CommissionAdvanceStatus.CANCELLED.value, 5),
            (filtered_data.c.status == CommissionAdvanceStatus.LEFT_ROA.value, 6),
            (filtered_data.c.status == CommissionAdvanceStatus.REPLACEMENT.value, 7),
            else_=8,
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

        empty_json_object = cast(literal("{}"), JSONB)

        listing_stmt = (
            select(
                filtered_agents.c.agent_name,
                func.coalesce(global_outstanding.c.total_outstanding, 0).label("total_outstanding"),
                func.coalesce(status_breakdowns.c.status_breakdown, empty_json_object).label("status_breakdown"),
                agent_user_status.c.agent_status,
            )
            .select_from(filtered_agents)
            .outerjoin(
                global_outstanding,
                filtered_agents.c.agent_name == global_outstanding.c.agent_name,
            )
            .outerjoin(
                status_breakdowns,
                filtered_agents.c.agent_name == status_breakdowns.c.agent_name,
            )
            .outerjoin(
                agent_status_priority,
                filtered_agents.c.agent_name == agent_status_priority.c.agent_name,
            )
            .outerjoin(
                agent_user_status,
                filtered_agents.c.agent_name == agent_user_status.c.agent_name,
            )
            .order_by(
                agent_status_priority.c.status_priority.asc(),
                func.coalesce(global_outstanding.c.total_outstanding, 0).desc(),
                filtered_agents.c.agent_name.asc(),
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
            CommissionAdvanceStatus.REPLACEMENT.value,
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
                    "total_outstanding": float(row["total_outstanding"] or 0),
                    "status_breakdown": ordered_breakdown,
                    "agent_status": row["agent_status"],
                }
            )

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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/detail",
    response_model=CommissionAdvanceDetailResponse,
)
def get_commission_advances_detail(
    agent_name: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        ca = CommissionAdvance
        transaction = CommissionAdvanceTransaction
        offset = (page - 1) * page_size

        total_count = db.scalar(
            select(func.count(ca.id)).where(ca.agent_name == agent_name)
        ) or 0

        # Global outstanding is calculated independently from pagination.
        # Do not sum the outstanding values of the returned CA items.
        global_outstanding_cte = build_global_outstanding_cte()

        global_outstanding = db.scalar(
            select(global_outstanding_cte.c.total_outstanding)
            .where(global_outstanding_cte.c.agent_name == agent_name)
        ) or 0

        paginated_ca_ids = (
            select(ca.id)
            .where(ca.agent_name == agent_name)
            .order_by(ca.id.asc())
            .limit(page_size)
            .offset(offset)
            .subquery()
        )

        detail_stmt = (
            select(
                ca.id.label("ca_id"),
                ca.agent_id,
                ca.agent_name,
                ca.state,
                ca.address,
                ca.company,
                ca.original_amount,
                ca.amount_paid,
                ca.outstanding_amount,
                ca.saleguid,
                ca.approved_date,
                ca.paid_date,
                ca.notes,
                ca.status,
                transaction.id.label("transaction_id"),
                transaction.garnishment_id,
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
            .join(paginated_ca_ids, paginated_ca_ids.c.id == ca.id)
            .outerjoin(transaction, transaction.ca_id == ca.id)
            .order_by(
                ca.id.asc(),
                transaction.id.asc().nullslast(),
            )
        )

        rows = db.execute(detail_stmt).mappings().all()

        items_by_id: Dict[int, Dict[str, Any]] = {}

        for row in rows:
            ca_id = row["ca_id"]

            if ca_id not in items_by_id:
                items_by_id[ca_id] = {
                    "id": row["ca_id"],
                    "agent_id": str(row["agent_id"]) if row["agent_id"] is not None else None,
                    "agent_name": row["agent_name"],
                    "state": row["state"],
                    "address": row["address"],
                    "company": row["company"],
                    "original_amount": float(row["original_amount"] or 0),
                    "amount_paid": float(row["amount_paid"] or 0),
                    "outstanding_amount": float(row["outstanding_amount"] or 0),
                    "saleguid": str(row["saleguid"]) if row["saleguid"] is not None else None,
                    "approved_date": row["approved_date"],
                    "paid_date": row["paid_date"],
                    "notes": row["notes"],
                    "status": row["status"],
                    "transactions": [],
                }

            if row["transaction_id"] is None:
                continue

            items_by_id[ca_id]["transactions"].append(
                {
                    "id": row["transaction_id"],
                    "garnishment_id": row["garnishment_id"],
                    "operation": row["operation"],
                    "type": row["transaction_type"],
                    "amount": float(row["transaction_amount"] or 0),
                    "transaction_date": row["transaction_date"],
                    "notes": row["transaction_notes"],
                    "outstanding_amount": float(row["transaction_outstanding_amount"] or 0),
                    "created_by": row["created_by"],
                    "updated_at": row["updated_at"],
                }
            )

        items = list(items_by_id.values())
        total_pages = max(1, ceil(total_count / page_size)) if total_count else 1

        return CommissionAdvanceDetailResponse(
            success=True,
            data=CommissionAdvanceDetailData(
                total_count=total_count,
                global_outstanding=float(global_outstanding or 0),
                items=items,
            ),
            page=page,
            page_size=page_size,
            count=len(items),
            total_pages=total_pages,
            has_next=page < total_pages,
            message="Request successful",
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc