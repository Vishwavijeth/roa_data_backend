import os
from dotenv import load_dotenv
import secrets
import base64
from urllib.parse import urlencode

import requests
from fastapi import HTTPException, Request, APIRouter
from fastapi.responses import RedirectResponse

load_dotenv()

QB_CLIENT_ID = os.getenv("QB_CLIENT_ID")
QB_CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET")
QB_REDIRECT_URI = os.getenv("QB_REDIRECT_URI")  # https://your-backend.vercel.app/auth/callback
FRONTEND_SUCCESS_URL = os.getenv("FRONTEND_SUCCESS_URL", "https://your-frontend.vercel.app/success")
FRONTEND_ERROR_URL = os.getenv("FRONTEND_ERROR_URL", "https://your-frontend.vercel.app/error")

QB_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QB_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QB_SCOPE = "com.intuit.quickbooks.accounting"

oauth_state_store = {}
token_store = {}

router = APIRouter()


def basic_auth_header() -> str:
    raw = f"{QB_CLIENT_ID}:{QB_CLIENT_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("utf-8")


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
def quickbooks_callback(request: Request):
    error = request.query_params.get("error")
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    realm_id = request.query_params.get("realmId")

    if error:
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?reason=quickbooks_auth_error&error={error}",
            status_code=302,
        )

    if not code:
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?reason=missing_code",
            status_code=302,
        )

    if not state or state not in oauth_state_store:
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?reason=invalid_state",
            status_code=302,
        )

    if not realm_id:
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?reason=missing_realm_id",
            status_code=302,
        )

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
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?reason=token_exchange_failed",
            status_code=302,
        )

    token_data = resp.json()

    token_store[realm_id] = {
        "realm_id": realm_id,
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data.get("expires_in"),
        "token_type": token_data.get("token_type"),
    }

    oauth_state_store.pop(state, None)

    return RedirectResponse(
        url=f"{FRONTEND_SUCCESS_URL}?connected=true&realm_id={realm_id}",
        status_code=302,
    )