import html
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from prometheus_client import make_asgi_app
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import create_database
from app.models import Base
from app.observability import (
    REQUEST_DURATION,
    REQUESTS,
    configure_logging,
)
from app.safety import validate_redirect_destination
from app.schemas import ShortenRequest, ShortenResponse, UrlStatsResponse
from app.seo import render_landing_page
from app.services.urls import ShortCodeGenerationError, UrlService

logger = configure_logging()


def _error(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                **({"details": details} if details is not None else {}),
            }
        },
    )


def _canonical_base(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("BASE_URL must be an absolute HTTP or HTTPS URL")
    return base_url.rstrip("/")


def _client_address(request: Request, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            address = forwarded_for.split(",", 1)[0].strip()
            if address:
                return address
    return request.client.host if request.client else "unknown"


def _add_security_headers(response: Response, canonical_base: str) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    if canonical_base.startswith("https://"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000"


def create_app(
    app_settings: Settings | None = None,
    database_url: str | None = None,
) -> FastAPI:
    configured = app_settings or Settings()
    if database_url is not None:
        configured = configured.model_copy(update={"database_url": database_url})
    canonical_base = _canonical_base(configured.base_url)
    app_engine, app_session_factory = create_database(configured)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(bind=app_engine)
        yield
        app_engine.dispose()

    application = FastAPI(title="URL Shortener", version="0.2.0", lifespan=lifespan)
    application.state.session_factory = app_session_factory
    application.state.settings = configured
    application.state.engine = app_engine
    application.state.create_rate_buckets = {}
    application.state.rate_limit_requests = 0

    def session_dependency(request: Request):
        with request.app.state.session_factory() as session:
            yield session

    DbSession = Annotated[Session, Depends(session_dependency)]

    @application.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException):
        code = {
            404: "not_found",
            422: "validation_error",
            503: "service_unavailable",
        }.get(exc.status_code, "request_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _error(exc.status_code, code, message)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        details = [
            {
                "location": list(error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return _error(422, "validation_error", "Request validation failed", details)

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(request: Request, exc: SQLAlchemyError):
        logger.exception(
            "Database operation failed",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "method": request.method,
            },
        )
        return _error(503, "database_unavailable", "Database unavailable")

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        logger.exception(
            "Unexpected request failure",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "method": request.method,
            },
        )
        return _error(500, "internal_error", "An unexpected error occurred")

    @application.middleware("http")
    async def observe_requests(request: Request, call_next):
        request_id = secrets.token_hex(8)
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            if request.method == "POST" and request.url.path == "/api/shorten":
                now = time.monotonic()
                address = _client_address(request, configured.trust_proxy_headers)
                buckets: dict[str, tuple[float, int]] = (
                    request.app.state.create_rate_buckets
                )
                window = configured.create_rate_limit_window_seconds
                bucket = buckets.get(address)
                if bucket is None or now - bucket[0] >= window:
                    bucket = (now, 0)
                if bucket[1] >= configured.create_rate_limit_count:
                    status_code = 429
                    response = _error(
                        429,
                        "rate_limit_exceeded",
                        "Too many link creation requests",
                    )
                    response.headers["Retry-After"] = str(
                        max(1, int(window - (now - bucket[0])))
                    )
                    response.headers["X-Request-ID"] = request_id
                    _add_security_headers(response, canonical_base)
                    return response
                buckets[address] = (bucket[0], bucket[1] + 1)

                request.app.state.rate_limit_requests += 1
                if request.app.state.rate_limit_requests % 256 == 0:
                    expired = [
                        key
                        for key, value in buckets.items()
                        if now - value[0] >= window
                    ]
                    for key in expired:
                        del buckets[key]
                    while len(buckets) > 10_000:
                        oldest = min(buckets, key=lambda key: buckets[key][0])
                        del buckets[oldest]

            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            _add_security_headers(response, canonical_base)
            return response
        finally:
            duration = time.perf_counter() - started
            route = getattr(request.scope.get("route"), "path", "unmatched")
            if request.url.path != "/metrics":
                REQUESTS.labels(request.method, route, str(status_code)).inc()
                REQUEST_DURATION.labels(request.method, route).observe(duration)
            logger.info(
                "HTTP request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 3),
                },
            )

    @application.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing_page():
        return render_landing_page(canonical_base, configured.seo_indexable)

    @application.get("/robots.txt", include_in_schema=False)
    def robots_txt():
        if not configured.seo_indexable:
            return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")
        return Response(
            f"User-agent: *\nAllow: /\nSitemap: {canonical_base}/sitemap.xml\n",
            media_type="text/plain",
        )

    @application.get("/sitemap.xml", include_in_schema=False)
    def sitemap():
        if not configured.seo_indexable:
            raise HTTPException(status_code=404, detail="Sitemap is disabled")
        location = html.escape(canonical_base, quote=True)
        return Response(
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{location}/</loc></url></urlset>",
            media_type="application/xml",
        )

    application.mount("/metrics", make_asgi_app())

    @application.get("/health")
    @application.get("/health/ready", include_in_schema=False)
    def readiness(request: Request, session: DbSession):
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception(
                "Readiness database check failed",
                extra={
                    "request_id": getattr(request.state, "request_id", None),
                    "path": "/health/ready",
                    "method": "GET",
                },
            )
            return _error(503, "database_unavailable", "Database unavailable")
        return {"status": "ok", "database": "ok"}

    @application.get("/health/live", include_in_schema=False)
    def liveness():
        return {"status": "ok"}

    @application.post("/api/shorten", response_model=ShortenResponse, status_code=201)
    def shorten(payload: ShortenRequest, request: Request, session: DbSession):
        configured_settings: Settings = request.app.state.settings
        if configured_settings.block_private_destinations:
            try:
                validate_redirect_destination(str(payload.target_url))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        service = UrlService(
            session,
            max_attempts=configured_settings.code_generation_attempts,
        )
        try:
            url = service.create(str(payload.target_url), payload.expires_at)
        except ShortCodeGenerationError as exc:
            raise HTTPException(
                status_code=503, detail="Could not allocate a short link"
            ) from exc
        return ShortenResponse(
            short_code=url.short_code,
            short_url=f"{configured_settings.base_url.rstrip('/')}/{url.short_code}",
            target_url=url.target_url,
            expires_at=url.expires_at,
        )

    @application.get("/{short_code}")
    def redirect(short_code: str, request: Request, session: DbSession):
        configured_settings: Settings = request.app.state.settings
        validator = (
            validate_redirect_destination
            if configured_settings.block_private_destinations
            else None
        )
        try:
            url = UrlService(session).get_active_and_record_click(
                short_code,
                validate_destination=validator,
                async_click_analytics=configured_settings.async_click_analytics,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=410, detail="Short link is no longer available"
            ) from exc
        if url is None:
            raise HTTPException(status_code=404, detail="Short link not found")
        return RedirectResponse(url=url.target_url, status_code=302)

    @application.get("/api/urls/{short_code}/stats", response_model=UrlStatsResponse)
    def stats(short_code: str, session: DbSession):
        result = UrlService(session).get_stats(short_code)
        if result is None:
            raise HTTPException(status_code=404, detail="Short link not found")
        url, pending_clicks = result
        return UrlStatsResponse(
            short_code=url.short_code,
            target_url=url.target_url,
            created_at=url.created_at,
            expires_at=url.expires_at,
            click_count=url.click_count + pending_clicks,
        )

    return application


app = create_app()
