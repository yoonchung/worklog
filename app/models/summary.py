from datetime import datetime
from sqlalchemy import Column, Integer, ForeignKey, Text, DateTime, Boolean
from sqlalchemy.orm import relationship
from app.models.base import Base


class Summary(Base):
    __tablename__ = "summaries"
    id = Column(Integer, primary_key=True)
    pull_request_id = Column(Integer, ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False)
    summary_text = Column(Text, nullable=False)
    is_resume_worthy = Column(Boolean, default=False, nullable=False)
    user_notes = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    pull_request = relationship("PullRequest", back_populates="summaries")
