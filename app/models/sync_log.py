from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.models.base import Base


class SyncLog(Base):
    __tablename__ = "sync_logs"
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    synced_at = Column(DateTime(timezone=True), nullable=False)
    prs_synced = Column(Integer, nullable=False, default=0)
    prs_skipped = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="success")
    error_message = Column(Text, nullable=True)

    repository = relationship("Repository", back_populates="sync_logs")
