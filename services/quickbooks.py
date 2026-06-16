import os
import base64
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import requests
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


def basic_auth_header() -> str:
    raw = f"{QB_CLIENT_ID}:{QB_CLIENT_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


def build_frontend_redirect(status: str, reason: Optional[str] = None, realm_id: Optional[str] = None) -> str:
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


def exchange_code_for_tokens(code: str) -> dict:
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

    resp = requests.post(QB_TOKEN_URL, headers=headers, data=data, timeout=30)

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


def refresh_quickbooks_tokens(conn, qb_row):
    headers = {
        "Authorization": basic_auth_header(),
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": qb_row["refresh_token"],
    }

    resp = requests.post(QB_TOKEN_URL, headers=headers, data=data, timeout=30)

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


def get_valid_quickbooks_connection(conn):
    qb_row = get_latest_quickbooks_connection(conn)

    expires_at = qb_row.get("expires_at")
    now = datetime.now(timezone.utc)

    if expires_at is None:
        return refresh_quickbooks_tokens(conn, qb_row)

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now + timedelta(minutes=5):
        return refresh_quickbooks_tokens(conn, qb_row)

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


def fetch_qb_customer_by_email(email: str, conn) -> Dict[str, Any]:
    qb = get_valid_quickbooks_connection(conn)

    realm_id = qb["realm_id"]
    access_token = qb["access_token"]

    safe_email = escape_qbo_value(email)
    query = f"SELECT * FROM Customer WHERE PrimaryEmailAddr = '{safe_email}'"

    url = f"{QBO_API_BASE}/v3/company/{realm_id}/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params = {
        "query": query,
        "minorversion": QBO_MINOR_VERSION,
    }

    resp = requests.get(url, headers=headers, params=params, timeout=30)

    if resp.status_code == 401:
        qb = refresh_quickbooks_tokens(conn, qb)
        headers["Authorization"] = f"Bearer {qb['access_token']}"
        resp = requests.get(url, headers=headers, params=params, timeout=30)

    if resp.status_code >= 400:
        return {
            "email": email,
            "found": False,
            "customer_count": 0,
            "customer_id": None,
            "display_name": None,
            "primary_email": email,
            "balance": None,
            "open_balance": None,
            "error": resp.text,
        }

    payload = resp.json()
    customers = payload.get("QueryResponse", {}).get("Customer", [])

    if not customers:
        return {
            "email": email,
            "found": False,
            "customer_count": 0,
            "customer_id": None,
            "display_name": None,
            "primary_email": email,
            "balance": None,
            "open_balance": None,
            "error": None,
        }

    customer = customers[0]

    return {
        "email": email,
        "found": True,
        "customer_count": len(customers),
        "customer_id": customer.get("Id"),
        "display_name": customer.get("DisplayName"),
        "primary_email": customer.get("PrimaryEmailAddr", {}).get("Address"),
        "balance": customer.get("Balance"),
        "open_balance": customer.get("OpenBalance"),
        "error": None,
    }


def enrich_rows_with_ar_balance(rows: List[Dict[str, Any]], conn) -> List[Dict[str, Any]]:
    email_result_map = {}
    original_email_map = {}

    for row in rows:
        for email in split_emails(row.get("email")):
            email_lower = email.lower()
            if email_lower not in original_email_map:
                original_email_map[email_lower] = email

    for email_lower, original_email in original_email_map.items():
        email_result_map[email_lower] = fetch_qb_customer_by_email(original_email, conn)

    enriched_rows = []
    for row in rows:
        ar_balance = {}

        for email in split_emails(row.get("email")):
            ar_balance[email] = email_result_map.get(
                email.lower(),
                {
                    "email": email,
                    "found": False,
                    "customer_count": 0,
                    "customer_id": None,
                    "display_name": None,
                    "primary_email": email,
                    "balance": None,
                    "open_balance": None,
                    "error": "lookup_missing",
                },
            )

        enriched_rows.append({
            **row,
            "ar_balance": ar_balance,
        })

    return enriched_rows