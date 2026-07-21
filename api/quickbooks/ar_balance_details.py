from typing import List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from psycopg2.extras import RealDictCursor, execute_batch, Json
from db import get_db
from services.account_hold_helper import fetch_ar_balance

router = APIRouter()


def get_brokerage_engine_user_rows(conn) -> List[Dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                primary_emailaddress AS email
            FROM brokerage_engine_users
            WHERE primary_emailaddress IS NOT NULL
              AND TRIM(primary_emailaddress) <> ''
            """
        )
        return cur.fetchall()


def clear_ar_balance_details(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ar_balance_details")
    conn.commit()


def build_customer_ar_rows(enriched_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    customer_map: Dict[int, Dict[str, Any]] = {}

    for row in enriched_rows:
        ar_balance = row.get("ar_balance") or {}
        customers = ar_balance.get("customers") or {}

        for email, customer_data in customers.items():
            customer_id = customer_data.get("customer_id")
            if not customer_id:
                continue

            try:
                customer_id = int(customer_id)
            except (TypeError, ValueError):
                continue

            open_invoices = customer_data.get("open_invoices") or []
            total_open_balance = customer_data.get("balance")
            invoice_count = customer_data.get("invoice_count", 0)

            payload = {
                "customer_id": customer_id,
                "email": email,
                "invoice_count": invoice_count,
                "due_date": customer_data.get("due_date"),
                "total_amt": customer_data.get("total_amt"),
                "balance": total_open_balance,
                "open_invoices": open_invoices,
                "error": customer_data.get("error"),
            }

            customer_map[customer_id] = {
                "customer_id": customer_id,
                "total_open_balance": float(total_open_balance or 0),
                "invoice_count": int(invoice_count or 0),
                "raw_invoice": payload,
            }

    return list(customer_map.values())


def insert_ar_balance_details(conn, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0

    sql = """
        INSERT INTO ar_balance_details (
            customer_id,
            total_open_balance,
            invoice_count,
            updated_at,
            raw_invoice
        )
        VALUES (%s, %s, %s, NOW(), %s)
    """

    values = [
        (
            row["customer_id"],
            row["total_open_balance"],
            row["invoice_count"],
            Json(row["raw_invoice"]),
        )
        for row in rows
    ]

    with conn.cursor() as cur:
        execute_batch(cur, sql, values, page_size=200)
    conn.commit()

    return len(rows)


@router.post("/sync-ar-balances")
async def sync_ar_balances(db=Depends(get_db)):
    conn = db

    try:
        user_rows = get_brokerage_engine_user_rows(conn)

        if not user_rows:
            return {
                "success": True,
                "message": "No brokerage engine users found",
                "user_count": 0,
                "customer_row_count": 0,
                "total_open_balance": 0.0,
            }

        enriched_rows = await fetch_ar_balance(user_rows, conn)
        customer_rows = build_customer_ar_rows(enriched_rows)

        clear_ar_balance_details(conn)
        inserted_count = insert_ar_balance_details(conn, customer_rows)

        total_open_balance = sum(
            float(row.get("total_open_balance") or 0)
            for row in customer_rows
        )

        return {
            "success": True,
            "message": "AR balances synced successfully",
            "user_count": len(user_rows),
            "customer_row_count": inserted_count,
            "total_open_balance": total_open_balance,
        }

    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Failed to sync AR balances",
                "error": str(exc),
            },
        )