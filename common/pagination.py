from math import ceil
from typing import Optional
from fastapi import Depends, Query
from pydantic import BaseModel, Field

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Number of records per page",
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def get_pagination_params(
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Records per page",
    ),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_records: int
    total_pages: int
    has_next: bool
    has_previous: bool
    next_page: Optional[int] = None
    previous_page: Optional[int] = None


def build_pagination_meta(total_records: int, pagination: PaginationParams) -> PaginationMeta:
    total_pages = ceil(total_records / pagination.page_size) if total_records > 0 else 0
    has_next = pagination.page < total_pages
    has_previous = pagination.page > 1

    return PaginationMeta(
        page=pagination.page,
        page_size=pagination.page_size,
        total_records=total_records,
        total_pages=total_pages,
        has_next=has_next,
        has_previous=has_previous,
        next_page=pagination.page + 1 if has_next else None,
        previous_page=pagination.page - 1 if has_previous else None,
    )