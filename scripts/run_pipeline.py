"""
End-to-end pipeline: Fetch PR data from GitHub → Summarize with LLM → Save to PostgreSQL
"""
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Add parent directory to sys.path for app imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models import Base, User, Repository, PullRequest, Summary
from fetch import (
    extract_pr_evaluation_data,
    fetch_merged_pull_requests,
    raw_pr_data,
    load_github_token,
    validate_repo_name,
)
from summarize import (
    load_anthropic_client,
    build_prompt,
    call_anthropic,
    parse_llm_response,
)

# Constants
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 512
COMMIT_BATCH_SIZE = 10
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


def retry_with_backoff(func, *args, max_retries=MAX_RETRIES, delay=RETRY_DELAY, **kwargs):
    """Retry a function with exponential backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.warning("Attempt %d/%d failed: %s", attempt, max_retries, e)
            if attempt == max_retries:
                logger.error("All %d attempts failed. Raising exception.", max_retries)
                raise
            time.sleep(delay * 2 ** (attempt - 1))


def get_or_create_user(session, github_username, github_id, access_token):
    """Get or create a User record."""
    user = session.query(User).filter_by(github_id=github_id).first()
    if not user:
        user = User(
            github_id=github_id,
            github_username=github_username,
            access_token=access_token,
        )
        session.add(user)
        session.flush()
    return user


def get_or_create_repository(session, user_id, repo_full_name, github_repo_id):
    """Get or create a Repository record."""
    repo = session.query(Repository).filter_by(github_repo_id=github_repo_id).first()
    if not repo:
        repo = Repository(
            user_id=user_id,
            github_repo_id=github_repo_id,
            full_name=repo_full_name,
            last_synced_at=datetime.now(UTC),
        )
        session.add(repo)
        session.flush()
    return repo


def save_pr_and_summary(session, repo_id, pr_obj, summary_dict):
    """Save PR and its summary to database."""
    # Always use fresh raw data from the PR object
    raw = raw_pr_data(pr_obj)
    if raw is None:
        raw = {
            "number": pr_obj.number,
            "title": pr_obj.title,
            "body": pr_obj.body,
            "user": pr_obj.user.login if pr_obj.user else None,
            "merged_at": str(pr_obj.merged_at),
            "state": pr_obj.state,
        }

    # Create or update PullRequest
    pr = session.query(PullRequest).filter_by(
        repo_id=repo_id, github_pr_number=pr_obj.number
    ).first()
    if not pr:
        pr = PullRequest(
            repo_id=repo_id,
            github_pr_number=pr_obj.number,
            title=pr_obj.title,
            description=pr_obj.body,
            merged_at=pr_obj.merged_at,
            raw_data=raw,
            created_at=datetime.now(UTC),
        )
        session.add(pr)
        session.flush()

    # Create or update Summary record
    summary_text = summary_dict.get("summary", "")
    is_resume_worthy = summary_dict.get("is_resume_worthy", False)
    user_notes = summary_dict.get("notes", "")

    summary = session.query(Summary).filter_by(pull_request_id=pr.id).first()
    if summary:
        summary.summary_text = summary_text
        summary.is_resume_worthy = is_resume_worthy
        summary.user_notes = user_notes
        summary.generated_at = datetime.now(UTC)
    else:
        summary = Summary(
            pull_request_id=pr.id,
            summary_text=summary_text,
            is_resume_worthy=is_resume_worthy,
            user_notes=user_notes,
            generated_at=datetime.now(UTC),
        )
        session.add(summary)
    session.flush()

    return pr, summary


def main():
    if len(sys.argv) < 2:
        logger.error("Usage: python run_pipeline.py owner/repo [output_filename]")
        sys.exit(1)

    repo_full_name = sys.argv[1].strip()
    output_filename = sys.argv[2].strip() if len(sys.argv) > 2 else "db_pull_requests.json"

    # Validate repository format
    try:
        validate_repo_name(repo_full_name)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)

    load_dotenv()
    
    # Initialize database connection
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/worklog_db",
    )
    engine = create_engine(database_url, echo=False, future=True)
    Session = sessionmaker(bind=engine)

    saved_prs = []
    saved_summaries = []

    # Use session as context manager for proper cleanup
    with Session() as session:
        # Initialize GitHub and Anthropic clients
        github_token = load_github_token()
        anthropic_client = load_anthropic_client()
        
        # Get configurable model from environment
        model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        max_tokens = int(os.environ.get("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS))

        logger.info("=" * 80)
        logger.info("Starting pipeline for %s", repo_full_name)
        logger.info("=" * 80)

        # Step 1: Fetch PRs from GitHub with retry
        logger.info("[Step 1] Fetching merged PRs from GitHub...")
        try:
            merged_prs = retry_with_backoff(fetch_merged_pull_requests, repo_full_name, github_token)
        except Exception as e:
            logger.error("Failed to fetch PRs after retries: %s", e)
            return

        if not merged_prs:
            logger.info("No merged PRs found. Exiting.")
            return
        
        logger.info("✓ Found %d merged PR(s)", len(merged_prs))

        # Get or create user and repository (reuse repo object already fetched with PRs)
        repo_obj = merged_prs[0].base.repo
        owner_name = repo_obj.owner.login
        owner_id = repo_obj.owner.id
        user = get_or_create_user(session, owner_name, owner_id, github_token)
        repository = get_or_create_repository(
            session, user.id, repo_full_name, repo_obj.id
        )
        session.commit()

        # Step 2: Summarize and save to database
        logger.info("[Step 2] Summarizing %d PR(s) with LLM and saving to database...", len(merged_prs))

        batch_count = 0
        
        for idx, pr in enumerate(merged_prs, 1):
            logger.info("  [%d/%d] PR #%d: %s", idx, len(merged_prs), pr.number, pr.title)

            pr_info = extract_pr_evaluation_data(pr)
            
            # Build and call summarization prompt with retry
            prompt = build_prompt(pr_info, item_type="pr")
            try:
                resp = retry_with_backoff(call_anthropic, anthropic_client, prompt, model=model, max_tokens=max_tokens)
            except Exception as e:
                logger.error("Failed to get LLM response for PR #%d: %s", pr.number, e)
                continue
            
            # Parse summary JSON using extracted helper
            summary_dict = parse_llm_response(resp)
            
            # Save to database
            try:
                with session.begin_nested():
                    pr_record, summary_record = save_pr_and_summary(
                        session, repository.id, pr, summary_dict
                    )

                saved_prs.append({
                    "id": pr_record.id,
                    "repo_id": pr_record.repo_id,
                    "github_pr_number": pr_record.github_pr_number,
                    "title": pr_record.title,
                    "description": pr_record.description,
                    "merged_at": pr_record.merged_at.isoformat() if pr_record.merged_at is not None else None,
                    "raw_data": pr_record.raw_data,
                    "created_at": pr_record.created_at.isoformat() if pr_record.created_at is not None else None,
                })
                saved_summaries.append({
                    "id": summary_record.id,
                    "pull_request_id": summary_record.pull_request_id,
                    "summary_text": summary_record.summary_text,
                    "is_resume_worthy": summary_record.is_resume_worthy,
                    "user_notes": summary_record.user_notes,
                    "generated_at": summary_record.generated_at.isoformat() if summary_record.generated_at is not None else None,
                })
                logger.info("    → Saved to database (PullRequest ID: %d)", pr_record.id)

                batch_count += 1
                # Batch commit for better performance
                if batch_count % COMMIT_BATCH_SIZE == 0:
                    session.commit()
                    logger.info("  [Batch commit after %d PRs]", batch_count)

            except Exception as e:
                logger.error("    ✗ Error saving PR #%d to database: %s", pr.number, e)

        # Final commit for any remaining records
        if batch_count % COMMIT_BATCH_SIZE != 0:
            session.commit()
            logger.info("  [Final batch commit after %d PRs]", batch_count)

    # Step 3: Save JSON output files (outside session context)
    logger.info("[Step 3] Saving results to worklog directory...")
    worklog_dir = Path(__file__).resolve().parent.parent
    
    # Save persisted pull request records
    pr_output_path = worklog_dir / output_filename
    with pr_output_path.open("w", encoding="utf-8") as f:
        json.dump(saved_prs, f, indent=2, ensure_ascii=False, default=str)
    logger.info("  ✓ Pull request data saved to %s", pr_output_path)
    
    # Save persisted summary records
    summary_path = worklog_dir / "db_summaries.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(saved_summaries, f, indent=2, ensure_ascii=False, default=str)
    logger.info("  ✓ Summaries saved to %s", summary_path)

    # Step 4: Print confirmation
    logger.info("=" * 80)
    logger.info("Pipeline completed successfully!")
    logger.info("=" * 80)
    logger.info("Summary:")
    logger.info("  • Repository: %s", repo_full_name)
    logger.info("  • PRs processed: %d", len(saved_prs))
    logger.info("  • Resume-worthy: %d", sum(1 for d in saved_summaries if d["is_resume_worthy"]))
    logger.info("  • Database: %d records saved", len(saved_prs))
    logger.info("  • Output files:")
    logger.info("    - %s", pr_output_path)
    logger.info("    - %s", summary_path)
    logger.info("")


if __name__ == "__main__":
    main()
