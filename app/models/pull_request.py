from datetime import datetime
from sqlalchemy import Column, Integer, ForeignKey, JSON, Text, DateTime, String
from sqlalchemy.orm import relationship
from app.models.base import Base


class PullRequest(Base):
    __tablename__ = "pull_requests"
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    github_pr_number = Column(Integer, nullable=False, index=True)
    title = Column(String(1024), nullable=False)
    description = Column(Text, nullable=True)
    commits = Column(JSON, nullable=True)  # array of commit messages
    merged_at = Column(DateTime, nullable=True)
    raw_data = Column(JSON, nullable=True)  # full GitHub response
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    repository = relationship("Repository", back_populates="pull_requests")
    summaries = relationship("Summary", back_populates="pull_request", cascade="all, delete-orphan")
