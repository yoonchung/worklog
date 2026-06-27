import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from fetch import extract_pr_evaluation_data, fetch_merged_pull_requests, raw_pr_data
from summarize import build_prompt, call_anthropic, load_anthropic_client, parse_llm_response

from app.models import PullRequest, Summary
from app.models.repository import Repository

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 512
SYNC_COOLDOWN_SECONDS = 3600  # 1 hour


def seconds_until_sync_allowed(repo: Repository) -> int | None:
    """Returns seconds remaining in cooldown, or None if a sync is allowed now."""
    if repo.last_synced_at is None:
        return None
    elapsed = (datetime.now(UTC) - repo.last_synced_at).total_seconds()
    remaining = SYNC_COOLDOWN_SECONDS - elapsed
    return int(remaining) if remaining > 0 else None


def _save_pr_and_summary(session: Session, repo_id: int, pr_obj, pr_info: dict, summary_dict: dict):
    raw = raw_pr_data(pr_obj)
    if raw is None:
        raw = {
            "number": pr_obj.number,
            "title": pr_obj.title,
            "body": pr_obj.body,
            "merged_at": str(pr_obj.merged_at),
        }

    pr = session.query(PullRequest).filter_by(repo_id=repo_id, github_pr_number=pr_obj.number).first()
    if not pr:
        pr = PullRequest(
            repo_id=repo_id,
            github_pr_number=pr_obj.number,
            title=pr_obj.title,
            description=pr_obj.body,
            merged_at=pr_obj.merged_at,
            commits=pr_info.get("commit_messages"),
            raw_data=raw,
            created_at=datetime.now(UTC),
        )
        session.add(pr)
        session.flush()

    summary = session.query(Summary).filter_by(pull_request_id=pr.id).first()
    if summary:
        summary.summary_text = summary_dict.get("summary", "")
        summary.is_resume_worthy = summary_dict.get("is_resume_worthy", False)
        summary.user_notes = summary_dict.get("notes", "")
        summary.generated_at = datetime.now(UTC)
    else:
        summary = Summary(
            pull_request_id=pr.id,
            summary_text=summary_dict.get("summary", ""),
            is_resume_worthy=summary_dict.get("is_resume_worthy", False),
            user_notes=summary_dict.get("notes", ""),
            generated_at=datetime.now(UTC),
        )
        session.add(summary)
    session.flush()


def run_sync(repo: Repository, access_token: str, session: Session) -> dict:
    """Fetch and summarize all merged PRs for a repo, saving results to DB."""
    client = load_anthropic_client()

    logger.info("Starting sync for %s", repo.full_name)
    merged_prs = fetch_merged_pull_requests(repo.full_name, access_token)
    logger.info("Found %d merged PR(s) for %s", len(merged_prs), repo.full_name)

    saved = 0
    skipped = 0
    for pr in merged_prs:
        pr_info = extract_pr_evaluation_data(pr)
        prompt = build_prompt(pr_info, item_type="pr")
        try:
            resp = call_anthropic(client, prompt, model=DEFAULT_MODEL, max_tokens=DEFAULT_MAX_TOKENS)
        except Exception as e:
            logger.error("Failed to summarize PR #%d: %s", pr.number, e)
            skipped += 1
            continue

        summary_dict = parse_llm_response(resp)
        try:
            with session.begin_nested():
                _save_pr_and_summary(session, repo.id, pr, pr_info, summary_dict)
            saved += 1
        except Exception as e:
            logger.error("Failed to save PR #%d to DB: %s", pr.number, e)
            skipped += 1

    repo.last_synced_at = datetime.now(UTC)
    session.commit()
    logger.info("Sync complete for %s: %d saved, %d skipped", repo.full_name, saved, skipped)
    return {"synced": saved, "skipped": skipped, "total": len(merged_prs)}
