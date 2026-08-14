import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas import ErrorDetail, ErrorIssue, ErrorResponse


logger = logging.getLogger(__name__)


class APIError(HTTPException):
    """An expected API failure with a stable machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(status_code=status_code, detail=message)


def documented_error(
    description: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    """Build an OpenAPI response entry for the shared error envelope."""
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": code,
                        "message": message,
                    }
                }
            }
        },
    }


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details)
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


async def http_error_handler(
    _: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    if isinstance(error, APIError):
        return _error_response(
            error.status_code,
            error.code,
            error.message,
            headers=error.headers,
        )

    try:
        status_name = HTTPStatus(error.status_code).name.lower()
        default_message = HTTPStatus(error.status_code).phrase
    except ValueError:
        status_name = "http_error"
        default_message = "HTTP request failed"

    message = error.detail if isinstance(error.detail, str) else default_message
    return _error_response(
        error.status_code,
        status_name,
        message,
        headers=error.headers,
    )


async def validation_error_handler(
    _: Request,
    error: RequestValidationError,
) -> JSONResponse:
    details = [
        ErrorIssue(
            field=".".join(str(part) for part in issue["loc"]),
            message=issue["msg"],
            code=issue["type"],
        )
        for issue in error.errors()
    ]
    return _error_response(
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=details,
    )


async def unexpected_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    logger.exception(
        "Unhandled error while processing %s %s",
        request.method,
        request.url.path,
        exc_info=error,
    )
    return _error_response(
        status_code=500,
        code="internal_server_error",
        message="An unexpected error occurred",
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
