from __future__ import annotations

from typing import Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationData(BaseModel, Generic[T]):
    total_count: int = Field(default=0, ge=0)
    items: list[T] = Field(default_factory=list)


class PaginationResponseWithCount(BaseModel, Generic[T]):
    success: bool = True
    data: PaginationData[T]
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    count: int = Field(default=0, ge=0)
    total_pages: int = Field(default=1, ge=1)
    has_next: bool = False
    message: str = "Request successful"

class PaginationResponse(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    count: int = Field(default=0, ge=0)
    total_count: int = Field(default=0, ge=0)
    total_pages: int = Field(default=1, ge=1)
    has_next: bool = False
    message: str = "Request successful"