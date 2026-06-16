import os
import base64
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from db import get_db
load_dotenv()

router = APIRouter(prefix="", tags=["quickbooks-auth"])

QB_CLIENT_ID = os.getenv("QB_CLIENT_ID")
QB_CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
QB_REDIRECT_URI = os.getenv("QB_REDIRECT_URI")

QB_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_SCOPE = "com.intuit.quickbooks.accounting"

FRONTEND_BASE_URL = "https://roa-data-ui.vercel.app"
FRONTEND_SUCCESS_URL = f"{FRONTEND_BASE_URL}/?quickbooks=connected#pre_cda"
FRONTEND_ERROR_URL = f"{FRONTEND_BASE_URL}/?quickbooks=error#pre_cda"

oauth_state_store = {}


def basic_auth_header() -> str:
    raw = f"{QB_CLIENT_ID}:{QB_CLIENT_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


def build_frontend_redirect(status: str, reason: str | None = None, realm_id: str | None = None) -> str:
    params = [f"quickbooks={status}"]

    if reason:
        params.append(f"reason={reason}")

    if realm_id:
        params.append(f"realm_id={realm_id}")

    return f"{FRONTEND_BASE_URL}/?{'&'.join(params)}#pre_cda"


def save_quickbooks_connection(
    conn,
    realm_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int | None,
    token_type: str | None,
    scope: str | None,
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
        raise HTTPException(status_code=400, detail="QuickBooks token exchange failed")

    return resp.json()


@router.get("/auth/quickbooks/login")
def quickbooks_login():
    if not QB_CLIENT_ID or not QB_CLIENT_SECRET or not QB_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="QuickBooks env vars are missing")

    state = secrets.token_urlsafe(32)
    oauth_state_store[state] = True

    params = {
        "client_id": QB_CLIENT_ID,
        "response_type": "code",
        "scope": QB_SCOPE,
        "redirect_uri": QB_REDIRECT_URI,
        "state": state,
    }

    auth_url = f"{QB_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/auth/callback")
def quickbooks_callback(request: Request, conn=Depends(get_db)):
    error = request.query_params.get("error")
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    realm_id = request.query_params.get("realmId")

    if error:
        oauth_state_store.pop(state, None)
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="quickbooks_auth_error"),
            status_code=302,
        )

    if not code:
        oauth_state_store.pop(state, None)
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="missing_code"),
            status_code=302,
        )

    if not state or state not in oauth_state_store:
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="invalid_state"),
            status_code=302,
        )

    if not realm_id:
        oauth_state_store.pop(state, None)
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="missing_realm_id"),
            status_code=302,
        )

    try:
        token_data = exchange_code_for_tokens(code)

        save_quickbooks_connection(
            conn=conn,
            realm_id=realm_id,
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_type=token_data.get("token_type"),
            scope=token_data.get("scope"),
            expires_in=token_data.get("expires_in"),
        )

    except HTTPException:
        conn.rollback()
        oauth_state_store.pop(state, None)
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="token_exchange_failed"),
            status_code=302,
        )

    except Exception:
        conn.rollback()
        oauth_state_store.pop(state, None)
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="db_save_failed"),
            status_code=302,
        )

    oauth_state_store.pop(state, None)

    return RedirectResponse(
        url=build_frontend_redirect(status="connected", realm_id=realm_id),
        status_code=302,
    )


@router.get("/auth/quickbooks/status")
def quickbooks_status(conn=Depends(get_db)):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT realm_id, expires_at, updated_at
            FROM quickbooks_connections
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()

    if not row:
        return {"connected": False}

    return {
        "connected": True,
        "realm_id": row[0],
        "expires_at": row[1].isoformat() if row[1] else None,
        "updated_at": row[2].isoformat() if row[2] else None,
    }