from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from db import get_db
from services.account_hold_helper import (
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

router = APIRouter()


@router.get("/auth/quickbooks/login")
def quickbooks_login():
    auth_url = build_quickbooks_auth_url()
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/auth/callback")
def quickbooks_callback(request: Request, db: Session = Depends(get_db)):
    raw_conn = db.connection().connection
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
            conn=raw_conn,
            realm_id=realm_id,
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data.get("expires_in"),
            token_type=token_data.get("token_type"),
            scope=token_data.get("scope"),
        )
    except Exception:
        db.rollback()
        return RedirectResponse(
            url=build_frontend_redirect(status="error", reason="token_or_db_failed"),
            status_code=302,
        )

    return RedirectResponse(
        url=build_frontend_redirect(status="connected", realm_id=realm_id),
        status_code=302,
    )


@router.get("/auth/quickbooks/status")
def quickbooks_status(db: Session = Depends(get_db)):
    raw_conn = db.connection().connection
    qb = get_valid_quickbooks_connection(raw_conn)

    return {
        "connected": True,
        "realm_id": qb["realm_id"],
        "expires_at": qb["expires_at"].isoformat() if qb.get("expires_at") else None,
    }

@router.get("/auth/quickbooks/token-status")
async def quickbooks_token_status(db: Session = Depends(get_db)):
    raw_conn = db.connection().connection

    try:
        qb = get_latest_quickbooks_connection(raw_conn)
    except HTTPException:
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

        qb = await refresh_quickbooks_tokens(raw_conn, qb)

        return {
            "connected": True,
            "status": "refreshed",
            "realm_id": qb["realm_id"],
            "message": "Access token refreshed successfully"
        }

    except HTTPException as exc:
        db.rollback()

        return {
            "connected": False,
            "status": "reconnect_required",
            "realm_id": qb.get("realm_id"),
            "message": "QuickBooks reconnect required",
            "error": exc.detail
        }

    except Exception as exc:
        db.rollback()

        return {
            "connected": False,
            "status": "reconnect_required",
            "realm_id": qb.get("realm_id"),
            "message": "QuickBooks reconnect required",
            "error": str(exc)
        }