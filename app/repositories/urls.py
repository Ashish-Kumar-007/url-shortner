from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Url


class UrlRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, short_code: str, target_url: str, expires_at: datetime | None) -> Url:
        url = Url(
            short_code=short_code,
            target_url=target_url,
            expires_at=expires_at,
            click_count=0,
        )
        self.session.add(url)
        self.session.commit()
        self.session.refresh(url)
        return url

    def find(self, short_code: str) -> Url | None:
        return self.session.get(Url, short_code)

    def find_active_and_increment(self, short_code: str) -> Url | None:
        now = datetime.now(timezone.utc)
        statement = (
            update(Url)
            .where(
                Url.short_code == short_code,
                (Url.expires_at.is_(None)) | (Url.expires_at > now),
            )
            .values(click_count=Url.click_count + 1)
            .returning(Url.short_code, Url.target_url, Url.expires_at)
        )
        result = self.session.execute(statement).one_or_none()
        if result is None:
            self.session.rollback()
            return None

        self.session.commit()
        return Url(
            short_code=result.short_code,
            target_url=result.target_url,
            expires_at=result.expires_at,
        )
