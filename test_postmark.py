import os
import smtplib

from dotenv import load_dotenv

load_dotenv(override=True)

access_key = os.getenv("POSTMARK_USER_ACCESS_KEY", "").strip()
secret_key = os.getenv("POSTMARK_USER_SECRET_KEY", "").strip()

print("Access key loaded:", bool(access_key))
print("Access key length:", len(access_key))
print("Secret key loaded:", bool(secret_key))
print("Secret key length:", len(secret_key))

try:
    with smtplib.SMTP(
        "smtp.postmarkapp.com",
        587,
        timeout=10,
    ) as smtp:
        print("1. Connected to Postmark")

        smtp.ehlo()

        smtp.starttls()
        smtp.ehlo()

        print("2. TLS started")

        smtp.login(
            access_key,
            secret_key,
        )

        print("3. SMTP AUTH SUCCESS")

except smtplib.SMTPAuthenticationError as exc:
    print("SMTP AUTH FAILED")
    print("Status code:", exc.smtp_code)
    print("Error:", exc.smtp_error)

except Exception as exc:
    print("OTHER ERROR:", type(exc).__name__, str(exc))