from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import PullRequest, Repository, Summary, User

router = APIRouter(prefix="/summaries")
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
async def list_summaries(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    summaries = (
        db.query(Summary)
        .join(PullRequest, Summary.pull_request_id == PullRequest.id)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .filter(Repository.user_id == current_user.id)
        .options(
            joinedload(Summary.pull_request).joinedload(PullRequest.repository)
        )
        # .order_by(Summary.generated_at.desc())
        .order_by(Summary.pull_request_id.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "summaries.html", {"user": current_user, "summaries": summaries}
    )


class SummaryPatch(BaseModel):
    is_resume_worthy: Optional[bool] = None
    user_notes: Optional[str] = None

    def model_post_init(self, __context) -> None:
        if self.is_resume_worthy is None and self.user_notes is None:
            raise ValueError("At least one field must be provided")
        if self.user_notes is not None and len(self.user_notes) > 2000:
            raise ValueError("user_notes must be 2000 characters or fewer")


@router.patch("/{summary_id}")
async def patch_summary(
    summary_id: int,
    body: SummaryPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    summary = (
        db.query(Summary)
        .join(PullRequest, Summary.pull_request_id == PullRequest.id)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .filter(Summary.id == summary_id, Repository.user_id == current_user.id)
        .first()
    )
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    if body.is_resume_worthy is not None:
        summary.is_resume_worthy = body.is_resume_worthy
    if body.user_notes is not None:
        summary.user_notes = body.user_notes

    db.commit()
    return {"status": "ok"}
