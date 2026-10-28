from datetime import datetime, timezone

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator


class ShortenRequest(BaseModel):
    target_url: AnyHttpUrl = Field(max_length=2048)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def expiration_must_be_future_and_timezone_aware(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("expires_at must include a timezone")
            if value <= datetime.now(timezone.utc):
                raise ValueError("expires_at must be in the future")
            return value.astimezone(timezone.utc)
        return None


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    target_url: str
    expires_at: datetime | None


class UrlStatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    short_code: str
    target_url: str
    created_at: datetime
    expires_at: datetime | None
    click_count: int
