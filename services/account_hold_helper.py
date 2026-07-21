import os
import base64
import secrets
import asyncio
import random
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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

QBO_REQUESTS_PER_MINUTE = int(os.getenv("QBO_REQUESTS_PER_MINUTE", "500"))
QBO_MAX_CONCURRENT = int(os.getenv("QBO_MAX_CONCURRENT", "10"))
QBO_BATCH_REQUESTS_PER_MINUTE = int(os.getenv("QBO_BATCH_REQUESTS_PER_MINUTE", "40"))
QBO_MAX_BATCH_PAYLOADS = int(os.getenv("QBO_MAX_BATCH_PAYLOADS", "30"))
QBO_MAX_RETRIES = int(os.getenv("QBO_MAX_RETRIES", "5"))
QBO_RETRY_MAX_DELAY_SECONDS = float(os.getenv("QBO_RETRY_MAX_DELAY_SECONDS", "30"))

_qbo_limiters: Dict[str, "QBORateLimiter"] = {}
_qbo_limiters_lock = asyncio.Lock()
_qbo_refresh_locks: Dict[str, asyncio.Lock] = {}
_qbo_refresh_locks_guard = asyncio.Lock()


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
                LOWER(TRIM(primary_emailaddress)) AS email,
                qb_customerid
            FROM brokerage_engine_users
            WHERE primary_emailaddress IS NOT NULL
              AND TRIM(primary_emailaddress) <> ''
              AND qb_customerid IS NOT NULL
              AND LOWER(TRIM(primary_emailaddress)) = ANY(%s)
            """,
            (normalized_emails,),
        )
        rows = cur.fetchall()

    return {
        row["email"]: str(row["qb_customerid"])
        for row in rows
        if row.get("email") and row.get("qb_customerid")
    }


class QBORateLimiter:
    def __init__(
        self,
        requests_per_minute: int = QBO_REQUESTS_PER_MINUTE,
        max_concurrent: int = QBO_MAX_CONCURRENT,
        batch_requests_per_minute: int = QBO_BATCH_REQUESTS_PER_MINUTE,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.max_concurrent = max_concurrent
        self.batch_requests_per_minute = batch_requests_per_minute

        self._request_timestamps = deque()
        self._batch_timestamps = deque()
        self._concurrency = asyncio.Semaphore(max_concurrent)
        self._window_lock = asyncio.Lock()

    async def acquire(self, is_batch: bool = False) -> None:
        await self._concurrency.acquire()
        try:
            await self._wait_for_window_slot(is_batch=is_batch)
        except Exception:
            self._concurrency.release()
            raise

    def release(self) -> None:
        self._concurrency.release()

    async def _wait_for_window_slot(self, is_batch: bool = False) -> None:
        while True:
            sleep_for = 0.0
            now = time.monotonic()

            async with self._window_lock:
                self._prune(now)

                if len(self._request_timestamps) >= self.requests_per_minute:
                    oldest_request = self._request_timestamps[0]
                    sleep_for = max(sleep_for, 60 - (now - oldest_request))

                if is_batch and len(self._batch_timestamps) >= self.batch_requests_per_minute:
                    oldest_batch = self._batch_timestamps[0]
                    sleep_for = max(sleep_for, 60 - (now - oldest_batch))

                if sleep_for <= 0:
                    current = time.monotonic()
                    self._request_timestamps.append(current)
                    if is_batch:
                        self._batch_timestamps.append(current)
                    return

            await asyncio.sleep(sleep_for + random.uniform(0.02, 0.2))

    def _prune(self, now: float) -> None:
        while self._request_timestamps and now - self._request_timestamps[0] >= 60:
            self._request_timestamps.popleft()

        while self._batch_timestamps and now - self._batch_timestamps[0] >= 60:
            self._batch_timestamps.popleft()


async def get_qbo_limiter(realm_id: str) -> QBORateLimiter:
    async with _qbo_limiters_lock:
        limiter = _qbo_limiters.get(realm_id)
        if limiter is None:
            limiter = QBORateLimiter()
            _qbo_limiters[realm_id] = limiter
        return limiter


async def get_refresh_lock(realm_id: str) -> asyncio.Lock:
    async with _qbo_refresh_locks_guard:
        lock = _qbo_refresh_locks.get(realm_id)
        if lock is None:
            lock = asyncio.Lock()
            _qbo_refresh_locks[realm_id] = lock
        return lock


def _parse_retry_after(response: httpx.Response) -> Optional[float]:
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None

    try:
        return max(float(retry_after), 0.0)
    except ValueError:
        pass

    try:
        retry_dt = parsedate_to_datetime(retry_after)
        if retry_dt.tzinfo is None:
            retry_dt = retry_dt.replace(tzinfo=timezone.utc)
        delay = (retry_dt - datetime.now(timezone.utc)).total_seconds()
        return max(delay, 0.0)
    except Exception:
        return None


def _compute_retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = _parse_retry_after(response)
    if retry_after is not None:
        return min(retry_after, QBO_RETRY_MAX_DELAY_SECONDS)

    base_delay = min(2 ** attempt, QBO_RETRY_MAX_DELAY_SECONDS)
    jitter = random.uniform(0.1, 0.8)
    return min(base_delay + jitter, QBO_RETRY_MAX_DELAY_SECONDS)


async def _quickbooks_query_once(
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


async def _refresh_tokens_once_for_shared_row(conn, qb_row: dict) -> dict:
    realm_id = str(qb_row["realm_id"])
    lock = await get_refresh_lock(realm_id)

    async with lock:
        latest = get_latest_quickbooks_connection(conn)

        if str(latest["realm_id"]) != realm_id:
            latest = qb_row

        expires_at = latest.get("expires_at")
        now = datetime.now(timezone.utc)

        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at > now + timedelta(minutes=5):
                qb_row.update(latest)
                return qb_row

        refreshed = await refresh_quickbooks_tokens(conn, latest)
        qb_row.update(refreshed)
        return qb_row


async def quickbooks_query_with_retry(
    client: httpx.AsyncClient,
    conn,
    qb_row: dict,
    query: str,
    max_retries: int = QBO_MAX_RETRIES,
) -> httpx.Response:
    realm_id = str(qb_row["realm_id"])
    limiter = await get_qbo_limiter(realm_id)
    refreshed_after_401 = False

    for attempt in range(max_retries + 1):
        await limiter.acquire(is_batch=False)
        try:
            response = await _quickbooks_query_once(
                client=client,
                realm_id=realm_id,
                access_token=qb_row["access_token"],
                query=query,
            )
        finally:
            limiter.release()

        if response.status_code < 400:
            return response

        if response.status_code == 401 and not refreshed_after_401:
            qb_row = await _refresh_tokens_once_for_shared_row(conn, qb_row)
            realm_id = str(qb_row["realm_id"])
            refreshed_after_401 = True
            continue

        if response.status_code == 429:
            if attempt >= max_retries:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "message": "QuickBooks rate limit exceeded after retries",
                        "realm_id": realm_id,
                        "query": query,
                    },
                )

            delay = _compute_retry_delay(response, attempt)
            await asyncio.sleep(delay)
            continue

        raise HTTPException(
            status_code=response.status_code,
            detail={
                "message": "QuickBooks query failed",
                "realm_id": realm_id,
                "query": query,
                "quickbooks_response": response.text,
            },
        )

    raise HTTPException(
        status_code=500,
        detail={"message": "Unexpected QuickBooks retry flow failure"},
    )


async def fetch_qb_invoices_by_customer_id(
    customer_id: str,
    conn,
    client: httpx.AsyncClient,
    qb_row: dict,
) -> Dict[str, Any]:
    realm_id = str(qb_row["realm_id"])
    safe_customer_id = escape_qbo_value(str(customer_id))
    query = f"SELECT * FROM Invoice WHERE CustomerRef = '{safe_customer_id}'"

    try:
        resp = await quickbooks_query_with_retry(
            client=client,
            conn=conn,
            qb_row=qb_row,
            query=query,
        )

        payload = resp.json()
        invoices = payload.get("QueryResponse", {}).get("Invoice", []) or []

        open_invoices = []
        open_balance = 0.0

        for invoice in invoices:
            balance = float(invoice.get("Balance") or 0)
            if balance != 0:
                open_invoices.append(
                    {
                        "invoice_id": invoice.get("Id"),
                        "doc_number": invoice.get("DocNumber"),
                        "txn_date": invoice.get("TxnDate"),
                        "due_date": invoice.get("DueDate"),
                        "total_amt": float(invoice.get("TotalAmt") or 0),
                        "balance": balance,
                        "customer_ref": invoice.get("CustomerRef"),
                    }
                )
                open_balance += balance

        return {
            "customer_id": customer_id,
            "realm_id": realm_id,
            "found": True,
            "invoice_count": len(open_invoices),
            "open_balance": open_balance,
            "open_invoices": open_invoices,
            "error": None,
        }

    except Exception as exc:
        return {
            "customer_id": customer_id,
            "realm_id": realm_id,
            "found": False,
            "invoice_count": 0,
            "open_balance": None,
            "open_invoices": [],
            "error": str(exc),
        }


def summarize_customer_invoice(invoice_data: Dict[str, Any]) -> Dict[str, Any]:
    if not invoice_data:
        return {
            "invoice_count": 0,
            "due_date": None,
            "total_amt": None,
            "balance": None,
            "open_invoices": [],
            "error": None,
        }

    open_invoices = invoice_data.get("open_invoices") or []
    open_balance = invoice_data.get("open_balance")
    error = invoice_data.get("error")

    latest_due_invoice = None
    sortable_invoices = [inv for inv in open_invoices if inv.get("due_date")]
    if sortable_invoices:
        latest_due_invoice = sorted(
            sortable_invoices,
            key=lambda inv: inv.get("due_date"),
            reverse=True,
        )[0]
    elif open_invoices:
        latest_due_invoice = open_invoices[0]

    return {
        "invoice_count": invoice_data.get("invoice_count", 0),
        "due_date": latest_due_invoice.get("due_date") if latest_due_invoice else None,
        "total_amt": latest_due_invoice.get("total_amt") if latest_due_invoice else None,
        "balance": open_balance,
        "open_invoices": open_invoices,
        "error": error,
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

        async with httpx.AsyncClient(timeout=QB_TIMEOUT) as client:
            tasks = [
                fetch_qb_invoices_by_customer_id(
                    customer_id=customer_id,
                    conn=conn,
                    client=client,
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
                    "invoice_count": 0,
                    "open_balance": None,
                    "open_invoices": [],
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
                    "customer_id": None,
                    "invoice_count": 0,
                    "due_date": None,
                    "total_amt": None,
                    "balance": None,
                    "open_invoices": [],
                    "error": None,
                }
                continue

            has_match = True
            invoice_data = customer_invoice_map.get(customer_id)

            if not invoice_data:
                customers_by_email[email] = {
                    "customer_id": customer_id,
                    "invoice_count": 0,
                    "due_date": None,
                    "total_amt": None,
                    "balance": None,
                    "open_invoices": [],
                    "error": None,
                }
                continue

            summary = summarize_customer_invoice(invoice_data)

            customers_by_email[email] = {
                "customer_id": customer_id,
                "invoice_count": summary["invoice_count"],
                "due_date": summary["due_date"],
                "total_amt": summary["total_amt"],
                "balance": summary["balance"],
                "open_invoices": summary["open_invoices"],
                "error": summary["error"],
            }

            if summary["balance"] is not None:
                total_open_balance += float(summary["balance"])

        enriched_rows.append(
            {
                **row,
                "ar_balance": {
                    "has_match": has_match,
                    "total_open_balance": total_open_balance if has_match else None,
                    "customers": customers_by_email,
                },
            }
        )

    return enriched_rows