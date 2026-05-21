from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.models.base import Base


class Repository(Base):
    __tablename__ = "repositories"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    github_repo_id = Column(Integer, nullable=False, index=True)
    full_name = Column(String(255), nullable=False)
    last_synced_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="repositories")
    pull_requests = relationship("PullRequest", back_populates="repository", cascade="all, delete-orphan")
