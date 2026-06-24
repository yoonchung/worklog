import sys
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import PullRequest, Repository, Summary, User

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from summarize import call_anthropic, load_anthropic_client

router = APIRouter(prefix="/export")
templates = Jinja2Templates(directory="templates")

POLISH_MODEL = "claude-haiku-4-5"
POLISH_MAX_TOKENS = 1024


def _fetch_resume_worthy(db: Session, user_id: int) -> list[Summary]:
    return (
        db.query(Summary)
        .join(PullRequest, Summary.pull_request_id == PullRequest.id)
        .join(Repository, PullRequest.repo_id == Repository.id)
        .filter(Repository.user_id == user_id, Summary.is_resume_worthy == True)
        .options(joinedload(Summary.pull_request).joinedload(PullRequest.repository))
        .order_by(Repository.full_name, PullRequest.merged_at.desc())
        .all()
    )


def _group_by_repo(summaries: list[Summary]) -> dict[str, list[Summary]]:
    grouped: dict[str, list[Summary]] = defaultdict(list)
    for s in summaries:
        grouped[s.pull_request.repository.full_name].append(s)
    return dict(grouped)


def _format_plain(grouped: dict[str, list[Summary]]) -> str:
    lines = []
    for repo, summaries in grouped.items():
        lines.append(f"## {repo}")
        for s in summaries:
            lines.append(f"• {s.summary_text.strip()}")
        lines.append("")
    return "\n".join(lines).strip()


@router.get("", response_class=HTMLResponse)
async def export_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    summaries = _fetch_resume_worthy(db, current_user.id)
    grouped = _group_by_repo(summaries)
    plain_text = _format_plain(grouped)
    return templates.TemplateResponse(
        request,
        "export.html",
        {
            "user": current_user,
            "grouped": grouped,
            "plain_text": plain_text,
            "total": len(summaries),
        },
    )


@router.post("/polish", response_class=PlainTextResponse)
def polish_export(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from fastapi import HTTPException
    summaries = _fetch_resume_worthy(db, current_user.id)
    if not summaries:
        raise HTTPException(status_code=422, detail="No resume-worthy summaries to polish")
    grouped = _group_by_repo(summaries)
    raw = _format_plain(grouped)

    prompt = f"""You are a technical resume editor. Below are PR summaries from a software engineer's GitHub history, grouped by repository. Each bullet is a raw summary of a merged pull request.

Your task:
- Rewrite these as polished, concise resume bullet points
- Group them by theme (e.g. "Backend / API", "Infrastructure", "Tooling", "Frontend") rather than by repository
- Each bullet should start with a strong action verb and quantify impact where possible
- Remove redundancy if multiple PRs cover the same area
- Output only the bullet points, grouped under theme headers (## Theme Name)
- Do not include preamble or explanation

Raw summaries:
{raw}"""

    client = load_anthropic_client()
    result = call_anthropic(client, prompt, model=POLISH_MODEL, max_tokens=POLISH_MAX_TOKENS)

    if hasattr(result, "content"):
        return result.content[0].text
    return str(result)
