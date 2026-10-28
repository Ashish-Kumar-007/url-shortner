from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Url(Base):
    __tablename__ = "urls"
    __table_args__ = (
        CheckConstraint("length(short_code) = 7", name="ck_urls_short_code_length"),
        CheckConstraint("click_count >= 0", name="ck_urls_click_count_nonnegative"),
    )

    short_code: Mapped[str] = mapped_column(String(7), primary_key=True)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ClickEvent(Base):
    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    short_code: Mapped[str] = mapped_column(
        String(7),
        ForeignKey("urls.short_code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clicked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
