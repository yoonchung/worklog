from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    github_id = Column(Integer, unique=True, index=True, nullable=False)
    github_username = Column(String(255), nullable=False)
    access_token = Column(String, nullable=False)  # store encrypted token
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    repositories = relationship("Repository", back_populates="user", cascade="all, delete-orphan")
