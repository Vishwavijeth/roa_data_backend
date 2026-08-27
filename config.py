import os
from dotenv import load_dotenv

load_dotenv()


POSTMARK_USER_ACCESS_KEY = os.getenv("POSTMARK_USER_ACCESS_KEY")
POSTMARK_USER_SECRET_KEY = os.getenv("POSTMARK_USER_SECRET_KEY")
POSTMARK_SMTP_HOST = "smtp.postmarkapp.com"
POSTMARK_SMTP_PORT = 587
EMAIL_SENDER_NAME = os.getenv(
    "EMAIL_SENDER_NAME",
    "Realty Of America",
)
EMAIL_SENDER_ADDRESS = os.getenv(
    "EMAIL_SENDER_ADDRESS",
    "noreply@roaworld.com",
)
SITE_ADDRESS = os.getenv(
    "SITE_ADDRESS",
    "realtyofamerica.com",
)
