import secrets
import string
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
        code_length: int = 7,
        max_attempts: int = 5,
    ):
        self.repository = UrlRepository(session)
        self.code_length = code_length
        self.max_attempts = max_attempts

    def create(
        self, target_url: str, expires_at: datetime | None
    ) -> Url:
        for _ in range(self.max_attempts):
            code = generate_short_code(self.code_length)
            try:
                return self.repository.create(code, target_url, expires_at)
            except IntegrityError:
                self.repository.session.rollback()
        raise ShortCodeGenerationError(
            "Could not generate an available short code after bounded retries"
        )

    def get_active_and_increment(self, short_code: str) -> Url | None:
        return self.repository.find_active_and_increment(short_code)

    def get(self, short_code: str) -> Url | None:
        return self.repository.find(short_code)
