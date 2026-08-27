import secrets
import string

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from db import get_db

from models.roa_data_users import (
    RoaDataUser,
    RoaDataUserRole,
    UserRole,
)

from api.user_access.base import (
    CreateUserRequest,
    CreateUserResponse,
    RoleResponse,
)

from api.auth.authentication import require_roles

from services.email import send_mail


router = APIRouter()

password_hash = PasswordHash.recommended()


def generate_random_password(
    length: int = 8,
) -> str:

    characters = (
        string.ascii_letters
        + string.digits
        + "!@#$%^&*"
    )

    return "".join(
        secrets.choice(characters)
        for _ in range(length)
    )


@router.post(
    "/create-user",
    response_model=CreateUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    payload: CreateUserRequest,

    current_user: RoaDataUser = Depends(
        require_roles(
            UserRole.ADMIN.value
        )
    ),

    db: Session = Depends(
        get_db
    ),
):
    # ========================================================
    # NORMALIZE EMAIL
    # ========================================================

    email = payload.email.strip().lower()

    # ========================================================
    # CHECK IF USER EXISTS
    # ========================================================

    existing_user = (
        db.query(RoaDataUser)
        .filter(
            RoaDataUser.email == email
        )
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )

    # ========================================================
    # GET STAFF ROLE
    # ========================================================

    staff_role = (
        db.query(RoaDataUserRole)
        .filter(
            RoaDataUserRole.name
            == UserRole.STAFF.value
        )
        .first()
    )

    if not staff_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Staff role is not configured",
        )

    # ========================================================
    # GENERATE TEMPORARY PASSWORD
    # ========================================================

    temporary_password = (
        generate_random_password(8)
    )

    hashed_password = (
        password_hash.hash(
            temporary_password
        )
    )

    # ========================================================
    # CREATE USER
    # ========================================================

    new_user = RoaDataUser(
        email=email,
        hashed_password=hashed_password,
        is_active=True,
        role_id=staff_role.id,
        refresh_token=None,
        refresh_token_expires_at=None,
    )

    try:
        db.add(new_user)

        db.commit()

        db.refresh(new_user)

        print(
            "USER CREATED:",
            new_user.id,
            new_user.email,
        )

    except Exception as exc:

        db.rollback()

        print(
            "USER CREATION ERROR:",
            repr(exc),
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )

    # ========================================================
    # BUILD EMAIL
    # ========================================================

    subject = (
        "ROA Data Account Created"
    )

    body = f"""
Hello,

Your ROA Data account has been created successfully.

Please use the following credentials to log in:

Email: {new_user.email}
Password: {temporary_password}

Regards,
Realty Of America
""".strip()

    # ========================================================
    # SEND EMAIL
    # ========================================================

    print(
        "Attempting to send account creation "
        f"email to {new_user.email}"
    )

    email_sent = send_mail(
        subject=subject,
        body=body,
        to_email=new_user.email,
    )

    print(
        "EMAIL SENT RESULT:",
        email_sent,
    )

    if not email_sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "User created successfully, "
                "but failed to send account "
                "creation email"
            ),
        )

    # ========================================================
    # RESPONSE
    # ========================================================

    return CreateUserResponse(
        id=new_user.id,
        email=new_user.email,
        is_active=new_user.is_active,
        role=RoleResponse(
            id=staff_role.id,
            name=staff_role.name,
        ),
    )