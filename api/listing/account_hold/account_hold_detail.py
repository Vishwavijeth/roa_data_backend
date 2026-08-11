from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from common.response import Response

from api.listing.account_hold.base import (
    OpenInvoiceItem,
    AccountHoldDetailData,
    ARBalanceItem,
    TransactionItem,
)

from models.brokerage_engine_users import BrokerageEngineUser
from models.quickbooks import QuickbooksInvoice
from models.skyslope.sale import Sale

from api.listing.account_hold.utils import (
    build_matched_transactions_subquery,
    build_latest_reconciliation_subquery,
)


router = APIRouter()


def has_account_hold_tag(agenttags):
    if not agenttags:
        return False

    return "AccountHold" in str(agenttags)


def fetch_agent_by_customer_id(
    db: Session,
    customer_id: int,
) -> dict | None:
    statement = (
        select(
            BrokerageEngineUser.display_name,
            BrokerageEngineUser.roa_email,
            BrokerageEngineUser.qb_customerid,
            BrokerageEngineUser.agenttags,
        )
        .where(
            BrokerageEngineUser.qb_customerid
            == customer_id
        )
        .limit(1)
    )

    row = db.execute(
        statement
    ).mappings().first()

    return dict(row) if row else None


def fetch_agent_detail_transactions(
    db: Session,
    email: str,
    display_name: str,
) -> list[dict]:
    target_emails = [email] if email else []
    target_names = [display_name] if display_name else []

    if not target_emails and not target_names:
        return []

    matched_transactions = (
        build_matched_transactions_subquery(
            target_emails=target_emails,
            target_names=target_names,
        )
    )

    latest_reconciliation = (
        build_latest_reconciliation_subquery()
    )

    statement = (
        select(
            matched_transactions.c.transaction_id.label(
                "transaction_identifier_transactionid"
            ),
            matched_transactions.c.property_address,
            matched_transactions.c.source_name,
            matched_transactions.c.source_status,
            latest_reconciliation.c.be_source_table,
            latest_reconciliation.c.saleguid,
            Sale.url.label(
                "skyslope_url"
            ),
            latest_reconciliation.c.be_transaction_specialist,
            latest_reconciliation.c.skyslope_reviewer,
            latest_reconciliation.c.be_gross_commission,
            latest_reconciliation.c.skyslope_gross_commission,
            latest_reconciliation.c.gross_commission_match,
            latest_reconciliation.c.be_close_date_value,
            latest_reconciliation.c.skyslope_close_date_value,
            latest_reconciliation.c.close_date_match,
            latest_reconciliation.c.be_status_value,
            latest_reconciliation.c.skyslope_status_value,
            latest_reconciliation.c.status_match,
            latest_reconciliation.c.be_sale_price,
            latest_reconciliation.c.skyslope_sale_price,
            latest_reconciliation.c.sale_price_match,
        )
        .select_from(matched_transactions)
        .outerjoin(
            latest_reconciliation,
            latest_reconciliation.c.transactionid
            == matched_transactions.c.transaction_id,
        )
        .outerjoin(
            Sale,
            Sale.saleguid
            == latest_reconciliation.c.saleguid,
        )
        .order_by(
            matched_transactions.c.property_address.asc(),
            matched_transactions.c.transaction_id.asc(),
        )
    )

    try:
        rows = db.execute(
            statement
        ).mappings().all()

        return [dict(row) for row in rows]

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "Detail transaction query failed: "
                f"{str(e)}"
            ),
        )


def fetch_agent_ar_balance(
    db: Session,
    customer_id: int,
):
    statement = (
        select(
            QuickbooksInvoice.invoice_id,
            QuickbooksInvoice.balance,
            QuickbooksInvoice.total_amt,
            QuickbooksInvoice.due_date,
            QuickbooksInvoice.txn_date,
            QuickbooksInvoice.doc_number,
        )
        .where(
            QuickbooksInvoice.customer_id
            == str(customer_id)
        )
        .order_by(
            QuickbooksInvoice.due_date.asc().nullslast()
        )
    )

    try:
        invoice_rows = db.execute(
            statement
        ).mappings().all()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "AR balance lookup failed: "
                f"{str(e)}"
            ),
        )

    if not invoice_rows:
        return None, False

    total_balance = sum(
        float(row["balance"] or 0)
        for row in invoice_rows
    )

    has_ar_balance = total_balance > 0

    open_invoices = []

    for row in invoice_rows:
        invoice_balance = float(
            row["balance"] or 0
        )

        if invoice_balance <= 0:
            continue

        open_invoices.append(
            OpenInvoiceItem(
                balance=row["balance"],
                due_date=row["due_date"],
                txn_date=row["txn_date"],
                total_amt=row["total_amt"],
                doc_number=row["doc_number"],
                invoice_id=row["invoice_id"],
            )
        )

    ar_balance_row = ARBalanceItem(
        balance=total_balance,
        open_invoices=open_invoices,
    )

    return ar_balance_row, has_ar_balance


def build_transaction_flags(
    row: dict,
) -> list[str]:
    if row.get("saleguid") is None:
        return ["no_skyslope_file_id"]

    transaction_flags = []

    match_mapping = {
        "gross_commission_match": "gross_commission",
        "close_date_match": "close_date",
        "status_match": "status",
        "sale_price_match": "sale_price",
    }

    for db_field, response_flag in (
        match_mapping.items()
    ):
        value = row.get(db_field)

        if (
            value is not None
            and str(value).strip().lower()
            != "match"
        ):
            transaction_flags.append(
                response_flag
            )

    return transaction_flags


def build_mismatch_details(
    row: dict,
    transaction_flags: list[str],
) -> dict:
    if row.get("saleguid") is None:
        return {}

    mismatch_field_map = {
        "gross_commission": {
            "be_key": "be_gross_commission",
            "skyslope_key": (
                "skyslope_gross_commission"
            ),
        },
        "close_date": {
            "be_key": "be_close_date_value",
            "skyslope_key": (
                "skyslope_close_date_value"
            ),
        },
        "status": {
            "be_key": "be_status_value",
            "skyslope_key": (
                "skyslope_status_value"
            ),
        },
        "sale_price": {
            "be_key": "be_sale_price",
            "skyslope_key": (
                "skyslope_sale_price"
            ),
        },
    }

    mismatch_details = {}

    for flag in transaction_flags:
        config = mismatch_field_map.get(flag)

        if not config:
            continue

        mismatch_details[flag] = {
            "be": row.get(
                config["be_key"]
            ),
            "skyslope": row.get(
                config["skyslope_key"]
            ),
        }

    return mismatch_details


@router.get(
    "/account-hold/detail/{customer_id}",
    response_model=Response[AccountHoldDetailData],
)
def get_account_hold_detail(
    customer_id: int,
    db: Session = Depends(get_db),
):
    agent = fetch_agent_by_customer_id(
        db=db,
        customer_id=customer_id,
    )

    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Agent not found",
        )

    email = agent.get(
        "roa_email"
    ) or ""

    display_name = agent.get(
        "display_name"
    ) or ""

    transaction_rows = (
        fetch_agent_detail_transactions(
            db=db,
            email=email,
            display_name=display_name,
        )
    )

    ar_balance_row, has_ar_balance = (
        fetch_agent_ar_balance(
            db=db,
            customer_id=customer_id,
        )
    )

    has_account_hold = has_account_hold_tag(
        agent.get("agenttags")
    )

    broker_flags = []

    if has_account_hold:
        broker_flags.append(
            "account_hold"
        )

    if has_ar_balance:
        broker_flags.append(
            "ar_balance"
        )

    transactions = []
    seen_transactions = set()

    for row in transaction_rows:
        transaction_id = row.get(
            "transaction_identifier_transactionid"
        )

        dedupe_key = (
            transaction_id,
            row.get("source_name"),
        )

        if dedupe_key in seen_transactions:
            continue

        transaction_flags = (
            build_transaction_flags(row)
        )

        mismatch_details = (
            build_mismatch_details(
                row=row,
                transaction_flags=transaction_flags,
            )
        )

        transactions.append(
            TransactionItem(
                transactionid=transaction_id,
                property_address=row.get(
                    "property_address"
                ),
                source_table=(
                    row.get("be_source_table")
                    or row.get("source_name")
                ),
                status=row.get(
                    "source_status"
                ),
                skyslope_url=row.get(
                    "skyslope_url"
                ),
                be_transaction_specialist=(
                    row.get(
                        "be_transaction_specialist"
                    )
                ),
                skyslope_reviewer=row.get(
                    "skyslope_reviewer"
                ),
                transaction_flags=(
                    transaction_flags
                ),
                mismatch_details=(
                    mismatch_details
                ),
            )
        )

        seen_transactions.add(
            dedupe_key
        )

    return Response(
        data=AccountHoldDetailData(
            display_name=agent.get(
                "display_name"
            ),
            roa_email=agent.get(
                "roa_email"
            ),
            customer_id=(
                str(
                    agent["qb_customerid"]
                )
                if agent.get(
                    "qb_customerid"
                ) is not None
                else None
            ),
            transaction_count=len(
                transactions
            ),
            broker_flags=broker_flags,
            ar_balance=ar_balance_row,
            transactions=transactions,
        )
    )