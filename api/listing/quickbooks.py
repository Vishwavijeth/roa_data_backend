from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from datetime import datetime, timezone, timedelta
from db import get_db
from services.quickbooks import (
    build_frontend_redirect,
    build_quickbooks_auth_url,
    exchange_code_for_tokens,
    get_valid_quickbooks_connection,
    remove_state,
    save_quickbooks_connection,
    validate_and_remove_state,
    refresh_quickbooks_tokens,
    get_latest_quickbooks_connection
)

router = APIRouter(tags=["quickbooks-auth"])


@router.get("/auth/quickbooks/login")
def quickbooks_login():
    auth_url = build_quickbooks_auth_url()
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/auth/callback")
def quickbooks_callback(request: Request, conn=Depends(get_db)):
    error = request.query_params.get("error")
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    realm_id = request.query_params.get("realmId")

    if error:
        remove_state(state)
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="quickbooks_auth_error"),
            status_code=302,
        )

    if not code:
        remove_state(state)
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="missing_code"),
            status_code=302,
        )

    if not validate_and_remove_state(state):
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="invalid_state"),
            status_code=302,
        )

    if not realm_id:
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
            expires_in=token_data.get("expires_in"),
            token_type=token_data.get("token_type"),
            scope=token_data.get("scope"),
        )
    except Exception:
        conn.rollback()
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="token_or_db_failed"),
            status_code=302,
        )

    return RedirectResponse(
        url=build_frontend_redirect(status="connected", realm_id=realm_id),
        status_code=302,
    )


@router.get("/auth/quickbooks/status")
def quickbooks_status(conn=Depends(get_db)):
    qb = get_valid_quickbooks_connection(conn)

    return {
        "connected": True,
        "realm_id": qb["realm_id"],
        "expires_at": qb["expires_at"].isoformat() if qb.get("expires_at") else None,
    }

@router.get("/auth/quickbooks/token-status")
def quickbooks_token_status(conn=Depends(get_db)):
    try:
        qb = get_latest_quickbooks_connection(conn)
    except Exception:
        return {
            "connected": False,
            "status": "reconnect_required",
            "message": "QuickBooks is not connected"
        }

    try:
        expires_at = qb.get("expires_at")
        now = datetime.now(timezone.utc)

        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at > now + timedelta(minutes=5):
                return {
                    "connected": True,
                    "status": "valid",
                    "realm_id": qb["realm_id"],
                    "message": "Access token is valid"
                }

        refresh_quickbooks_tokens(conn, qb)

        return {
            "connected": True,
            "status": "refreshed",
            "realm_id": qb["realm_id"],
            "message": "Access token refreshed successfully"
        }

    except Exception:
        conn.rollback()
        return {
            "connected": False,
            "status": "reconnect_required",
            "message": "QuickBooks reconnect required"
        }