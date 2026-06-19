"""Typed exceptions mapped to RFC 9457 ``application/problem+json`` responses.

Services raise the typed :class:`EdisError` subclasses; the installed exception
handlers render them as a :class:`Problem` document with the correct HTTP status
and ``content-type: application/problem+json``. This module imports FastAPI lazily
inside :func:`install_exception_handlers` so the error types themselves are usable
anywhere without a web framework present.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

PROBLEM_CONTENT_TYPE = "application/problem+json"

# Base URN for the ``type`` field; stable, dereferenceable-by-convention.
_PROBLEM_TYPE_BASE = "urn:edis:problem"


class Problem(BaseModel):
    """RFC 9457 problem detail document."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None


class EdisError(Exception):
    """Base class for all typed EDIS application errors.

    ``status`` is the HTTP status the exception maps to; ``title`` is the short,
    human-readable RFC 9457 title; ``problem_type`` is the URN identifying the
    error class.
    """

    status: int = 500
    title: str = "Internal Server Error"
    problem_type: str = f"{_PROBLEM_TYPE_BASE}:internal"

    def __init__(self, detail: str | None = None, *, instance: str | None = None) -> None:
        self.detail = detail
        self.instance = instance
        super().__init__(detail or self.title)

    def to_problem(self) -> Problem:
        return Problem(
            type=self.problem_type,
            title=self.title,
            status=self.status,
            detail=self.detail,
            instance=self.instance,
        )


class NotFoundError(EdisError):
    status = 404
    title = "Not Found"
    problem_type = f"{_PROBLEM_TYPE_BASE}:not-found"


class ConflictError(EdisError):
    status = 409
    title = "Conflict"
    problem_type = f"{_PROBLEM_TYPE_BASE}:conflict"


class AuthError(EdisError):
    status = 401
    title = "Unauthorized"
    problem_type = f"{_PROBLEM_TYPE_BASE}:unauthorized"


class ForbiddenError(EdisError):
    status = 403
    title = "Forbidden"
    problem_type = f"{_PROBLEM_TYPE_BASE}:forbidden"


class ValidationProblem(EdisError):
    status = 422
    title = "Unprocessable Entity"
    problem_type = f"{_PROBLEM_TYPE_BASE}:validation"

    def __init__(
        self,
        detail: str | None = None,
        *,
        instance: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        self.errors = errors or []
        super().__init__(detail, instance=instance)


def install_exception_handlers(app) -> None:
    """Register handlers that render exceptions as ``problem+json``.

    Imports FastAPI/Starlette lazily so this module stays import-safe without a
    web stack. Handles :class:`EdisError`, framework request-validation errors,
    and a catch-all for unexpected exceptions.
    """

    from fastapi import FastAPI, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    assert isinstance(app, FastAPI)  # noqa: S101 - dev-time guard

    def _response(problem: Problem) -> JSONResponse:
        return JSONResponse(
            status_code=problem.status,
            content=problem.model_dump(),
            media_type=PROBLEM_CONTENT_TYPE,
        )

    @app.exception_handler(EdisError)
    async def _handle_edis_error(request: Request, exc: EdisError) -> JSONResponse:
        problem = exc.to_problem()
        if problem.instance is None:
            problem.instance = str(request.url)
        return _response(problem)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        problem = Problem(
            type=f"{_PROBLEM_TYPE_BASE}:validation",
            title="Unprocessable Entity",
            status=422,
            detail="Request validation failed.",
            instance=str(request.url),
        )
        body = problem.model_dump()
        body["errors"] = exc.errors()
        return JSONResponse(status_code=422, content=body, media_type=PROBLEM_CONTENT_TYPE)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        problem = Problem(
            type="about:blank",
            title=str(exc.detail) if exc.detail else "HTTP Error",
            status=exc.status_code,
            detail=str(exc.detail) if exc.detail else None,
            instance=str(request.url),
        )
        return _response(problem)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        problem = Problem(
            type=f"{_PROBLEM_TYPE_BASE}:internal",
            title="Internal Server Error",
            status=500,
            detail="An unexpected error occurred.",
            instance=str(request.url),
        )
        return _response(problem)
