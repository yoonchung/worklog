import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from github import Auth, Github

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_github_token() -> str:
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN not found in .env. Add a line like: GITHUB_TOKEN=your_token_here"
        )
    return token


def validate_repo_name(repo_full_name: str) -> None:
    """Validate that repo_full_name is in 'owner/repo' format."""
    if "/" not in repo_full_name:
        logger.error("Invalid repository format: '%s'. Expected 'owner/repo'.", repo_full_name)
        sys.exit(1)
    parts = repo_full_name.split("/")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        logger.error("Invalid repository format: '%s'. Expected 'owner/repo' with non-empty parts.", repo_full_name)
        sys.exit(1)


def fetch_merged_pull_requests(repo_full_name: str, token: str, max_prs: int | None = None) -> list:
    """Fetch merged pull requests from a GitHub repository.

    Args:
        repo_full_name: GitHub repo in format "owner/repo"
        token: GitHub personal access token
        max_prs: Maximum number of PRs to fetch (None = fetch all)

    Returns:
        List of merged pull request objects
    """
    auth = Auth.Token(token=token)
    gh = Github(auth=auth)
    repo = gh.get_repo(repo_full_name)
    pulls = repo.get_pulls(state="closed", sort="updated", direction="desc")

    merged_prs = []
    count = 0
    for pr in pulls:
        if pr.is_merged():
            merged_prs.append(pr)
            count += 1
            if max_prs and count >= max_prs:
                break
    return merged_prs


def fetch_commits(repo_full_name: str, token: str, max_commits: int = None):
    """
    Fetch commits directly from a repository (alternative to PR-based fetching).
    
    Args:
        repo_full_name: GitHub repo in format "owner/repo"
        token: GitHub personal access token
        max_commits: Maximum number of commits to fetch (None = fetch all)
    
    Returns:
        List of commit objects
    """
    auth = Auth.Token(token=token)
    gh = Github(auth=auth)
    repo = gh.get_repo(repo_full_name)
    commits = repo.get_commits()
    
    commit_list = []
    count = 0
    for commit in commits:
        commit_list.append(commit)
        count += 1
        if max_commits and count >= max_commits:
            break
    return commit_list


def get_commit_messages(pr, max_message_length: int = 200):
    """Extract commit messages from a PR's commits.

    Args:
        pr: PullRequest object from PyGithub
        max_message_length: Truncate messages longer than this (default 200 chars)

    Returns:
        List of commit message strings (truncated if too long)
    """
    messages = []
    for commit in pr.get_commits():
        msg = commit.commit.message
        if msg and len(msg) > max_message_length:
            msg = msg[:max_message_length] + "..."
        messages.append(msg)
    return messages


def raw_pr_data(pr):
    """Retrieve raw data from a PR object.

    This reads PyGithub internal attributes (_rawData or raw_data) that may
    have been cached during initial fetch. It does NOT make additional API calls.

    Args:
        pr: PullRequest object from PyGithub

    Returns:
        Dict of raw PR data or None if not available
    """
    raw = getattr(pr, "raw_data", None)
    if raw is None:
        raw = getattr(pr, "_rawData", None)
    return raw

def extract_pr_evaluation_data(pr):
    """Extract PR data relevant for work evaluation.

    Returns simple, JSON-serializable values (strings, numbers, lists).
    """
    # prefer the repository object from head (fork) or fall back to base
    repo_obj = getattr(pr, "head", None)
    repo = None
    if repo_obj:
        repo = getattr(repo_obj, "repo", None)
    if repo is None:
        repo = getattr(pr, "base", None)
        if repo:
            repo = getattr(repo, "repo", None)

    commit_messages = get_commit_messages(pr)

    # Collect simple serializable representations of comments
    try:
        comments = [c.body for c in pr.get_comments()]
    except Exception:
        comments = []

    try:
        review_comments = [c.body for c in pr.get_review_comments()]
    except Exception:
        review_comments = []

    try:
        files = [
            {
                "path": f.filename,
                "additions": f.additions,
                "deletions": f.deletions,
            }
            for f in pr.get_files()
        ]
    except Exception:
        files = []

    return {
        "pr_number": pr.number,
        "title": pr.title,
        "description": pr.body,
        "commit_messages": commit_messages,
        "repo_description": repo.description if repo is not None else None,
        "additions": pr.additions,
        "changed_files": pr.changed_files,
        "repo_language": repo.language if repo is not None else None,
        "labels": [label.name for label in pr.get_labels()],
        "merged_at": str(pr.merged_at),
        "comments": comments,
        "review_comments": review_comments,
        "files": files,
    }


def main():
    if len(sys.argv) not in (2, 3):
        logger.error("Usage: python fetch.py owner/repo [output_filename]")
        sys.exit(1)

    repo_full_name = sys.argv[1].strip()
    output_filename = sys.argv[2].strip() if len(sys.argv) == 3 else "pull_requests.json"

    # Validate repository format
    validate_repo_name(repo_full_name)

    token = load_github_token()

    logger.info("Fetching merged PRs for %s...", repo_full_name)
    merged_prs = fetch_merged_pull_requests(repo_full_name, token)
    logger.info("Found %d merged PR(s).", len(merged_prs))

    if not merged_prs:
        logger.info("No merged PRs found. Exiting.")
        return

    pr_data = []
    logger.info("Processing %d merged PR(s)...", len(merged_prs))

    for pr in merged_prs:
        pr_info = extract_pr_evaluation_data(pr)
        pr_data.append(pr_info)
        logger.debug("Processed PR #%d: %s", pr.number, pr.title)

    output_path = Path(__file__).resolve().parent.parent / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out_file:
        json.dump(pr_data, out_file, indent=2, default=str)

    logger.info("Saved %d PR entries to %s", len(pr_data), output_path)


if __name__ == "__main__":
    main()
