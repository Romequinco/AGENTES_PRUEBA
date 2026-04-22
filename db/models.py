import os
from datetime import datetime, timezone

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean,
    DateTime, Float, ForeignKey, Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise EnvironmentError(
        "DATABASE_URL no está definido en el entorno. "
        "Configura una URL de PostgreSQL (ej: postgresql://user:pass@host/db)."
    )

# Railway expone URLs con scheme 'postgres://' pero SQLAlchemy >= 1.4 requiere 'postgresql://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(254), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    tier = Column(SAEnum("free", "premium", "pro", name="user_tier"), nullable=False, default="free")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    subscription = relationship("NewsletterSubscriber", back_populates="user", uselist=False)
    stripe_subscription = relationship("Subscription", back_populates="user", uselist=False)
    alerts = relationship("Alert", back_populates="user")


class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    active = Column(Boolean, nullable=False, default=True)
    subscribed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="subscription")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    stripe_customer_id = Column(String(128), nullable=True, index=True)
    stripe_subscription_id = Column(String(128), nullable=True, index=True)
    tier = Column(SAEnum("free", "premium", "pro", name="sub_tier"), nullable=False, default="free")
    status = Column(SAEnum("active", "cancelled", "past_due", name="sub_status"), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="stripe_subscription")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    condition_type = Column(
        SAEnum("price_above", "price_below", "rsi_above", "rsi_below", name="alert_condition"),
        nullable=False,
    )
    condition_value = Column(Float, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    triggered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="alerts")


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
