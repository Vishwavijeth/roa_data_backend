from __future__ import annotations

from math import ceil
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, and_, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from db import get_db
from common.pagination import PaginationResponse
from common.response import Response

from api.listing.account_hold.base import (
    AccountHoldItem,
    AccountHoldSummaryData,
)

from models.brokerage_engine_users import BrokerageEngineUser
from models.quickbooks import QuickbooksInvoice

from api.listing.account_hold.utils import (
    build_matched_transactions_subquery,
    build_latest_reconciliation_subquery,
    build_mismatch_expression,
)


router = APIRouter()


def account_hold_expression():
    return func.coalesce(BrokerageEngineUser.agenttags, "").contains("AccountHold")


def build_invoice_summary_subquery():
    return (
        select(
            QuickbooksInvoice.customer_id.label("customer_id"),
            func.coalesce(func.sum(QuickbooksInvoice.balance), 0).label("total_open_balance"),
            func.count(QuickbooksInvoice.invoice_id).label("invoice_count"),
            func.max(QuickbooksInvoice.updated_at).label("ar_updated_at"),
        )
        .group_by(QuickbooksInvoice.customer_id)
        .subquery("invoice_summary")
    )


def build_agent_base_subquery():
    invoice_summary = build_invoice_summary_subquery()

    customer_join_condition = (
        cast(BrokerageEngineUser.qb_customerid, String) == invoice_summary.c.customer_id
    )

    total_open_balance = func.coalesce(invoice_summary.c.total_open_balance, 0)
    invoice_count = func.coalesce(invoice_summary.c.invoice_count, 0)
    has_account_hold = account_hold_expression()
    has_ar_balance = total_open_balance > 0

    return (
        select(
            BrokerageEngineUser.agent_identifier.label("agent_identifier"),
            BrokerageEngineUser.display_name.label("display_name"),
            BrokerageEngineUser.roa_email.label("roa_email"),
            BrokerageEngineUser.agenttags.label("agenttags"),
            BrokerageEngineUser.qb_customerid.label("qb_customerid"),
            invoice_summary.c.customer_id.label("matched_customer_id"),
            total_open_balance.label("total_open_balance"),
            invoice_count.label("invoice_count"),
            invoice_summary.c.ar_updated_at.label("ar_updated_at"),
            has_account_hold.label("has_account_hold"),
            has_ar_balance.label("has_ar_balance"),
        )
        .select_from(BrokerageEngineUser)
        .outerjoin(invoice_summary, customer_join_condition)
        .subquery("agent_base")
    )


def apply_listing_filters(
    statement,
    base,
    search: str | None = None,
    account_hold: bool | None = None,
    ar_balance: bool | None = None,
    match_mode: Literal["and", "or"] = "and",
):
    filters = []

    if search and search.strip():
        search_value = f"%{search.strip()}%"
        filters.append(
            or_(
                base.c.display_name.ilike(search_value),
                base.c.roa_email.ilike(search_value),
            )
        )

    boolean_filters = []

    if account_hold is True:
        boolean_filters.append(base.c.has_account_hold.is_(True))
    elif account_hold is False:
        boolean_filters.append(base.c.has_account_hold.is_(False))

    if ar_balance is True:
        boolean_filters.append(base.c.has_ar_balance.is_(True))
    elif ar_balance is False:
        boolean_filters.append(base.c.has_ar_balance.is_(False))

    if boolean_filters:
        filters.append(or_(*boolean_filters) if match_mode == "or" else and_(*boolean_filters))

    if filters:
        statement = statement.where(and_(*filters))

    return statement


def fetch_agent_transaction_summary(db: Session, agent_rows: list[dict]) -> dict[str, dict]:
    target_emails = [row["roa_email"] for row in agent_rows if row.get("roa_email")]
    target_names = [row["display_name"] for row in agent_rows if row.get("display_name")]

    if not target_emails and not target_names:
        return {}

    matched_transactions = build_matched_transactions_subquery(target_emails, target_names)
    latest_reconciliation = build_latest_reconciliation_subquery()
    mismatch_expr = build_mismatch_expression(latest_reconciliation)

    statement = (
        select(
            matched_transactions.c.agent_key,
            func.count(func.distinct(matched_transactions.c.transaction_id)).label("transaction_count"),
            func.bool_or(mismatch_expr).label("has_transaction_mismatch"),
        )
        .select_from(matched_transactions)
        .outerjoin(
            latest_reconciliation,
            latest_reconciliation.c.transactionid == matched_transactions.c.transaction_id,
        )
        .group_by(matched_transactions.c.agent_key)
    )

    rows = db.execute(statement).mappings().all()

    return {
        row["agent_key"]: {
            "transaction_count": int(row["transaction_count"] or 0),
            "has_transaction_mismatch": bool(row["has_transaction_mismatch"]),
        }
        for row in rows
    }


def fetch_agent_by_email(db: Session, email: str) -> dict | None:
    statement = (
        select(
            BrokerageEngineUser.display_name,
            BrokerageEngineUser.roa_email,
            BrokerageEngineUser.agenttags,
            BrokerageEngineUser.qb_customerid,
        )
        .where(func.trim(BrokerageEngineUser.roa_email) == func.trim(email))
        .limit(1)
    )

    row = db.execute(statement).mappings().first()
    return dict(row) if row else None


def fetch_agent_ar_balance(db: Session, qb_customerid: int | str | None) -> dict | None:
    if qb_customerid is None:
        return None

    statement = (
        select(
            QuickbooksInvoice.customer_id.label("customer_id"),
            func.coalesce(func.sum(QuickbooksInvoice.balance), 0).label("total_open_balance"),
            func.count(QuickbooksInvoice.invoice_id).label("invoice_count"),
            func.max(QuickbooksInvoice.updated_at).label("updated_at"),
        )
        .where(QuickbooksInvoice.customer_id == str(qb_customerid))
        .group_by(QuickbooksInvoice.customer_id)
    )

    row = db.execute(statement).mappings().first()

    if not row:
        return None

    return {
        "customer_id": str(row["customer_id"]),
        "total_open_balance": row["total_open_balance"],
        "invoice_count": int(row["invoice_count"] or 0),
        "updated_at": row["updated_at"],
    }


@router.get("/account-hold/summary", response_model=Response[AccountHoldSummaryData])
def get_account_hold_summary(db: Session = Depends(get_db)):
    base = build_agent_base_subquery()

    statement = select(
        func.count().label("total_agents"),
        func.count().filter(base.c.has_ar_balance.is_(True)).label("agents_with_ar_balance"),
        func.count().filter(base.c.has_account_hold.is_(True)).label("agents_with_account_hold"),
    )

    row = db.execute(statement).mappings().one()

    return Response(
        data=AccountHoldSummaryData(
            total_agents=int(row["total_agents"] or 0),
            agents_with_ar_balance=int(row["agents_with_ar_balance"] or 0),
            agents_with_account_hold=int(row["agents_with_account_hold"] or 0),
        )
    )


@router.get("/account-hold", response_model=PaginationResponse[AccountHoldItem])
def get_account_hold_listing(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    account_hold: bool | None = Query(None),
    ar_balance: bool | None = Query(None),
    match_mode: Literal["and", "or"] = Query("and"),
    search: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
):
    base = build_agent_base_subquery()
    statement = select(base)

    statement = apply_listing_filters(
        statement=statement,
        base=base,
        search=search,
        account_hold=account_hold,
        ar_balance=ar_balance,
        match_mode=match_mode,
    )

    count_statement = select(func.count()).select_from(statement.subquery())
    total_count = int(db.scalar(count_statement) or 0)

    offset = (page - 1) * size

    data_statement = (
        statement.order_by(
            desc(base.c.has_account_hold),
            desc(base.c.total_open_balance),
            base.c.display_name.asc(),
            base.c.roa_email.asc(),
        )
        .offset(offset)
        .limit(size)
    )

    agent_rows = [dict(row) for row in db.execute(data_statement).mappings().all()]

    transaction_summary_map = fetch_agent_transaction_summary(db=db, agent_rows=agent_rows)

    data: list[AccountHoldItem] = []

    for row in agent_rows:
        transaction_summary = (
            transaction_summary_map.get(row.get("roa_email"))
            or transaction_summary_map.get(row.get("display_name"))
            or {}
        )

        has_account_hold = bool(row.get("has_account_hold"))
        total_open_balance = float(row.get("total_open_balance") or 0)
        has_ar_balance = total_open_balance > 0

        broker_flags: list[str] = []
        if has_account_hold:
            broker_flags.append("account_hold")
        if has_ar_balance:
            broker_flags.append("ar_balance")

        transaction_flags: list[str] = []
        if transaction_summary.get("has_transaction_mismatch", False):
            transaction_flags.append("transaction_mismatch")

        data.append(
            AccountHoldItem(
                display_name=row.get("display_name"),
                roa_email=row.get("roa_email"),
                customer_id=str(row["qb_customerid"]) if row.get("qb_customerid") is not None else None,
                transaction_count=int(transaction_summary.get("transaction_count", 0)),
                broker_flags=broker_flags,
                transaction_flags=transaction_flags,
            )
        )

    total_pages = ceil(total_count / size) if total_count else 1

    return PaginationResponse(
        data=data,
        page=page,
        page_size=size,
        count=len(data),
        total_count=total_count,
        total_pages=total_pages,
        has_next=page < total_pages,
    )