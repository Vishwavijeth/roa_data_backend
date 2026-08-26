import secrets
import string
from fastapi import Depends, HTTPException, status, APIRouter
from pwdlib import PasswordHash
from sqlalchemy.orm import Session
from db import get_db
from models.roa_data_users import RoaDataUser, RoaDataUserRole, UserRole
from api.user_access.base import CreateUserRequest, CreateUserResponse
from api.auth.authentication import require_roles

router = APIRouter()

password_hash = PasswordHash.recommended()

def generate_random_password(length: int = 8) -> str:
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(characters) for _ in range(length))

@router.post(
    "/create-user",
    response_model=CreateUserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    payload: CreateUserRequest,
    current_user: RoaDataUser = Depends(require_roles(UserRole.ADMIN.value)),
    db: Session = Depends(get_db),
):
    existing_user = (
        db.query(RoaDataUser)
        .filter(RoaDataUser.email == payload.email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User with this email already exists",
        )

    staff_role = (
        db.query(RoaDataUserRole)
        .filter(RoaDataUserRole.name == UserRole.STAFF.value)
        .first()
    )

    if not staff_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Staff role is not configured",
        )

    temporary_password = generate_random_password(8)

    hashed_password = password_hash.hash(temporary_password)

    new_user = RoaDataUser(
        email=payload.email,
        hashed_password=hashed_password,
        is_active=True,
        role_id=staff_role.id,
        refresh_token=None,
        refresh_token_expires_at=None,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return CreateUserResponse(
        id=new_user.id,
        email=new_user.email,
        role=UserRole.STAFF.value,
        temporary_password=temporary_password,
    )