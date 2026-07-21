from typing import Optional, Dict, Any, List, Tuple
import logging
import time
import requests

from db import get_db
from fastapi import APIRouter, Depends, Query, HTTPException
from psycopg2.extras import RealDictCursor, execute_values
from services.account_hold_helper import (
    get_valid_quickbooks_connection,
    split_emails,
)

logger = logging.getLogger(__name__)
router = APIRouter()

QBO_API_BASE = "https://quickbooks.api.intuit.com"
QBO_MINOR_VERSION = 75
QBO_PAGE_SIZE = 1000
DB_UPDATE_BATCH_SIZE = 1000


def chunked(items: List[Tuple[str, int]], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_users_without_qb_customerid(conn, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = """
        SELECT personguid, primary_emailaddress
        FROM brokerage_engine_users
        WHERE qb_customerid IS NULL
          AND primary_emailaddress IS NOT NULL
          AND TRIM(primary_emailaddress) <> ''
        ORDER BY personguid
    """

    params = []
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)

    logger.info(
        "Fetching brokerage_engine_users without qb_customerid",
        extra={"limit": limit},
    )

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    logger.info(
        "Fetched brokerage_engine_users without qb_customerid",
        extra={"count": len(rows)},
    )
    return rows


async def get_quickbooks_session(conn):
    qb = await get_valid_quickbooks_connection(conn)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {qb['access_token']}",
        "Accept": "application/json",
        "Connection": "keep-alive",
    })

    logger.info(
        "Initialized QuickBooks session",
        extra={"realm_id": qb["realm_id"], "minor_version": QBO_MINOR_VERSION},
    )

    return session, qb


async def fetch_all_qb_customers_map(conn) -> Dict[str, Dict[str, Any]]:
    session, qb = await get_quickbooks_session(conn)
    realm_id = qb["realm_id"]

    email_map: Dict[str, Dict[str, Any]] = {}
    start_position = 1
    page_number = 1

    logger.info("Starting QuickBooks customer sync", extra={"realm_id": realm_id})

    try:
        while True:
            query = f"SELECT * FROM Customer STARTPOSITION {start_position} MAXRESULTS {QBO_PAGE_SIZE}"
            url = f"{QBO_API_BASE}/v3/company/{realm_id}/query"
            params = {
                "query": query,
                "minorversion": QBO_MINOR_VERSION,
            }

            logger.info(
                "Fetching QuickBooks customer page",
                extra={
                    "page_number": page_number,
                    "start_position": start_position,
                    "max_results": QBO_PAGE_SIZE,
                },
            )

            resp = session.get(url, params=params, timeout=60)

            if resp.status_code == 401:
                logger.warning(
                    "QuickBooks token expired, refreshing token",
                    extra={"realm_id": realm_id},
                )
                session.close()
                session, qb = await get_quickbooks_session(conn)
                realm_id = qb["realm_id"]
                url = f"{QBO_API_BASE}/v3/company/{realm_id}/query"
                resp = session.get(url, params=params, timeout=60)

            if resp.status_code >= 400:
                logger.error(
                    "QuickBooks customer fetch failed",
                    extra={
                        "status_code": resp.status_code,
                        "response_text": resp.text[:500],
                        "page_number": page_number,
                    },
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": "QuickBooks customer fetch failed",
                        "quickbooks_response": resp.text,
                    },
                )

            payload = resp.json()
            customers = payload.get("QueryResponse", {}).get("Customer", [])

            logger.info(
                "Fetched QuickBooks customer page",
                extra={"page_number": page_number, "customer_count": len(customers)},
            )

            if not customers:
                break

            for customer in customers:
                primary_email = ((customer.get("PrimaryEmailAddr") or {}).get("Address") or "").strip()
                if not primary_email:
                    continue

                email_key = primary_email.lower()

                if email_key not in email_map:
                    customer_id = customer.get("Id")
                    email_map[email_key] = {
                        "customer_id": int(customer_id) if customer_id is not None else None,
                        "display_name": customer.get("DisplayName"),
                        "primary_email": primary_email,
                    }

            if len(customers) < QBO_PAGE_SIZE:
                break

            start_position += QBO_PAGE_SIZE
            page_number += 1

        logger.info(
            "Completed QuickBooks customer sync",
            extra={
                "realm_id": realm_id,
                "customer_emails_loaded": len(email_map),
                "pages_fetched": page_number,
            },
        )
        return email_map

    finally:
        session.close()
        logger.debug("Closed QuickBooks session")


def bulk_update_user_qb_customerids(conn, updates: List[Tuple[str, int]]) -> int:
    if not updates:
        logger.info("No brokerage_engine_users updates to apply")
        return 0

    total_updated = 0

    logger.info(
        "Applying bulk brokerage_engine_users qb_customerid updates",
        extra={"requested_updates": len(updates), "batch_size": DB_UPDATE_BATCH_SIZE},
    )

    try:
        with conn.cursor() as cur:
            for batch_number, batch in enumerate(chunked(updates, DB_UPDATE_BATCH_SIZE), start=1):
                logger.info(
                    "Executing brokerage_engine_users qb_customerid update batch",
                    extra={
                        "batch_number": batch_number,
                        "batch_size": len(batch),
                        "sample_updates": batch[:3],
                    },
                )

                execute_values(
                    cur,
                    """
                    UPDATE brokerage_engine_users AS u
                    SET qb_customerid = v.qb_customerid::integer
                    FROM (VALUES %s) AS v(personguid, qb_customerid)
                    WHERE u.personguid = v.personguid::uuid
                      AND u.qb_customerid IS NULL
                    """,
                    batch,
                    template="(%s, %s)",
                    page_size=len(batch),
                )

                batch_updated = cur.rowcount if cur.rowcount != -1 else 0
                total_updated += batch_updated

                logger.info(
                    "Executed brokerage_engine_users qb_customerid update batch",
                    extra={
                        "batch_number": batch_number,
                        "batch_requested": len(batch),
                        "batch_updated": batch_updated,
                    },
                )

        conn.commit()

        logger.info(
            "Bulk brokerage_engine_users qb_customerid update committed",
            extra={"requested_updates": len(updates), "updated_count": total_updated},
        )
        return total_updated

    except Exception:
        conn.rollback()
        logger.exception("Bulk brokerage_engine_users qb_customerid update failed and transaction rolled back")
        raise


def select_customer_from_email_map(
    emails: List[str],
    qb_email_map: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    for email in emails:
        email_key = email.strip().lower()
        if email_key in qb_email_map:
            return qb_email_map[email_key]
    return None


async def populate_qb_customerids(conn, limit: Optional[int] = None) -> Dict[str, Any]:
    started_at = time.perf_counter()

    logger.info(
        "Starting qb_customerid population for brokerage_engine_users",
        extra={"limit": limit},
    )

    users = get_users_without_qb_customerid(conn, limit=limit)
    qb_email_map = await fetch_all_qb_customers_map(conn)

    updates: List[Tuple[str, int]] = []
    results = []

    summary = {
        "total_users": len(users),
        "matched": 0,
        "updated": 0,
        "not_found": 0,
        "skipped": 0,
        "errors": 0,
        "qb_customer_emails_loaded": len(qb_email_map),
    }

    for user in users:
        try:
            personguid = user["personguid"]
            raw_email = user.get("primary_emailaddress")
            emails = split_emails(raw_email)

            if not emails:
                summary["skipped"] += 1
                logger.debug(
                    "Skipping user due to invalid primary_emailaddress",
                    extra={"personguid": str(personguid)},
                )
                results.append({
                    "personguid": str(personguid),
                    "primary_emailaddress": raw_email,
                    "status": "skipped",
                    "qb_customerid": None,
                    "reason": "no_valid_email",
                })
                continue

            selected_customer = select_customer_from_email_map(emails, qb_email_map)

            if not selected_customer or selected_customer.get("customer_id") is None:
                summary["not_found"] += 1
                logger.debug(
                    "No QuickBooks customer match found for brokerage_engine_user",
                    extra={"personguid": str(personguid), "emails": emails},
                )
                results.append({
                    "personguid": str(personguid),
                    "primary_emailaddress": raw_email,
                    "status": "not_found",
                    "qb_customerid": None,
                    "reason": "no_matching_customer",
                })
                continue

            qb_customerid = int(selected_customer["customer_id"])
            summary["matched"] += 1
            updates.append((str(personguid), qb_customerid))

            results.append({
                "personguid": str(personguid),
                "primary_emailaddress": raw_email,
                "status": "updated",
                "qb_customerid": qb_customerid,
                "matched_email": selected_customer.get("primary_email"),
                "display_name": selected_customer.get("display_name"),
            })

        except Exception:
            summary["errors"] += 1
            logger.exception(
                "Failed processing brokerage_engine_user during qb_customerid population",
                extra={
                    "personguid": str(user.get("personguid")),
                    "primary_emailaddress": user.get("primary_emailaddress"),
                },
            )
            results.append({
                "personguid": str(user.get("personguid")),
                "primary_emailaddress": user.get("primary_emailaddress"),
                "status": "error",
                "qb_customerid": None,
                "reason": "processing_failed",
            })

    if updates:
        logger.info(
            "Prepared updates for brokerage_engine_users",
            extra={
                "update_count": len(updates),
                "sample_updates": updates[:3],
            },
        )

        updated_count = bulk_update_user_qb_customerids(conn, updates)
        summary["updated"] = updated_count

    duration_seconds = round(time.perf_counter() - started_at, 3)
    summary["duration_seconds"] = duration_seconds

    logger.info(
        "Completed qb_customerid population for brokerage_engine_users",
        extra={
            "total_users": summary["total_users"],
            "matched": summary["matched"],
            "updated": summary["updated"],
            "not_found": summary["not_found"],
            "skipped": summary["skipped"],
            "errors": summary["errors"],
            "duration_seconds": duration_seconds,
        },
    )

    return {
        "summary": summary,
        "results": results,
    }


@router.post("/qb-customerid-population")
async def qb_customerid_population(
    limit: Optional[int] = Query(default=None, ge=1),
    conn=Depends(get_db),
):
    logger.info(
        "Received request for qb_customerid population",
        extra={"limit": limit},
    )
    return await populate_qb_customerids(conn=conn, limit=limit)