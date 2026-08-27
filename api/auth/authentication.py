from datetime import datetime, timedelta, timezone
import os

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from db import get_db

from models.roa_data_users import (
    RoaDataUser,
    RoaDataUserRole,
)

from api.auth.base import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    TokenDataResponse,
    LoginResponse,
    UserResponse,
    RoleResponse,
)


load_dotenv()

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


SECRET_KEY = os.getenv("AUTH_SECRET_KEY")

ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES = 30

REFRESH_TOKEN_EXPIRE_DAYS = 7


if not SECRET_KEY:
    raise RuntimeError(
        "AUTH_SECRET_KEY is not configured"
    )


password_hash = PasswordHash.recommended()


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/auth/login"
)


def create_token(
    data: dict,
    expires_delta: timedelta,
    token_type: str,
):
    to_encode = data.copy()

    expire = (
        datetime.now(timezone.utc)
        + expires_delta
    )

    to_encode.update(
        {
            "exp": expire,
            "type": token_type,
        }
    )

    token = jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM,
    )

    return token, expire


def create_access_token(
    user: RoaDataUser,
):
    return create_token(
        {
            "user_id": user.id,
            "email": user.email,
        },
        timedelta(
            minutes=ACCESS_TOKEN_EXPIRE_MINUTES
        ),
        "access",
    )


def create_refresh_token(
    user: RoaDataUser,
):
    return create_token(
        {
            "user_id": user.id,
        },
        timedelta(
            days=REFRESH_TOKEN_EXPIRE_DAYS
        ),
        "refresh",
    )


def decode_token(
    token: str,
):
    try:
        return jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )

    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def get_user_role(
    user: RoaDataUser,
    db: Session,
) -> RoaDataUserRole:

    role = (
        db.query(RoaDataUserRole)
        .filter(
            RoaDataUserRole.id
            == user.role_id
        )
        .first()
    )

    if not role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User role not found",
        )

    return role


def get_current_user(
    token: str = Depends(
        oauth2_scheme
    ),
    db: Session = Depends(
        get_db
    ),
) -> RoaDataUser:

    payload = decode_token(
        token
    )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token required",
        )

    user_id = payload.get(
        "user_id"
    )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = (
        db.query(RoaDataUser)
        .filter(
            RoaDataUser.id
            == user_id
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    return user


def require_roles(
    *allowed_roles: str,
):

    def role_checker(
        current_user: RoaDataUser = Depends(
            get_current_user
        ),
        db: Session = Depends(
            get_db
        ),
    ) -> RoaDataUser:

        role = get_user_role(
            current_user,
            db,
        )

        if role.name not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "You do not have permission "
                    "to access this resource"
                ),
            )

        return current_user

    return role_checker


@router.post(
    "/login",
    response_model=LoginResponse,
)
def login(
    payload: LoginRequest,
    db: Session = Depends(
        get_db
    ),
):

    email = (
        payload.email
        .strip()
        .lower()
    )

    user = (
        db.query(RoaDataUser)
        .filter(
            RoaDataUser.email
            == email
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not password_hash.verify(
        payload.password,
        user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    role = get_user_role(
        user,
        db,
    )

    access_token, _ = (
        create_access_token(
            user
        )
    )

    refresh_token, refresh_expiry = (
        create_refresh_token(
            user
        )
    )

    user.refresh_token = (
        refresh_token
    )

    user.refresh_token_expires_at = (
        refresh_expiry.replace(
            tzinfo=None
        )
    )

    db.commit()

    return LoginResponse(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        role=RoleResponse(
            id=role.id,
            name=role.name,
        ),
        token=TokenDataResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        ),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
)
def refresh(
    payload: RefreshRequest,
    db: Session = Depends(
        get_db
    ),
):

    decoded = decode_token(
        payload.refresh_token
    )

    if decoded.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )

    user_id = decoded.get(
        "user_id"
    )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = (
        db.query(RoaDataUser)
        .filter(
            RoaDataUser.id
            == user_id
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    if not user.refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session",
        )

    if (
        user.refresh_token
        != payload.refresh_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    if not user.refresh_token_expires_at:

        user.refresh_token = None
        user.refresh_token_expires_at = None

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    now_utc = (
        datetime.now(
            timezone.utc
        )
        .replace(
            tzinfo=None
        )
    )

    if (
        user.refresh_token_expires_at
        <= now_utc
    ):

        user.refresh_token = None
        user.refresh_token_expires_at = None

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    new_access_token, _ = (
        create_access_token(
            user
        )
    )

    (
        new_refresh_token,
        new_refresh_expiry,
    ) = create_refresh_token(
        user
    )

    user.refresh_token = (
        new_refresh_token
    )

    user.refresh_token_expires_at = (
        new_refresh_expiry.replace(
            tzinfo=None
        )
    )

    db.commit()

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
    )


@router.post(
    "/logout"
)
def logout(
    payload: RefreshRequest,
    db: Session = Depends(
        get_db
    ),
):

    decoded = decode_token(
        payload.refresh_token
    )

    if decoded.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )

    user_id = decoded.get(
        "user_id"
    )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = (
        db.query(RoaDataUser)
        .filter(
            RoaDataUser.id
            == user_id
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session",
        )

    if (
        user.refresh_token
        != payload.refresh_token
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    user.refresh_token = None
    user.refresh_token_expires_at = None

    db.commit()

    return {
        "message": "Logged out successfully"
    }


# =================================================
# Current User
# =================================================


@router.get(
    "/me",
    response_model=UserResponse,
)
def me(
    current_user: RoaDataUser = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):

    role = get_user_role(
        current_user,
        db,
    )

    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        is_active=current_user.is_active,
        role=RoleResponse(
            id=role.id,
            name=role.name,
        ),
    )