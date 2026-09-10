from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.orm import Session

from db import get_db
from models.brokerage_engine_users import BrokerageEngineUser
from models.quickbooks import QuickbooksInvoice
from models.commission_advances.commission_advances import CommissionAdvanceLegalHold
from api.listing.account_hold.utils import build_agent_transactions_subquery
from api.listing.commission_advances.utils import CommissionAdvanceLegalHoldStatus

router = APIRouter()


def fetch_agent_and_ar_details(db: Session, customer_id: str) -> tuple[dict | None, dict]:
    customer_id = str(customer_id)

    agent_base = (
        select(
            BrokerageEngineUser.agent_identifier.label("agent_identifier"),
            BrokerageEngineUser.display_name.label("display_name"),
            BrokerageEngineUser.roa_email.label("roa_email"),
            BrokerageEngineUser.agenttags.label("agenttags"),
            BrokerageEngineUser.qb_customerid.label("qb_customerid"),
        )
        .where(cast(BrokerageEngineUser.qb_customerid, String) == customer_id)
        .limit(1)
        .subquery("agent_base")
    )

    legal_hold_balance_subquery = (
        select(CommissionAdvanceLegalHold.outstanding_amount)
        .where(
            CommissionAdvanceLegalHold.agent_id == agent_base.c.agent_identifier,
            CommissionAdvanceLegalHold.status == CommissionAdvanceLegalHoldStatus.ACTIVE.value,
            CommissionAdvanceLegalHold.outstanding_amount > 0,
        )
        .order_by(CommissionAdvanceLegalHold.id.desc())
        .limit(1)
        .scalar_subquery()
    )

    statement = (
        select(
            agent_base.c.agent_identifier,
            agent_base.c.display_name,
            agent_base.c.roa_email,
            agent_base.c.agenttags,
            agent_base.c.qb_customerid,
            func.coalesce(legal_hold_balance_subquery, 0).label("legal_hold_balance"),
            QuickbooksInvoice.invoice_id,
            QuickbooksInvoice.doc_number,
            QuickbooksInvoice.txn_date,
            QuickbooksInvoice.due_date,
            QuickbooksInvoice.total_amt,
            QuickbooksInvoice.balance,
            QuickbooksInvoice.updated_at,
        )
        .select_from(agent_base)
        .outerjoin(
            QuickbooksInvoice,
            and_(
                QuickbooksInvoice.customer_id == cast(agent_base.c.qb_customerid, String),
                QuickbooksInvoice.balance > 0,
            ),
        )
        .order_by(
            QuickbooksInvoice.due_date.asc().nullslast(),
            QuickbooksInvoice.txn_date.asc().nullslast(),
        )
    )

    rows = db.execute(statement).mappings().all()

    if not rows:
        return None, {
            "total_open_balance": 0.0,
            "updated_at": None,
            "invoices": [],
        }

    first_row = rows[0]

    agent = {
        "agent_identifier": first_row.get("agent_identifier"),
        "display_name": first_row.get("display_name"),
        "roa_email": first_row.get("roa_email"),
        "agenttags": first_row.get("agenttags"),
        "qb_customerid": first_row.get("qb_customerid"),
        "legal_hold_balance": float(first_row.get("legal_hold_balance") or 0),
    }

    total_open_balance = 0.0
    updated_at = None
    invoices = []

    for row in rows:
        if row.get("invoice_id") is None:
            continue

        balance = float(row.get("balance") or 0)
        total_open_balance += balance

        row_updated_at = row.get("updated_at")

        if row_updated_at is not None and (updated_at is None or row_updated_at > updated_at):
            updated_at = row_updated_at

        invoices.append(
            {
                "invoice_id": str(row["invoice_id"]),
                "doc_number": row.get("doc_number"),
                "txn_date": row.get("txn_date"),
                "due_date": row.get("due_date"),
                "total_amt": float(row["total_amt"]) if row.get("total_amt") is not None else 0.0,
                "balance": balance,
            }
        )

    return agent, {
        "total_open_balance": total_open_balance,
        "updated_at": updated_at,
        "invoices": invoices,
    }


def fetch_agent_detail_transactions(db: Session, agent_identifier) -> tuple[list[dict], int, int, float, float, bool]:
    if agent_identifier is None:
        return [], 0, 0, 0.0, 0.0, False

    agent_transactions = build_agent_transactions_subquery([agent_identifier])

    statement = (
        select(
            agent_transactions.c.transaction_id,
            agent_transactions.c.property_address,
            agent_transactions.c.source_status,
            agent_transactions.c.is_closed,
            agent_transactions.c.agent_net,
            agent_transactions.c.saleguid,
            agent_transactions.c.skyslope_url,
            agent_transactions.c.be_source_table,
            agent_transactions.c.be_transaction_specialist,
            agent_transactions.c.skyslope_reviewer,
            agent_transactions.c.has_transaction_mismatch,
            agent_transactions.c.be_gross_commission,
            agent_transactions.c.skyslope_gross_commission,
            agent_transactions.c.gross_commission_match,
            agent_transactions.c.be_close_date_value,
            agent_transactions.c.skyslope_close_date_value,
            agent_transactions.c.close_date_match,
            agent_transactions.c.be_status_value,
            agent_transactions.c.skyslope_status_value,
            agent_transactions.c.status_match,
            agent_transactions.c.be_sale_price,
            agent_transactions.c.skyslope_sale_price,
            agent_transactions.c.sale_price_match,
        )
        .order_by(agent_transactions.c.transaction_id)
    )

    rows = db.execute(statement).mappings()

    transactions = []
    transaction_ids = set()
    closed_transaction_ids = set()
    sale_volume_transaction_ids = set()

    total_commission_earned = 0.0
    total_sale_volume = 0.0
    has_transaction_mismatch = False

    for row in rows:
        transaction_id = row.get("transaction_id")

        if transaction_id is not None:
            transaction_ids.add(transaction_id)

        is_closed = bool(row.get("is_closed"))
        agent_net = float(row.get("agent_net") or 0)
        be_source_table = str(row.get("be_source_table") or "").strip().lower()

        if is_closed:
            if transaction_id is not None:
                closed_transaction_ids.add(transaction_id)

            total_commission_earned += agent_net

            if (
                be_source_table == "sale income"
                and transaction_id is not None
                and transaction_id not in sale_volume_transaction_ids
            ):
                total_sale_volume += float(row.get("be_sale_price") or 0)
                sale_volume_transaction_ids.add(transaction_id)

        row_has_mismatch = bool(row.get("has_transaction_mismatch"))

        if row_has_mismatch:
            has_transaction_mismatch = True

        transaction_flags = ["transaction_mismatch"] if row_has_mismatch else []

        transactions.append(
            {
                "transaction_id": str(transaction_id) if transaction_id is not None else None,
                "property_address": row.get("property_address"),
                "transaction_status": row.get("source_status"),
                "agent_net": agent_net,
                "saleguid": str(row["saleguid"]) if row.get("saleguid") is not None else None,
                "skyslope_url": row.get("skyslope_url"),
                "be_source_table": row.get("be_source_table"),
                "be_transaction_specialist": row.get("be_transaction_specialist"),
                "skyslope_reviewer": row.get("skyslope_reviewer"),
                "transaction_flags": transaction_flags,
                "mismatch_details": {
                    "gross_commission": {
                        "be_value": float(row["be_gross_commission"]) if row.get("be_gross_commission") is not None else None,
                        "skyslope_value": float(row["skyslope_gross_commission"]) if row.get("skyslope_gross_commission") is not None else None,
                        "match": row.get("gross_commission_match"),
                    },
                    "close_date": {
                        "be_value": row.get("be_close_date_value"),
                        "skyslope_value": row.get("skyslope_close_date_value"),
                        "match": row.get("close_date_match"),
                    },
                    "status": {
                        "be_value": row.get("be_status_value"),
                        "skyslope_value": row.get("skyslope_status_value"),
                        "match": row.get("status_match"),
                    },
                    "sale_price": {
                        "be_value": float(row["be_sale_price"]) if row.get("be_sale_price") is not None else None,
                        "skyslope_value": float(row["skyslope_sale_price"]) if row.get("skyslope_sale_price") is not None else None,
                        "match": row.get("sale_price_match"),
                    },
                },
            }
        )

    transaction_count = len(transaction_ids)
    closed_volume = len(closed_transaction_ids)

    return (
        transactions,
        transaction_count,
        closed_volume,
        total_commission_earned,
        total_sale_volume,
        has_transaction_mismatch,
    )


@router.get("/account-hold/detail/{customer_id}")
def get_account_hold_detail(customer_id: str, db: Session = Depends(get_db)):
    agent, ar_details = fetch_agent_and_ar_details(
        db=db,
        customer_id=customer_id,
    )

    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Agent not found",
        )

    (
        transactions,
        transaction_count,
        closed_volume,
        total_commission_earned,
        total_sale_volume,
        has_transaction_mismatch,
    ) = fetch_agent_detail_transactions(
        db=db,
        agent_identifier=agent.get("agent_identifier"),
    )

    legal_hold_balance = float(agent.get("legal_hold_balance") or 0)

    has_account_hold = "AccountHold" in (agent.get("agenttags") or "")
    has_ar_balance = ar_details["total_open_balance"] > 0

    broker_flags = []

    if has_account_hold:
        broker_flags.append("account_hold")

    if has_ar_balance:
        broker_flags.append("ar_balance")

    transaction_flags = []

    if has_transaction_mismatch:
        transaction_flags.append("transaction_mismatch")

    return {
        "success": True,
        "data": {
            "agent_identifier": str(agent["agent_identifier"]) if agent.get("agent_identifier") is not None else None,
            "display_name": agent.get("display_name"),
            "roa_email": agent.get("roa_email"),
            "qb_customerid": str(agent["qb_customerid"]) if agent.get("qb_customerid") is not None else None,
            "legal_hold_balance": legal_hold_balance,
            "broker_flags": broker_flags,
            "transaction_flags": transaction_flags,
            "transaction_count": transaction_count,
            "closed_volume": closed_volume,
            "total_commission_earned": total_commission_earned,
            "total_sale_volume": total_sale_volume,
            "ar_balance": ar_details,
            "transactions": transactions,
        },
    }