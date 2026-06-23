from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel

from common.pagination import PaginationMeta

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: Optional[str] = None
    message: Optional[str] = None
    details: Optional[Any] = None


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    pagination: Optional[PaginationMeta] = None
    data: T
    error: Optional[ErrorDetail] = None


def build_success_response(data: Any) -> ApiResponse[Any]:
    return ApiResponse(
        success=True,
        pagination=None,
        data=data,
        error=None,
    )


def build_paginated_response(items: list[Any], pagination: PaginationMeta) -> ApiResponse[list[Any]]:
    return ApiResponse(
        success=True,
        pagination=pagination,
        data=items,
        error=None,
    )


def build_error_response(
    message: str,
    code: Optional[str] = None,
    details: Optional[Any] = None,
    data: Any = None,
) -> ApiResponse[Any]:
    return ApiResponse(
        success=False,
        pagination=None,
        data=data if data is not None else {},
        error=ErrorDetail(
            code=code,
            message=message,
            details=details,
        ),
    )