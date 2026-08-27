from math import ceil

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models.roa_data_users import RoaDataUser, RoaDataUserRole
from api.auth.authentication import get_current_user
from common.pagination import PaginationResponseWithCount, PaginationData


router = APIRouter(prefix="/user-access")


class UserListItem(BaseModel):
    email: str
    is_active: bool
    role: str


@router.get(
    "/users",
    response_model=PaginationResponseWithCount[UserListItem],
)
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: RoaDataUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(
            RoaDataUser.email,
            RoaDataUser.is_active,
            RoaDataUserRole.name.label("role"),
        )
        .join(
            RoaDataUserRole,
            RoaDataUser.role_id == RoaDataUserRole.id,
        )
    )

    total_count = query.count()
    offset = (page - 1) * page_size

    rows = (
        query
        .order_by(RoaDataUser.email.asc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    items = [
        UserListItem(
            email=row.email,
            is_active=row.is_active,
            role=row.role,
        )
        for row in rows
    ]

    total_pages = max(1, ceil(total_count / page_size))

    return PaginationResponseWithCount[UserListItem](
        success=True,
        data=PaginationData[UserListItem](
            total_count=total_count,
            items=items,
        ),
        page=page,
        page_size=page_size,
        count=len(items),
        total_pages=total_pages,
        has_next=page < total_pages,
        message="Users fetched successfully",
    )