import time

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import create_database
from app.models import Base, ClickEvent
from app.observability import configure_logging
from app.services.urls import UrlService

logger = configure_logging()


def run_worker() -> None:
    settings = Settings()
    engine, session_factory = create_database(settings)
    Base.metadata.create_all(bind=engine)
    try:
        while True:
            try:
                with session_factory() as session:
                    processed = UrlService(session).process_click_events(
                        settings.analytics_batch_size
                    )
                    pending = (
                        session.scalar(select(func.count()).select_from(ClickEvent))
                        or 0
                    )
                if processed:
                    logger.info(
                        "Processed click analytics batch",
                        extra={
                            "processed_events": processed,
                            "pending_events": pending,
                        },
                    )
                else:
                    time.sleep(settings.analytics_poll_interval_seconds)
            except SQLAlchemyError:
                logger.exception("Click analytics worker database failure")
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    run_worker()
