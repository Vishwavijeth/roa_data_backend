from datetime import datetime, timedelta, timezone
import hashlib
import os

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from pwdlib import PasswordHash
from db import get_db
from models.roa_data_users import RoaDataUser
from services.email import send_mail
from api.auth.base import (
    ForgotPasswordRequest, 
    ForgotPasswordResponse, 
    ResetPasswordRequest, 
    ResetPasswordResponse
)

load_dotenv()
password_hash = PasswordHash.recommended()


router = APIRouter(prefix="/auth")

SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
ALGORITHM = "HS256"
PASSWORD_RESET_EXPIRE_MINUTES = 6
FRONTEND_URL = os.getenv("FRONTEND_HOST").rstrip("/")

if not SECRET_KEY:
    raise RuntimeError("AUTH_SECRET_KEY is not configured")


def get_password_fingerprint(hashed_password: str) -> str:
    return hashlib.sha256(hashed_password.encode("utf-8")).hexdigest()


def create_password_reset_token(user: RoaDataUser) -> str:
    """
    Creates a short-lived JWT specifically for resetting the password.
    Nothing is stored in the database.
    """

    expire = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES)

    password_fingerprint = get_password_fingerprint(user.hashed_password)

    payload = {
        "user_id": user.id,
        "email": user.email,
        "type": "password_reset",
        "password_fingerprint": password_fingerprint,
        "exp": expire,
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()

    generic_response = ForgotPasswordResponse(
        message="If an account exists with this email, a password reset link has been sent."
    )

    user = db.query(RoaDataUser).filter(RoaDataUser.email == email).first()

    if not user:
        return generic_response

    if not user.is_active:
        return generic_response

    reset_token = create_password_reset_token(user)

    reset_url = f"{FRONTEND_URL}/reset-password?token={reset_token}"

    subject = "ROA Data Password Reset"

    body = f"""
Hello,

We received a request to reset the password for your ROA Data account.

Please click the link below to reset your password:

{reset_url}

This password reset link will expire in {PASSWORD_RESET_EXPIRE_MINUTES} minutes.

If you did not request a password reset, please ignore this email.

Regards,
Realty Of America
""".strip()

    print(f"Attempting to send password reset email to {user.email}")

    try:
        email_sent = send_mail(
            subject=subject,
            body=body,
            to_email=user.email,
        )
    except Exception as exc:
        print("PASSWORD RESET EMAIL ERROR:", repr(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send password reset email",
        )

    print("PASSWORD RESET EMAIL SENT RESULT:", email_sent)

    if not email_sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send password reset email",
        )

    return generic_response


@router.post("/reset-password", response_model=ResetPasswordResponse, status_code=status.HTTP_200_OK)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        decoded = jwt.decode(payload.token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired password reset link")

    if decoded.get("type") != "password_reset":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid password reset token")

    user_id = decoded.get("user_id")

    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid password reset token")

    user = db.query(RoaDataUser).filter(RoaDataUser.id == user_id).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid password reset token")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    token_email = decoded.get("email")

    if not token_email or token_email.lower() != user.email.lower():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid password reset token")

    token_password_fingerprint = decoded.get("password_fingerprint")
    current_password_fingerprint = get_password_fingerprint(user.hashed_password)

    if not token_password_fingerprint or token_password_fingerprint != current_password_fingerprint:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password reset link has already been used or is invalid")

    user.hashed_password = password_hash.hash(payload.new_password)
    user.refresh_token = None
    user.refresh_token_expires_at = None

    try:
        db.commit()
        db.refresh(user)
    except Exception as exc:
        db.rollback()
        print("PASSWORD RESET ERROR:", repr(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to reset password")

    return ResetPasswordResponse(message="Password reset successfully")