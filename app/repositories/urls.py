from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import ClickEvent, Url


class UrlRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self, short_code: str, target_url: str, expires_at: datetime | None
    ) -> Url:
        url = Url(
            short_code=short_code,
            target_url=target_url,
            expires_at=expires_at,
            click_count=0,
        )
        with self.session.begin():
            self.session.add(url)
            self.session.flush()
        return url

    def find_stats(self, short_code: str) -> tuple[Url, int] | None:
        statement = (
            select(Url, func.count(ClickEvent.id))
            .outerjoin(ClickEvent, ClickEvent.short_code == Url.short_code)
            .where(Url.short_code == short_code)
            .group_by(Url.short_code)
        )
        result = self.session.execute(statement).one_or_none()
        if result is None:
            return None
        url, pending_clicks = result
        return url, int(pending_clicks)

    def find(self, short_code: str) -> Url | None:
        return self.session.get(Url, short_code)

    def find_active_and_record_click(
        self,
        short_code: str,
        validate_destination: Callable[[str], None] | None = None,
        async_click_analytics: bool = True,
    ) -> Url | None:
        now = datetime.now(timezone.utc)
        statement = select(Url.short_code, Url.target_url, Url.expires_at).where(
            Url.short_code == short_code,
            (Url.expires_at.is_(None)) | (Url.expires_at > now),
        )
        with self.session.begin():
            result = self.session.execute(statement).one_or_none()
            if result is None:
                return None
            if validate_destination is not None:
                validate_destination(result.target_url)
            if async_click_analytics:
                self.session.add(ClickEvent(short_code=short_code, clicked_at=now))
            else:
                self.session.execute(
                    update(Url)
                    .where(Url.short_code == short_code)
                    .values(click_count=Url.click_count + 1)
                )
            self.session.flush()
        return Url(
            short_code=result.short_code,
            target_url=result.target_url,
            expires_at=result.expires_at,
        )

    def process_click_events(self, batch_size: int) -> int:
        with self.session.begin():
            events = list(
                self.session.scalars(
                    select(ClickEvent)
                    .order_by(ClickEvent.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            if not events:
                return 0

            click_counts: dict[str, int] = {}
            for event in events:
                click_counts[event.short_code] = (
                    click_counts.get(event.short_code, 0) + 1
                )

            for code, count in click_counts.items():
                self.session.execute(
                    update(Url)
                    .where(Url.short_code == code)
                    .values(click_count=Url.click_count + count)
                )
            self.session.execute(
                delete(ClickEvent).where(
                    ClickEvent.id.in_([event.id for event in events])
                )
            )
        return len(events)
