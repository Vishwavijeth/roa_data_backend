import os
import base64
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

QB_CLIENT_ID = os.getenv("QB_CLIENT_ID")
QB_CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
QB_REDIRECT_URI = os.getenv("QB_REDIRECT_URI")

QB_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_SCOPE = "com.intuit.quickbooks.accounting"

QBO_API_BASE = "https://quickbooks.api.intuit.com"
QBO_MINOR_VERSION = 75

FRONTEND_BASE_URL = "https://roa-data-ui.vercel.app"

oauth_state_store = {}

QB_TIMEOUT = httpx.Timeout(30.0)
QB_CONCURRENCY_LIMIT = int(os.getenv("QB_CONCURRENCY_LIMIT", "5"))


def basic_auth_header() -> str:
    raw = f"{QB_CLIENT_ID}:{QB_CLIENT_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


def build_frontend_redirect(
    status: str,
    reason: Optional[str] = None,
    realm_id: Optional[str] = None,
) -> str:
    params = [f"quickbooks={status}"]
    if reason:
        params.append(f"reason={reason}")
    if realm_id:
        params.append(f"realm_id={realm_id}")
    return f"{FRONTEND_BASE_URL}/?{'&'.join(params)}#pre_cda"


def create_oauth_state() -> str:
    state = secrets.token_urlsafe(32)
    oauth_state_store[state] = True
    return state


def validate_and_remove_state(state: Optional[str]) -> bool:
    if not state or state not in oauth_state_store:
        return False
    oauth_state_store.pop(state, None)
    return True


def remove_state(state: Optional[str]):
    oauth_state_store.pop(state, None)


def build_quickbooks_auth_url() -> str:
    if not QB_CLIENT_ID or not QB_CLIENT_SECRET or not QB_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="QuickBooks env vars are missing")

    state = create_oauth_state()

    params = {
        "client_id": QB_CLIENT_ID,
        "response_type": "code",
        "scope": QB_SCOPE,
        "redirect_uri": QB_REDIRECT_URI,
        "state": state,
    }

    return f"{QB_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> dict:
    headers = {
        "Authorization": basic_auth_header(),
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": QB_REDIRECT_URI,
    }

    async with httpx.AsyncClient(timeout=QB_TIMEOUT) as client:
        resp = await client.post(QB_TOKEN_URL, headers=headers, data=data)

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "QuickBooks token exchange failed",
                "quickbooks_response": resp.text,
            },
        )

    return resp.json()


def save_quickbooks_connection(
    conn,
    realm_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: Optional[int],
    token_type: Optional[str],
    scope: Optional[str],
):
    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quickbooks_connections (
                realm_id,
                access_token,
                refresh_token,
                token_type,
                scope,
                expires_at,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (realm_id)
            DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                token_type = EXCLUDED.token_type,
                scope = EXCLUDED.scope,
                expires_at = EXCLUDED.expires_at,
                updated_at = NOW()
            """,
            (
                realm_id,
                access_token,
                refresh_token,
                token_type,
                scope,
                expires_at,
            ),
        )
    conn.commit()


def get_latest_quickbooks_connection(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, realm_id, access_token, refresh_token, expires_at, token_type, scope
            FROM quickbooks_connections
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="QuickBooks is not connected")

    return row


async def refresh_quickbooks_tokens(conn, qb_row) -> dict:
    headers = {
        "Authorization": basic_auth_header(),
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": qb_row["refresh_token"],
    }

    async with httpx.AsyncClient(timeout=QB_TIMEOUT) as client:
        resp = await client.post(QB_TOKEN_URL, headers=headers, data=data)

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "QuickBooks token refresh failed",
                "quickbooks_response": resp.text,
            },
        )

    token_data = resp.json()

    new_access_token = token_data.get("access_token")
    new_refresh_token = token_data.get("refresh_token")
    new_expires_in = token_data.get("expires_in")
    new_token_type = token_data.get("token_type")
    new_scope = token_data.get("scope")

    new_expires_at = None
    if new_expires_in:
        new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(new_expires_in))

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quickbooks_connections
            SET
                access_token = %s,
                refresh_token = %s,
                token_type = %s,
                scope = %s,
                expires_at = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                new_access_token,
                new_refresh_token,
                new_token_type,
                new_scope,
                new_expires_at,
                qb_row["id"],
            ),
        )
    conn.commit()

    qb_row["access_token"] = new_access_token
    qb_row["refresh_token"] = new_refresh_token
    qb_row["expires_at"] = new_expires_at
    qb_row["token_type"] = new_token_type
    qb_row["scope"] = new_scope

    return qb_row


async def get_valid_quickbooks_connection(conn) -> dict:
    qb_row = get_latest_quickbooks_connection(conn)

    expires_at = qb_row.get("expires_at")
    now = datetime.now(timezone.utc)

    if expires_at is None:
        return await refresh_quickbooks_tokens(conn, qb_row)

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now + timedelta(minutes=5):
        return await refresh_quickbooks_tokens(conn, qb_row)

    return qb_row


def escape_qbo_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def split_emails(email_value: Optional[str]) -> List[str]:
    if not email_value:
        return []

    cleaned = []
    seen = set()

    for part in email_value.split(","):
        email = part.strip()
        if not email:
            continue

        email_lower = email.lower()
        if email_lower in seen:
            continue

        seen.add(email_lower)
        cleaned.append(email)

    return cleaned


def get_qb_customerids_for_emails(emails: List[str], conn) -> Dict[str, str]:
    if not emails:
        return {}

    normalized_emails = []
    seen = set()

    for email in emails:
        email_lower = email.strip().lower()
        if email_lower and email_lower not in seen:
            seen.add(email_lower)
            normalized_emails.append(email_lower)

    if not normalized_emails:
        return {}

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                LOWER(TRIM(email)) AS email,
                qb_customerid
            FROM users
            WHERE email IS NOT NULL
              AND TRIM(email) <> ''
              AND qb_customerid IS NOT NULL
              AND TRIM(qb_customerid::text) <> ''
              AND LOWER(TRIM(email)) = ANY(%s)
            """,
            (normalized_emails,),
        )
        rows = cur.fetchall()

    return {
        row["email"]: str(row["qb_customerid"])
        for row in rows
        if row.get("email") and row.get("qb_customerid")
    }


async def _quickbooks_query(
    client: httpx.AsyncClient,
    realm_id: str,
    access_token: str,
    query: str,
) -> httpx.Response:
    url = f"{QBO_API_BASE}/v3/company/{realm_id}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/text",
    }
    params = {
        "query": query,
        "minorversion": QBO_MINOR_VERSION,
    }
    return await client.get(url, headers=headers, params=params)


async def fetch_qb_invoices_by_customer_id(
    customer_id: str,
    conn,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    qb_row: dict,
) -> Dict[str, Any]:
    async with semaphore:
        realm_id = qb_row["realm_id"]
        access_token = qb_row["access_token"]

        safe_customer_id = escape_qbo_value(str(customer_id))
        query = f"SELECT * FROM Invoice WHERE CustomerRef = '{safe_customer_id}'"

        try:
            resp = await _quickbooks_query(
                client=client,
                realm_id=realm_id,
                access_token=access_token,
                query=query,
            )

            if resp.status_code == 401:
                qb_row = await refresh_quickbooks_tokens(conn, qb_row)
                resp = await _quickbooks_query(
                    client=client,
                    realm_id=qb_row["realm_id"],
                    access_token=qb_row["access_token"],
                    query=query,
                )

            if resp.status_code >= 400:
                return {
                    "customer_id": customer_id,
                    "found": False,
                    "invoice_count": 0,
                    "open_balance": None,
                    "open_invoices": [],
                    "error": resp.text,
                }

            payload = resp.json()
            invoices = payload.get("QueryResponse", {}).get("Invoice", [])

            open_invoices = []
            open_balance = 0.0

            for invoice in invoices:
                balance = float(invoice.get("Balance") or 0)
                if balance != 0:
                    open_invoices.append({
                        "invoice_id": invoice.get("Id"),
                        "doc_number": invoice.get("DocNumber"),
                        "txn_date": invoice.get("TxnDate"),
                        "due_date": invoice.get("DueDate"),
                        "total_amt": invoice.get("TotalAmt"),
                        "balance": balance,
                        "customer_ref": invoice.get("CustomerRef"),
                    })
                    open_balance += balance

            return {
                "customer_id": customer_id,
                "found": True,
                "invoice_count": len(open_invoices),
                "open_balance": open_balance,
                "open_invoices": open_invoices,
                "error": None,
            }

        except Exception as exc:
            return {
                "customer_id": customer_id,
                "found": False,
                "invoice_count": 0,
                "open_balance": None,
                "open_invoices": [],
                "error": str(exc),
            }


async def fetch_ar_balance(rows: List[Dict[str, Any]], conn) -> List[Dict[str, Any]]:
    all_emails = []
    for row in rows:
        all_emails.extend(split_emails(row.get("email")))

    email_to_customerid = get_qb_customerids_for_emails(all_emails, conn)

    unique_customer_ids = []
    seen_customer_ids = set()

    for customer_id in email_to_customerid.values():
        if customer_id not in seen_customer_ids:
            seen_customer_ids.add(customer_id)
            unique_customer_ids.append(customer_id)

    customer_invoice_map: Dict[str, Dict[str, Any]] = {}

    if unique_customer_ids:
        qb_row = await get_valid_quickbooks_connection(conn)
        semaphore = asyncio.Semaphore(QB_CONCURRENCY_LIMIT)

        async with httpx.AsyncClient(timeout=QB_TIMEOUT) as client:
            tasks = [
                fetch_qb_invoices_by_customer_id(
                    customer_id=customer_id,
                    conn=conn,
                    client=client,
                    semaphore=semaphore,
                    qb_row=qb_row,
                )
                for customer_id in unique_customer_ids
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

        for customer_id, result in zip(unique_customer_ids, results):
            if isinstance(result, Exception):
                customer_invoice_map[customer_id] = {
                    "customer_id": customer_id,
                    "found": False,
                    "due_date": None,
                    "total_amt": None,
                    "balance": None,
                    "error": str(result),
                }
            else:
                customer_invoice_map[customer_id] = result

    enriched_rows = []

    for row in rows:
        emails = split_emails(row.get("email"))

        customers_by_email = {}
        total_open_balance = 0.0
        has_match = False

        for email in emails:
            customer_id = email_to_customerid.get(email.lower())
            if not customer_id:
                customers_by_email[email] = {
                    "due_date": None,
                    "total_amt": None,
                    "balance": None,
                }
                continue

            has_match = True
            invoice_data = customer_invoice_map.get(customer_id)

            if not invoice_data:
                customers_by_email[email] = {
                    "due_date": None,
                    "total_amt": None,
                    "balance": None,
                }
                continue

            customers_by_email[email] = {
                "due_date": invoice_data.get("due_date"),
                "total_amt": invoice_data.get("total_amt"),
                "balance": invoice_data.get("balance"),
            }

            if invoice_data.get("balance") is not None:
                total_open_balance += float(invoice_data["balance"])

        enriched_rows.append({
            **row,
            "ar_balance": {
                "has_match": has_match,
                "total_open_balance": total_open_balance if has_match else None,
                "customers": customers_by_email,
            },
        })

    return enriched_rows