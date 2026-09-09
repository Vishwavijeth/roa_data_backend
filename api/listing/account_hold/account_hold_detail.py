from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from db import get_db
from models.brokerage_engine_users import BrokerageEngineUser
from models.quickbooks import QuickbooksInvoice
from models.commission_advances.commission_advances import CommissionAdvanceLegalHold
from api.listing.account_hold.utils import build_agent_transactions_subquery
from api.listing.commission_advances.utils import CommissionAdvanceLegalHoldStatus

router = APIRouter()


def fetch_agent_by_customer_id(db: Session, customer_id: str) -> dict | None:
    statement = (
        select(
            BrokerageEngineUser.agent_identifier,
            BrokerageEngineUser.display_name,
            BrokerageEngineUser.roa_email,
            BrokerageEngineUser.agenttags,
            BrokerageEngineUser.qb_customerid,
        )
        .where(cast(BrokerageEngineUser.qb_customerid, String) == str(customer_id))
        .limit(1)
    )

    row = db.execute(statement).mappings().first()
    return dict(row) if row else None


def fetch_agent_legal_hold_balance(db: Session, agent_identifier) -> float:
    if agent_identifier is None:
        return 0.0

    legal_hold_balance = db.scalar(
        select(CommissionAdvanceLegalHold.outstanding_amount)
        .where(
            CommissionAdvanceLegalHold.agent_id == agent_identifier,
            CommissionAdvanceLegalHold.status == CommissionAdvanceLegalHoldStatus.ACTIVE.value,
            CommissionAdvanceLegalHold.outstanding_amount > 0,
        )
        .order_by(CommissionAdvanceLegalHold.id.desc())
        .limit(1)
    )

    return float(legal_hold_balance or 0)


def fetch_agent_detail_transactions(db: Session, agent_identifier) -> tuple[list[dict], int, int, float]:
    if agent_identifier is None:
        return [], 0, 0, 0.0

    agent_transactions = build_agent_transactions_subquery([agent_identifier])

    statement = (
        select(agent_transactions)
        .order_by(agent_transactions.c.transaction_id)
    )

    rows = db.execute(statement).mappings().all()

    transactions = []
    transaction_ids = set()
    closed_transaction_ids = set()
    total_commission_earned = 0.0

    for row in rows:
        transaction_id = row.get("transaction_id")

        if transaction_id is not None:
            transaction_ids.add(transaction_id)

        is_closed = bool(row.get("is_closed"))
        agent_net = float(row.get("agent_net") or 0)

        if is_closed:
            if transaction_id is not None:
                closed_transaction_ids.add(transaction_id)

            total_commission_earned += agent_net

        transaction_flags: list[str] = []

        if bool(row.get("has_transaction_mismatch")):
            transaction_flags.append("transaction_mismatch")

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

    return transactions, transaction_count, closed_volume, total_commission_earned


def fetch_agent_ar_details(db: Session, qb_customerid: int | str | None) -> dict:
    if qb_customerid is None:
        return {
            "total_open_balance": 0.0,
            "updated_at": None,
            "invoices": [],
        }

    customer_id = str(qb_customerid)

    statement = (
        select(
            QuickbooksInvoice.invoice_id,
            QuickbooksInvoice.doc_number,
            QuickbooksInvoice.txn_date,
            QuickbooksInvoice.due_date,
            QuickbooksInvoice.total_amt,
            QuickbooksInvoice.balance,
            QuickbooksInvoice.updated_at,
        )
        .where(
            QuickbooksInvoice.customer_id == customer_id,
            QuickbooksInvoice.balance > 0,
        )
        .order_by(
            QuickbooksInvoice.due_date.asc().nullslast(),
            QuickbooksInvoice.txn_date.asc().nullslast(),
        )
    )

    rows = db.execute(statement).mappings().all()

    total_open_balance = sum(
        float(row.get("balance") or 0)
        for row in rows
    )

    updated_at = max(
        (
            row["updated_at"]
            for row in rows
            if row.get("updated_at") is not None
        ),
        default=None,
    )

    invoices = [
        {
            "invoice_id": str(row["invoice_id"]) if row.get("invoice_id") is not None else None,
            "doc_number": row.get("doc_number"),
            "txn_date": row.get("txn_date"),
            "due_date": row.get("due_date"),
            "total_amt": float(row["total_amt"]) if row.get("total_amt") is not None else 0.0,
            "balance": float(row["balance"]) if row.get("balance") is not None else 0.0,
        }
        for row in rows
    ]

    return {
        "total_open_balance": total_open_balance,
        "updated_at": updated_at,
        "invoices": invoices,
    }


@router.get("/account-hold/detail/{customer_id}")
def get_account_hold_detail(customer_id: str, db: Session = Depends(get_db)):
    agent = fetch_agent_by_customer_id(
        db=db,
        customer_id=customer_id,
    )

    if not agent:
        raise HTTPException(
            status_code=404,
            detail="Agent not found",
        )

    transactions, transaction_count, closed_volume, total_commission_earned = fetch_agent_detail_transactions(
        db=db,
        agent_identifier=agent.get("agent_identifier"),
    )

    ar_details = fetch_agent_ar_details(
        db=db,
        qb_customerid=agent.get("qb_customerid"),
    )

    legal_hold_balance = fetch_agent_legal_hold_balance(
        db=db,
        agent_identifier=agent.get("agent_identifier"),
    )

    has_account_hold = "AccountHold" in (agent.get("agenttags") or "")
    has_ar_balance = ar_details["total_open_balance"] > 0

    has_transaction_mismatch = any(
        "transaction_mismatch" in transaction.get("transaction_flags", [])
        for transaction in transactions
    )

    broker_flags: list[str] = []

    if has_account_hold:
        broker_flags.append("account_hold")

    if has_ar_balance:
        broker_flags.append("ar_balance")

    transaction_flags: list[str] = []

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
            "ar_balance": ar_details,
            "transactions": transactions,
        },
    }