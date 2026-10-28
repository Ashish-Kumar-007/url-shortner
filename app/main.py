from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import create_database
from app.models import Base
from app.schemas import ShortenRequest, ShortenResponse, UrlStatsResponse
from app.services.urls import ShortCodeGenerationError, UrlService


def create_app(
    app_settings: Settings | None = None,
    database_url: str | None = None,
) -> FastAPI:
    configured = app_settings or Settings()
    if database_url is not None:
        configured = configured.model_copy(update={"database_url": database_url})
    app_engine, app_session_factory = create_database(configured)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(bind=app_engine)
        yield
        app_engine.dispose()

    application = FastAPI(title="URL Shortener", version="0.1.0", lifespan=lifespan)
    application.state.session_factory = app_session_factory
    application.state.settings = configured

    def session_dependency(request: Request):
        with request.app.state.session_factory() as session:
            yield session

    DbSession = Annotated[Session, Depends(session_dependency)]

    @application.get("/health")
    def health(session: DbSession):
        try:
            session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok"}

    @application.post(
        "/api/shorten", response_model=ShortenResponse, status_code=201
    )
    def shorten(payload: ShortenRequest, request: Request, session: DbSession):
        configured_settings: Settings = request.app.state.settings
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
    def redirect(short_code: str, session: DbSession):
        url = UrlService(session).get_active_and_increment(short_code)
        if url is None:
            raise HTTPException(status_code=404, detail="Short link not found")
        return RedirectResponse(url=url.target_url, status_code=302)

    @application.get(
        "/api/urls/{short_code}/stats", response_model=UrlStatsResponse
    )
    def stats(short_code: str, session: DbSession):
        url = UrlService(session).get(short_code)
        if url is None:
            raise HTTPException(status_code=404, detail="Short link not found")
        return url

    return application


app = create_app()
