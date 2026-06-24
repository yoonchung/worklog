import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import decrypt_token
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Repository, User
from app.sync import run_sync, seconds_until_sync_allowed

router = APIRouter(prefix="/repos")
templates = Jinja2Templates(directory="templates")


def _validate_full_name(full_name: str) -> str:
    full_name = full_name.strip()
    if full_name.count("/") != 1:
        raise HTTPException(status_code=400, detail="Repository must be in 'owner/repo' format")
    owner, repo = full_name.split("/")
    if not owner or not repo:
        raise HTTPException(status_code=400, detail="Owner and repository name must not be empty")
    if len(owner) > 100 or len(repo) > 100:
        raise HTTPException(status_code=400, detail="Owner or repository name is too long")
    return full_name


async def _fetch_github_repo(full_name: str, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{full_name}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Repository '{full_name}' not found on GitHub")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to verify repository on GitHub")
    return resp.json()


@router.get("", response_class=HTMLResponse)
async def list_repos(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repos = db.query(Repository).filter_by(user_id=current_user.id).all()
    return templates.TemplateResponse(
        request, "repos.html", {"repos": repos, "user": current_user}
    )


@router.post("", response_class=RedirectResponse)
async def add_repo(
    full_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    full_name = _validate_full_name(full_name)

    existing = db.query(Repository).filter_by(user_id=current_user.id, full_name=full_name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Repository '{full_name}' is already connected")

    access_token = decrypt_token(current_user.access_token)
    github_repo = await _fetch_github_repo(full_name, access_token)

    repo = Repository(
        user_id=current_user.id,
        github_repo_id=github_repo["id"],
        full_name=github_repo["full_name"],
    )
    db.add(repo)
    db.commit()

    return RedirectResponse(url="/repos", status_code=303)


@router.post("/{repo_id}/delete", response_class=RedirectResponse)
async def delete_repo_form(
    repo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repo = db.get(Repository, repo_id)
    if not repo or repo.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Repository not found")
    db.delete(repo)
    db.commit()
    return RedirectResponse(url="/repos", status_code=303)


@router.post("/{repo_id}/sync")
def sync_repo(
    repo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repo = db.get(Repository, repo_id)
    if not repo or repo.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Repository not found")

    remaining = seconds_until_sync_allowed(repo)
    if remaining is not None:
        mins, secs = divmod(remaining, 60)
        raise HTTPException(
            status_code=429,
            detail=f"Synced too recently. Try again in {mins}m {secs}s.",
        )

    access_token = decrypt_token(current_user.access_token)
    result = run_sync(repo, access_token, db)
    return {"status": "ok", **result}


@router.delete("/{repo_id}")
async def delete_repo(
    repo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repo = db.get(Repository, repo_id)
    if not repo or repo.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Repository not found")
    db.delete(repo)
    db.commit()
    return {"detail": "Repository disconnected"}
