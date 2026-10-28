import secrets
import string
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Url
from app.repositories.urls import UrlRepository

CODE_ALPHABET = string.ascii_letters + string.digits


class ShortCodeGenerationError(Exception):
    pass


def generate_short_code(length: int = 7) -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))


class UrlService:
    def __init__(
        self,
        session: Session,
        max_attempts: int = 5,
    ):
        self.repository = UrlRepository(session)
        self.max_attempts = max_attempts

    def create(self, target_url: str, expires_at: datetime | None) -> Url:
        for _ in range(self.max_attempts):
            code = generate_short_code()
            try:
                return self.repository.create(code, target_url, expires_at)
            except IntegrityError:
                continue
        raise ShortCodeGenerationError(
            "Could not generate an available short code after bounded retries"
        )

    def get_active_and_record_click(
        self,
        short_code: str,
        validate_destination: Callable[[str], None] | None = None,
    ) -> Url | None:
        return self.repository.find_active_and_record_click(
            short_code,
            validate_destination=validate_destination,
        )

    def process_click_events(self, batch_size: int = 500) -> int:
        return self.repository.process_click_events(batch_size)

    def get(self, short_code: str) -> Url | None:
        return self.repository.find(short_code)
