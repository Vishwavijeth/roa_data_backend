from __future__ import annotations
from typing import Any, Generic, TypeVar
from pydantic import BaseModel
from fastapi.responses import JSONResponse

T = TypeVar("T")

class Response(BaseModel, Generic[T]):
    success: bool = True
    data: T
    message: str = "Request successful"


class ErrorResponse(BaseModel):
    success: bool = False
    error_code: str
    message: str
    details: list[dict[str, Any]] | None = None

class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details


def build_error_response(
    status_code: int,
    error_code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error_code=error_code,
            message=message,
            details=details,
        ).model_dump(),
    )