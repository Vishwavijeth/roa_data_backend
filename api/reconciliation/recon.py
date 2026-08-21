from typing import Optional, List, Any
from uuid import UUID as PythonUUID

from fastapi import APIRouter, Query, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import Date, String, and_, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from db import get_db
from common.pagination import PaginationData, PaginationResponseWithCount
from common.response import Response as APIResponse
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
import pandas as pd
import io
import datetime
from decimal import Decimal
from models.reconciliation_data import ReconciliationData, ReconciliationReview
from models.skyslope.sale import Sale
from models.skyslope.meta import Stage


router = APIRouter()


# ============================================================
# DISPLAY / FILTER CONFIGURATION
# ============================================================

PARAMETER_DISPLAY_NAMES = {
    "gross_commission": "Gross Commission",
    "close_date": "Close Date",
    "status": "Status",
    "sale_price": "Sale Price",
    "listing_price": "Listing Price",
    "contract_date": "Contract Date",
    "buyer_name": "Buyer Name",
    "seller_name": "Seller Name",
    "buying_agent_name": "Buying Agent Name",
    "title_company": "Title Company",
}


SOURCE_TABLE_DISPLAY_NAMES = {
    "sale income": "sale income",
    "other income": "other income",
}


SOURCE_TABLE_FILTER_MAP = {
    "sale income": "sale income",
    "other income": "other income",
}


EXPORT_PARAMETER_CONFIG = {
    "gross_commission": {
        "be_attr": "be_gross_commission",
        "ss_attr": "skyslope_gross_commission",
        "match_attr": "gross_commission_match",
        "be_header": "BE Gross Commission",
        "ss_header": "Skyslope Gross Commission",
        "match_header": "Gross Commission Match",
    },
    "close_date": {
        "be_attr": "be_close_date_value",
        "ss_attr": "skyslope_close_date_value",
        "match_attr": "close_date_match",
        "be_header": "BE Close Date",
        "ss_header": "Skyslope Close Date",
        "match_header": "Close Date Match",
    },
    "status": {
        "be_attr": "be_status_value",
        "ss_attr": "skyslope_status_value",
        "match_attr": "status_match",
        "be_header": "BE Status",
        "ss_header": "Skyslope Status",
        "match_header": "Status Match",
    },
    "sale_price": {
        "be_attr": "be_sale_price",
        "ss_attr": "skyslope_sale_price",
        "match_attr": "sale_price_match",
        "be_header": "BE Sale Price",
        "ss_header": "Skyslope Sale Price",
        "match_header": "Sale Price Match",
    },
    "listing_price": {
        "be_attr": "be_listing_price",
        "ss_attr": "skyslope_listing_price",
        "match_attr": "listing_price_match",
        "be_header": "BE Listing Price",
        "ss_header": "Skyslope Listing Price",
        "match_header": "Listing Price Match",
    },
    "contract_date": {
        "be_attr": "be_contract_date",
        "ss_attr": "skyslope_contract_date",
        "match_attr": "contract_date_match",
        "be_header": "BE Contract Date",
        "ss_header": "Skyslope Contract Date",
        "match_header": "Contract Date Match",
    },
    "buyer_name": {
        "be_attr": "be_buyer_name",
        "ss_attr": "skyslope_buyer_name",
        "match_attr": "buyer_name_match",
        "be_header": "BE Buyer Name",
        "ss_header": "Skyslope Buyer Name",
        "match_header": "Buyer Name Match",
    },
    "seller_name": {
        "be_attr": "be_seller_name",
        "ss_attr": "skyslope_seller_name",
        "match_attr": "seller_name_match",
        "be_header": "BE Seller Name",
        "ss_header": "Skyslope Seller Name",
        "match_header": "Seller Name Match",
    },
    "buying_agent_name": {
        "be_attr": "be_buying_agent_name",
        "ss_attr": "skyslope_buying_agent_name",
        "match_attr": "buying_agent_match",
        "be_header": "BE Buying Agent Name",
        "ss_header": "Skyslope Buying Agent Name",
        "match_header": "Buying Agent Match",
    },
    "title_company": {
        "be_attr": "be_title_company",
        "ss_attr": "skyslope_title_company",
        "match_attr": "title_company_match",
        "be_header": "BE Title Company",
        "ss_header": "Skyslope Title Company",
        "match_header": "Title Company Match",
    },
}


# ============================================================
# PARAMETER PARSING
# ============================================================

def parse_mismatch_params(mismatch_parameter: Optional[List[str]]) -> List[str]:
    parsed = []

    if mismatch_parameter:
        for value in mismatch_parameter:
            for part in value.split(","):
                normalized = part.strip().lower().replace(" ", "_")
                if normalized:
                    parsed.append(normalized)

    return parsed


def parse_source_table_params(source_table: Optional[List[str]]) -> List[str]:
    parsed = []

    if source_table:
        for value in source_table:
            for part in value.split(","):
                normalized = part.strip().lower()
                mapped_value = SOURCE_TABLE_FILTER_MAP.get(normalized)

                if mapped_value:
                    parsed.append(mapped_value)

    return parsed


def parse_text_list_params(values: Optional[List[str]]) -> List[str]:
    parsed = []

    if values:
        for value in values:
            for part in value.split(","):
                normalized = part.strip().lower()

                if normalized:
                    parsed.append(normalized)

    return parsed


# ============================================================
# OPTIMIZED BASE RECONCILIATION QUERY
# ============================================================

def build_base_reconciliation_subquery():
    rd = ReconciliationData

    source_priority = case(
        (func.lower(rd.be_source_table) == "other income", 0),
        (func.lower(rd.be_source_table) == "sale income", 1),
        else_=2,
    )

    saleguid_group_flags = (
        select(
            rd.saleguid.label("saleguid"),
            func.bool_or(func.lower(rd.be_source_table) == "sale income").label("has_sale_income"),
            func.bool_or(func.lower(rd.be_source_table) == "other income").label("has_other_income"),
        )
        .where(rd.saleguid.is_not(None))
        .group_by(rd.saleguid)
        .cte("saleguid_group_flags")
    )

    deduplicated_reconciliation = (
        select(
            rd.transactionid.label("transactionid"),
            rd.be_source_table.label("source_table"),
            rd.saleguid.label("saleguid"),
            rd.property_address.label("propertyaddress"),
            rd.be_close_date.label("be_close_date"),
            rd.be_status.label("be_status"),
            rd.be_transaction_specialist.label("be_transaction_specialist"),
            rd.skyslope_reviewer.label("skyslope_reviewer"),
            rd.gross_commission_match.label("gross_commission_match"),
            rd.close_date_match.label("close_date_match"),
            rd.status_match.label("status_match"),
            rd.sale_price_match.label("sale_price_match"),
            rd.listing_price_match.label("listing_price_match"),
            rd.contract_date_match.label("contract_date_match"),
            rd.buyer_name_match.label("buyer_name_match"),
            rd.seller_name_match.label("seller_name_match"),
            rd.buying_agent_match.label("buying_agent_match"),
            rd.title_company_match.label("title_company_match"),
        )
        .where(rd.saleguid.is_not(None))
        .distinct(rd.saleguid)
        .order_by(rd.saleguid, source_priority, rd.transactionid)
        .cte("deduplicated_reconciliation")
    )

    linked_rows = (
        select(
            deduplicated_reconciliation.c.transactionid,
            deduplicated_reconciliation.c.source_table,
            deduplicated_reconciliation.c.saleguid,
            deduplicated_reconciliation.c.propertyaddress,
            deduplicated_reconciliation.c.be_close_date,
            deduplicated_reconciliation.c.be_status,
            deduplicated_reconciliation.c.be_transaction_specialist,
            deduplicated_reconciliation.c.skyslope_reviewer,
            deduplicated_reconciliation.c.gross_commission_match,
            deduplicated_reconciliation.c.close_date_match,
            deduplicated_reconciliation.c.status_match,
            deduplicated_reconciliation.c.sale_price_match,
            deduplicated_reconciliation.c.listing_price_match,
            deduplicated_reconciliation.c.contract_date_match,
            deduplicated_reconciliation.c.buyer_name_match,
            deduplicated_reconciliation.c.seller_name_match,
            deduplicated_reconciliation.c.buying_agent_match,
            deduplicated_reconciliation.c.title_company_match,
            saleguid_group_flags.c.has_sale_income,
            saleguid_group_flags.c.has_other_income,
            Stage.name.label("skyslope_stage"),
            ReconciliationReview.review_status.label("review_status"),
            ReconciliationReview.notes.label("review_notes"),
            ReconciliationReview.updated_by.label("review_updated_by"),
            ReconciliationReview.updated_at.label("review_updated_at"),
            Sale.url.label("skyslope_url"),
        )
        .select_from(
            deduplicated_reconciliation
            .join(saleguid_group_flags, saleguid_group_flags.c.saleguid == deduplicated_reconciliation.c.saleguid)
            .outerjoin(Sale, Sale.saleguid == deduplicated_reconciliation.c.saleguid)
            .outerjoin(Stage, Stage.stageid == Sale.stageid)
            .outerjoin(ReconciliationReview, ReconciliationReview.transactionid == deduplicated_reconciliation.c.transactionid)
        )
    )

    unlinked_rows = (
        select(
            rd.transactionid.label("transactionid"),
            rd.be_source_table.label("source_table"),
            rd.saleguid.label("saleguid"),
            rd.property_address.label("propertyaddress"),
            rd.be_close_date.label("be_close_date"),
            rd.be_status.label("be_status"),
            rd.be_transaction_specialist.label("be_transaction_specialist"),
            rd.skyslope_reviewer.label("skyslope_reviewer"),
            rd.gross_commission_match.label("gross_commission_match"),
            rd.close_date_match.label("close_date_match"),
            rd.status_match.label("status_match"),
            rd.sale_price_match.label("sale_price_match"),
            rd.listing_price_match.label("listing_price_match"),
            rd.contract_date_match.label("contract_date_match"),
            rd.buyer_name_match.label("buyer_name_match"),
            rd.seller_name_match.label("seller_name_match"),
            rd.buying_agent_match.label("buying_agent_match"),
            rd.title_company_match.label("title_company_match"),
            (func.lower(rd.be_source_table) == "sale income").label("has_sale_income"),
            (func.lower(rd.be_source_table) == "other income").label("has_other_income"),
            cast(literal(None), String).label("skyslope_stage"),
            ReconciliationReview.review_status.label("review_status"),
            ReconciliationReview.notes.label("review_notes"),
            ReconciliationReview.updated_by.label("review_updated_by"),
            ReconciliationReview.updated_at.label("review_updated_at"),
            cast(literal(None), String).label("skyslope_url"),
        )
        .select_from(rd)
        .outerjoin(ReconciliationReview, ReconciliationReview.transactionid == rd.transactionid)
        .where(rd.saleguid.is_(None))
    )

    return union_all(linked_rows, unlinked_rows).subquery("cs")


# ============================================================
# FILTER BUILDER
# ============================================================

def build_filter_conditions(
    cs,
    search: Optional[str],
    parsed_mismatch_params: List[str],
    parsed_source_tables: Optional[List[str]] = None,
    from_close_date: Optional[str] = None,
    to_close_date: Optional[str] = None,
    status: Optional[List[str]] = None,
    skyslope_stage: Optional[List[str]] = None,
    review_status: Optional[List[str]] = None,
    specialist: Optional[List[str]] = None,
    reviewer: Optional[List[str]] = None,
    saleincome_no_skyslopefileid: Optional[bool] = None,
    otherincome_no_skyslopefileid: Optional[bool] = None,
):
    conditions = []

    if search:
        search_term = f"%{search}%"

        conditions.append(
            or_(
                cast(cs.c.transactionid, String).ilike(search_term),
                cs.c.propertyaddress.ilike(search_term),
            )
        )

    if parsed_source_tables:
        source_table_conditions = []

        if "sale income" in parsed_source_tables:
            source_table_conditions.append(
                and_(
                    cs.c.has_sale_income.is_(True),
                    cs.c.has_other_income.is_(False),
                )
            )

        if "other income" in parsed_source_tables:
            source_table_conditions.append(
                cs.c.has_other_income.is_(True)
            )

        if source_table_conditions:
            conditions.append(or_(*source_table_conditions))

    if from_close_date:
        conditions.append(
            cs.c.be_close_date >= cast(literal(from_close_date), Date)
        )

    if to_close_date:
        conditions.append(
            cs.c.be_close_date <= cast(literal(to_close_date), Date)
        )

    if status:
        normalized_status = [
            value.strip().lower()
            for value in status
            if value and value.strip()
        ]

        if normalized_status:
            conditions.append(
                func.lower(cs.c.be_status).in_(normalized_status)
            )

    if skyslope_stage:
        normalized_stages = [
            value.strip().lower()
            for value in skyslope_stage
            if value and value.strip()
        ]

        if normalized_stages:
            conditions.append(
                func.lower(cs.c.skyslope_stage).in_(normalized_stages)
            )

    if review_status:
        normalized_review_filters = [
            value.strip().lower()
            for value in review_status
            if value and value.strip()
        ]

        review_conditions = []

        allowed_review_statuses = [
            value
            for value in normalized_review_filters
            if value in {"in_review", "review_done", "not_a_mismatch"}
        ]

        if allowed_review_statuses:
            # Controlled DB values: avoid LOWER() so PostgreSQL can use
            # an index on review_status directly.
            review_conditions.append(
                cs.c.review_status.in_(allowed_review_statuses)
            )

        if "not_reviewed" in normalized_review_filters:
            review_conditions.append(cs.c.review_status.is_(None))

        if review_conditions:
            conditions.append(or_(*review_conditions))

    parsed_specialists = parse_text_list_params(specialist)

    if parsed_specialists:
        normalized_specialist_column = func.lower(
            func.trim(cs.c.be_transaction_specialist)
        )

        if "unassigned" in parsed_specialists:
            non_unassigned_specialists = [
                value
                for value in parsed_specialists
                if value != "unassigned"
            ]

            specialist_conditions = [
                func.coalesce(
                    func.nullif(normalized_specialist_column, ""),
                    "unassigned",
                ) == "unassigned"
            ]

            if non_unassigned_specialists:
                specialist_conditions.append(
                    normalized_specialist_column.in_(
                        non_unassigned_specialists
                    )
                )

            conditions.append(or_(*specialist_conditions))
        else:
            conditions.append(
                normalized_specialist_column.in_(parsed_specialists)
            )

    parsed_reviewers = parse_text_list_params(reviewer)

    if parsed_reviewers:
        normalized_reviewer_column = func.lower(
            func.trim(cs.c.skyslope_reviewer)
        )

        if "unassigned" in parsed_reviewers:
            non_unassigned_reviewers = [
                value
                for value in parsed_reviewers
                if value != "unassigned"
            ]

            reviewer_conditions = [
                func.coalesce(
                    func.nullif(normalized_reviewer_column, ""),
                    "unassigned",
                ) == "unassigned"
            ]

            if non_unassigned_reviewers:
                reviewer_conditions.append(
                    normalized_reviewer_column.in_(
                        non_unassigned_reviewers
                    )
                )

            conditions.append(or_(*reviewer_conditions))
        else:
            conditions.append(
                normalized_reviewer_column.in_(parsed_reviewers)
            )

    mismatch_columns = {
        "gross_commission": cs.c.gross_commission_match,
        "close_date": cs.c.close_date_match,
        "status": cs.c.status_match,
        "sale_price": cs.c.sale_price_match,
        "listing_price": cs.c.listing_price_match,
        "contract_date": cs.c.contract_date_match,
        "buyer_name": cs.c.buyer_name_match,
        "seller_name": cs.c.seller_name_match,
        "buying_agent_name": cs.c.buying_agent_match,
        "title_company": cs.c.title_company_match,
    }

    if parsed_mismatch_params:
        mismatch_conditions = [
            mismatch_columns[parameter] == "mismatch"
            for parameter in parsed_mismatch_params
            if parameter in mismatch_columns
        ]

        if mismatch_conditions:
            conditions.append(or_(*mismatch_conditions))

    no_skyslope_conditions = []

    if saleincome_no_skyslopefileid is True:
        no_skyslope_conditions.append(
            and_(
                cs.c.saleguid.is_(None),
                func.lower(cs.c.source_table) == "sale income",
            )
        )

    if otherincome_no_skyslopefileid is True:
        no_skyslope_conditions.append(
            and_(
                cs.c.saleguid.is_(None),
                func.lower(cs.c.source_table) == "other income",
            )
        )

    if no_skyslope_conditions:
        conditions.append(or_(*no_skyslope_conditions))

    return conditions


# ============================================================
# COMMON HELPERS
# ============================================================

def get_mismatched_parameters_from_row(row):
    parameter_to_column = {
        "gross_commission": "gross_commission_match",
        "close_date": "close_date_match",
        "status": "status_match",
        "sale_price": "sale_price_match",
        "listing_price": "listing_price_match",
        "contract_date": "contract_date_match",
        "buyer_name": "buyer_name_match",
        "seller_name": "seller_name_match",
        "buying_agent_name": "buying_agent_match",
        "title_company": "title_company_match",
    }

    return [
        parameter
        for parameter, column_name in parameter_to_column.items()
        if row.get(column_name) == "mismatch"
    ]


def build_source_table_value(row) -> List[str]:
    if row.get("saleguid") is None:
        source_table = row.get("source_table")

        if not source_table:
            return []

        return [
            SOURCE_TABLE_DISPLAY_NAMES.get(
                source_table.lower(),
                source_table,
            )
        ]

    result = []

    # Same source-table information as before, without ARRAY_AGG.
    if row.get("has_sale_income"):
        result.append("sale income")

    if row.get("has_other_income"):
        result.append("other income")

    return result


def get_summary_counts(db: Session):
    rd = ReconciliationData

    stmt = select(
        (
            func.count(func.distinct(rd.saleguid)).filter(
                rd.saleguid.is_not(None)
            )
            +
            func.count().filter(
                rd.saleguid.is_(None)
            )
        ).label("total_record"),
        func.count().filter(
            and_(
                func.lower(rd.be_source_table) == "sale income",
                rd.saleguid.is_(None),
            )
        ).label("saleincome_no_skyslopefileid"),
        func.count().filter(
            and_(
                func.lower(rd.be_source_table) == "other income",
                rd.saleguid.is_(None),
            )
        ).label("otherincome_no_skyslopefileid"),
    )

    return db.execute(stmt).mappings().one()


def get_status_filters(db: Session):
    stmt = (
        select(
            ReconciliationData.be_status.label("status")
        )
        .where(
            ReconciliationData.be_status.is_not(None),
            func.trim(ReconciliationData.be_status) != "",
        )
        .distinct()
        .order_by(ReconciliationData.be_status)
    )

    rows = db.execute(stmt).mappings().all()
    return [row["status"] for row in rows]


def get_specialist_filters(db: Session):
    specialist = func.coalesce(
        func.nullif(
            func.trim(
                ReconciliationData.be_transaction_specialist
            ),
            "",
        ),
        "unassigned",
    ).label("specialist")

    stmt = (
        select(specialist)
        .distinct()
        .order_by(specialist)
    )

    rows = db.execute(stmt).mappings().all()
    return [row["specialist"] for row in rows]


def get_reviewer_filters(db: Session):
    reviewer = func.coalesce(
        func.nullif(
            func.trim(
                ReconciliationData.skyslope_reviewer
            ),
            "",
        ),
        "unassigned",
    ).label("reviewer")

    stmt = (
        select(reviewer)
        .distinct()
        .order_by(reviewer)
    )

    rows = db.execute(stmt).mappings().all()
    return [row["reviewer"] for row in rows]


# ============================================================
# FILTER API
# ============================================================

@router.get("/reconciliation/filter", response_model=APIResponse[dict[str, Any]])
def get_reconciliation_filters(db: Session = Depends(get_db)):
    summary = get_summary_counts(db)
    return APIResponse(data={
        "summary": {
            "total_record": summary["total_record"],
            "saleincome_no_skyslopefileid": summary["saleincome_no_skyslopefileid"],
            "otherincome_no_skyslopefileid": summary["otherincome_no_skyslopefileid"],
        },
        "filters": {
            "parameter": list(PARAMETER_DISPLAY_NAMES.values()),
            "source_table": ["sale income", "other income"],
            "review_status": ["in_review", "review_done", "not_a_mismatch", "not_reviewed"],
            "status": get_status_filters(db),
            "specialist": get_specialist_filters(db),
            "reviewer": get_reviewer_filters(db),
        },
    })


# ============================================================
# LISTING API
# ============================================================

@router.get("/reconciliation/transactions", response_model=PaginationResponseWithCount[dict[str, Any]])
def get_reconciliation_transactions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    mismatch_parameter: Optional[List[str]] = Query(default=None),
    source_table: Optional[List[str]] = Query(default=None),
    from_close_date: Optional[str] = Query(None),
    to_close_date: Optional[str] = Query(None),
    status: Optional[List[str]] = Query(None),
    skyslope_stage: Optional[List[str]] = Query(None),
    review_status: Optional[List[str]] = Query(None),
    specialist: Optional[List[str]] = Query(None),
    reviewer: Optional[List[str]] = Query(None),
    saleincome_no_skyslopefileid: Optional[bool] = Query(default=None),
    otherincome_no_skyslopefileid: Optional[bool] = Query(default=None),
    db: Session = Depends(get_db),
):
    parsed_mismatch_params = parse_mismatch_params(mismatch_parameter)
    parsed_source_tables = parse_source_table_params(source_table)

    cs = build_base_reconciliation_subquery()

    conditions = build_filter_conditions(
        cs=cs,
        search=search,
        parsed_mismatch_params=parsed_mismatch_params,
        parsed_source_tables=parsed_source_tables,
        from_close_date=from_close_date,
        to_close_date=to_close_date,
        status=status,
        skyslope_stage=skyslope_stage,
        review_status=review_status,
        specialist=specialist,
        reviewer=reviewer,
        saleincome_no_skyslopefileid=saleincome_no_skyslopefileid,
        otherincome_no_skyslopefileid=otherincome_no_skyslopefileid,
    )

    # Keep COUNT(*) OVER() so the API still returns count in the same
    # single DB query and does not execute the expensive reconciliation
    # CTE twice.
    stmt = (
        select(
            cs.c.transactionid,
            cs.c.saleguid,
            cs.c.skyslope_url,
            cs.c.propertyaddress,
            cs.c.source_table,
            cs.c.has_sale_income,
            cs.c.has_other_income,
            cs.c.skyslope_stage,
            cs.c.gross_commission_match,
            cs.c.close_date_match,
            cs.c.status_match,
            cs.c.sale_price_match,
            cs.c.listing_price_match,
            cs.c.contract_date_match,
            cs.c.buyer_name_match,
            cs.c.seller_name_match,
            cs.c.buying_agent_match,
            cs.c.title_company_match,
            cs.c.review_status,
            cs.c.review_notes,
            cs.c.review_updated_by,
            func.count().over().label("_total_count"),
        )
        .select_from(cs)
    )

    if conditions:
        stmt = stmt.where(*conditions)

    # Final ordering intentionally removed as requested.
    offset = (page - 1) * limit

    stmt = (
        stmt
        .limit(limit)
        .offset(offset)
    )

    rows = db.execute(stmt).mappings().all()

    total_count = (
        rows[0]["_total_count"]
        if rows
        else 0
    )

    results = []

    for row in rows:
        results.append(
            {
                "transactionid": (
                    str(row["transactionid"])
                    if row.get("transactionid")
                    else None
                ),
                "saleguid": (
                    str(row["saleguid"])
                    if row.get("saleguid")
                    else None
                ),
                "skyslope_url": row.get(
                    "skyslope_url"
                ),
                "propertyaddress": row.get(
                    "propertyaddress"
                ),
                "source_table": build_source_table_value(
                    row
                ),
                "skyslope_stage": row.get(
                    "skyslope_stage"
                ),
                "mismatched_parameters": (
                    get_mismatched_parameters_from_row(
                        row
                    )
                ),
                "review": {
                    "review_status": row.get(
                        "review_status"
                    ),
                    "notes": row.get(
                        "review_notes"
                    ),
                    "updated_by": row.get(
                        "review_updated_by"
                    ),
                },
            }
        )

    total_pages = max(1, (total_count + limit - 1) // limit)
    return PaginationResponseWithCount(
        data=PaginationData(total_count=total_count, items=results),
        page=page,
        page_size=limit,
        count=total_count,
        total_pages=total_pages,
        has_next=page < total_pages,
    )


# ============================================================
# DETAIL API
# ============================================================

@router.get(
    "/reconciliation/transaction/{transaction_id}"
)
def get_reconciliation_transaction_details(
    transaction_id: str,
    db: Session = Depends(get_db),
):
    try:
        transaction_uuid = PythonUUID(transaction_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail="Transaction not found.")

    rd = ReconciliationData

    # Direct UUID equality allows PostgreSQL to use the PK index.
    stmt = (
        select(
            rd.transactionid.label(
                "transactionid"
            ),
            rd.saleguid.label(
                "saleguid"
            ),
            rd.property_address.label(
                "propertyaddress"
            ),
            rd.be_source_table.label(
                "source_table"
            ),
            rd.be_status.label(
                "be_status"
            ),
            rd.skyslope_status_value.label(
                "skyslope_status"
            ),
            rd.be_gross_commission.label(
                "be_gross_commission"
            ),
            rd.skyslope_gross_commission.label(
                "skyslope_gross_commission"
            ),
            rd.gross_commission_match.label(
                "gross_commission_match"
            ),
            rd.be_close_date_value.label(
                "be_close_date_value"
            ),
            rd.skyslope_close_date_value.label(
                "skyslope_close_date_value"
            ),
            rd.close_date_match.label(
                "close_date_match"
            ),
            rd.be_status_value.label(
                "be_status_value"
            ),
            rd.skyslope_status_value.label(
                "skyslope_status_value"
            ),
            rd.status_match.label(
                "status_match"
            ),
            rd.be_sale_price.label(
                "be_sale_price"
            ),
            rd.skyslope_sale_price.label(
                "skyslope_sale_price"
            ),
            rd.sale_price_match.label(
                "sale_price_match"
            ),
            rd.be_listing_price.label(
                "be_listing_price"
            ),
            rd.skyslope_listing_price.label(
                "skyslope_listing_price"
            ),
            rd.listing_price_match.label(
                "listing_price_match"
            ),
            rd.be_contract_date.label(
                "be_contract_date"
            ),
            rd.skyslope_contract_date.label(
                "skyslope_contract_date"
            ),
            rd.contract_date_match.label(
                "contract_date_match"
            ),
            rd.be_buyer_name.label(
                "be_buyer_name"
            ),
            rd.skyslope_buyer_name.label(
                "skyslope_buyer_name"
            ),
            rd.buyer_name_match.label(
                "buyer_name_match"
            ),
            rd.be_seller_name.label(
                "be_seller_name"
            ),
            rd.skyslope_seller_name.label(
                "skyslope_seller_name"
            ),
            rd.seller_name_match.label(
                "seller_name_match"
            ),
            rd.be_buying_agent_name.label(
                "be_buying_agent_name"
            ),
            rd.skyslope_buying_agent_name.label(
                "skyslope_buying_agent_name"
            ),
            rd.buying_agent_match.label(
                "buying_agent_match"
            ),
            rd.be_title_company.label(
                "be_title_company"
            ),
            rd.skyslope_title_company.label(
                "skyslope_title_company"
            ),
            rd.title_company_match.label(
                "title_company_match"
            ),
        )
        .where(
            rd.transactionid == transaction_uuid
        )
    )

    row_res = db.execute(stmt).mappings().first()

    row = dict(row_res) if row_res else None

    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    def serialize_date(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    def serialize_numeric(value):
        return float(value) if value is not None else None

    detailed_parameters = {
        "gross_commission": {
            "be_value": serialize_numeric(
                row.get("be_gross_commission")
            ),
            "skyslope_value": serialize_numeric(
                row.get("skyslope_gross_commission")
            ),
            "match_result": row.get(
                "gross_commission_match"
            ),
        },
        "close_date": {
            "be_value": serialize_date(
                row.get("be_close_date_value")
            ),
            "skyslope_value": serialize_date(
                row.get("skyslope_close_date_value")
            ),
            "match_result": row.get(
                "close_date_match"
            ),
        },
        "status": {
            "be_value": row.get(
                "be_status_value"
            ),
            "skyslope_value": row.get(
                "skyslope_status_value"
            ),
            "match_result": row.get(
                "status_match"
            ),
        },
        "sale_price": {
            "be_value": serialize_numeric(
                row.get("be_sale_price")
            ),
            "skyslope_value": serialize_numeric(
                row.get("skyslope_sale_price")
            ),
            "match_result": row.get(
                "sale_price_match"
            ),
        },
        "listing_price": {
            "be_value": serialize_numeric(
                row.get("be_listing_price")
            ),
            "skyslope_value": serialize_numeric(
                row.get("skyslope_listing_price")
            ),
            "match_result": row.get(
                "listing_price_match"
            ),
        },
        "contract_date": {
            "be_value": serialize_date(
                row.get("be_contract_date")
            ),
            "skyslope_value": serialize_date(
                row.get("skyslope_contract_date")
            ),
            "match_result": row.get(
                "contract_date_match"
            ),
        },
        "buyer_name": {
            "be_value": row.get(
                "be_buyer_name"
            ),
            "skyslope_value": row.get(
                "skyslope_buyer_name"
            ),
            "match_result": row.get(
                "buyer_name_match"
            ),
        },
        "seller_name": {
            "be_value": row.get(
                "be_seller_name"
            ),
            "skyslope_value": row.get(
                "skyslope_seller_name"
            ),
            "match_result": row.get(
                "seller_name_match"
            ),
        },
        "buying_agent_name": {
            "be_value": row.get(
                "be_buying_agent_name"
            ),
            "skyslope_value": row.get(
                "skyslope_buying_agent_name"
            ),
            "match_result": row.get(
                "buying_agent_match"
            ),
        },
        "title_company": {
            "be_value": row.get(
                "be_title_company"
            ),
            "skyslope_value": row.get(
                "skyslope_title_company"
            ),
            "match_result": row.get(
                "title_company_match"
            ),
        },
    }

    return {
        "transactionid": (
            str(row["transactionid"])
            if row.get("transactionid")
            else None
        ),
        "saleguid": (
            str(row["saleguid"])
            if row.get("saleguid")
            else None
        ),
        "propertyaddress": row.get(
            "propertyaddress"
        ),
        "source_table": row.get(
            "source_table"
        ),
        "be_status": row.get(
            "be_status"
        ),
        "skyslope_status": row.get(
            "skyslope_status"
        ),
        "parameters": detailed_parameters,
    }


# ============================================================
# EXCEL DOWNLOAD API
# ============================================================

@router.get("/recon-data/download")
def download_recon_data(
    search: Optional[str] = Query(default=None),
    mismatch_parameter: Optional[List[str]] = Query(default=None),
    source_table: Optional[List[str]] = Query(default=None),
    from_close_date: Optional[str] = Query(None),
    to_close_date: Optional[str] = Query(None),
    status: Optional[List[str]] = Query(None),
    skyslope_stage: Optional[List[str]] = Query(None),
    review_status: Optional[List[str]] = Query(None),
    specialist: Optional[List[str]] = Query(None),
    reviewer: Optional[List[str]] = Query(None),
    saleincome_no_skyslopefileid: Optional[bool] = Query(default=None),
    otherincome_no_skyslopefileid: Optional[bool] = Query(default=None),
    db: Session = Depends(get_db),
):
    parsed_mismatch_params = parse_mismatch_params(mismatch_parameter)
    parsed_source_tables = parse_source_table_params(source_table)

    cs = build_base_reconciliation_subquery()

    conditions = build_filter_conditions(
        cs=cs,
        search=search,
        parsed_mismatch_params=parsed_mismatch_params,
        parsed_source_tables=parsed_source_tables,
        from_close_date=from_close_date,
        to_close_date=to_close_date,
        status=status,
        skyslope_stage=skyslope_stage,
        review_status=review_status,
        specialist=specialist,
        reviewer=reviewer,
        saleincome_no_skyslopefileid=saleincome_no_skyslopefileid,
        otherincome_no_skyslopefileid=otherincome_no_skyslopefileid,
    )

    default_columns = [
        (
            "transactionid",
            cs.c.transactionid,
            "Transaction ID",
        ),
        (
            "source_table",
            cs.c.source_table,
            "Source Table",
        ),
        (
            "saleguid",
            cs.c.saleguid,
            "Sale GUID",
        ),
        (
            "propertyaddress",
            cs.c.propertyaddress,
            "Property Address",
        ),
        (
            "be_transaction_specialist",
            cs.c.be_transaction_specialist,
            "Transaction Specialist",
        ),
        (
            "skyslope_reviewer",
            cs.c.skyslope_reviewer,
            "Skyslope Reviewer",
        ),
        (
            "skyslope_stage",
            cs.c.skyslope_stage,
            "Skyslope Stage",
        ),
    ]

    selected_parameters = [
        parameter
        for parameter in parsed_mismatch_params
        if parameter in EXPORT_PARAMETER_CONFIG
    ]

    if not selected_parameters:
        selected_parameters = list(
            EXPORT_PARAMETER_CONFIG.keys()
        )

    select_columns = [
        column_expr.label(alias)
        for alias, column_expr, _ in default_columns
    ]

    export_headers = {
        alias: header
        for alias, _, header in default_columns
    }

    for parameter in selected_parameters:
        config = EXPORT_PARAMETER_CONFIG[
            parameter
        ]

        be_alias = (
            f"{parameter}_be_value"
        )
        ss_alias = (
            f"{parameter}_ss_value"
        )
        match_alias = (
            f"{parameter}_match_result"
        )

        select_columns.extend(
            [
                getattr(
                    ReconciliationData,
                    config["be_attr"],
                ).label(be_alias),
                getattr(
                    ReconciliationData,
                    config["ss_attr"],
                ).label(ss_alias),
                getattr(
                    cs.c,
                    config["match_attr"],
                ).label(match_alias),
            ]
        )

        export_headers[
            be_alias
        ] = config["be_header"]

        export_headers[
            ss_alias
        ] = config["ss_header"]

        export_headers[
            match_alias
        ] = config["match_header"]

    stmt = (
        select(*select_columns)
        .select_from(
            cs.outerjoin(
                ReconciliationData,
                ReconciliationData.transactionid
                == cs.c.transactionid,
            )
        )
    )

    if conditions:
        stmt = stmt.where(*conditions)

    # Final ordering intentionally removed as requested.
    rows = db.execute(stmt).mappings().all()

    data = [
        dict(row)
        for row in rows
    ]

    rows_to_export = []

    for record in data:
        row_dict = {}

        for alias, header in export_headers.items():
            val = record.get(alias)

            if isinstance(val, Decimal):
                val = float(val)

            elif isinstance(
                val,
                (
                    datetime.date,
                    datetime.datetime,
                ),
            ):
                val = val.strftime(
                    "%Y-%m-%d"
                )

            elif isinstance(val, bool):
                val = (
                    "Yes"
                    if val
                    else "No"
                )

            elif val is None:
                val = ""

            row_dict[
                header
            ] = val

        rows_to_export.append(
            row_dict
        )

    df = pd.DataFrame(
        rows_to_export
    )

    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Recon Data", index=False)

        worksheet = writer.sheets[
            "Recon Data"
        ]

        for cell in worksheet[1]:
            cell.font = Font(bold=True)

        for col in worksheet.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)

            for cell in col:
                val = cell.value

                if val is not None:
                    max_len = max(max_len, len(str(val)))

            worksheet.column_dimensions[col_letter].width = max(max_len + 2, 12)

    output.seek(0)

    filename = "reconciliation_data_report.xlsx"

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    return Response(
        content=output.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers=headers,
    )