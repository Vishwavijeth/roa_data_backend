import base64
import hashlib
import hmac
import requests
from datetime import datetime, timezone
import os

# ── Credentials ───────────────────────────────────────────────────────────
CLIENT_ID     = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ACCESS_KEY    = os.getenv("ACCESS_KEY")
ACCESS_SECRET = os.getenv("ACCESS_SECRET")

LOGIN_URL = "https://api.skyslope.com/auth/login"


def get_session_token() -> str:
    # Step 1: timestamp
    timestamp = datetime.now(timezone.utc).isoformat()

    # Step 2: HMAC-SHA256 signature
    message = f"{CLIENT_ID}:{CLIENT_SECRET}:{timestamp}"
    sig_bytes = hmac.new(
        ACCESS_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    hmac_encoded = base64.b64encode(sig_bytes).decode("utf-8")

    # Step 3: Build headers & payload
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"SS {ACCESS_KEY}:{hmac_encoded}",
        "Timestamp": timestamp,
    }
    payload = {
        "clientID": CLIENT_ID,
        "clientSecret": CLIENT_SECRET,
    }

    # Step 4: POST to login
    response = requests.post(LOGIN_URL, json=payload, headers=headers, timeout=30)

    if response.status_code == 200:
        data = response.json()
        # The API may return the token as a plain string or inside a key
        if isinstance(data, str):
            return data
        # Try common key names
        for key in ("sessionToken", "session", "token", "Session"):
            if key in data:
                return str(data[key])
        # Fallback: return full JSON as string (for debugging)
        raise RuntimeError(f"Token key not found in response: {data}")
    else:
        raise RuntimeError(
            f"SkySlope login failed [{response.status_code}]: {response.text}"
        )


# ── Run directly to test ──────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        token = get_session_token()
        print("Login successful!")
        print("Session Token:", token)
    except RuntimeError as e:
        print("Login failed:", e)
