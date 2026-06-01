import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from github import Github


def load_github_token() -> str:
    load_dotenv()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN not found in .env. Add a line like: GITHUB_TOKEN=your_token_here"
        )
    return token


def fetch_merged_pull_requests(repo_full_name: str, token: str):
    gh = Github(token)
    repo = gh.get_repo(repo_full_name)
    pulls = repo.get_pulls(state="closed", sort="updated", direction="desc")

    merged_prs = []
    for pr in pulls:
        if pr.is_merged():
            merged_prs.append(pr)
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
    gh = Github(token)
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


def get_commit_messages(pr):
    return [commit.commit.message for commit in pr.get_commits()]


def format_commit_data(commit):
    """Format a single commit object to a dictionary."""
    return {
        "sha": commit.sha,
        "message": commit.commit.message,
        "author": commit.commit.author.name if commit.commit.author else None,
        "author_email": commit.commit.author.email if commit.commit.author else None,
        "date": str(commit.commit.author.date) if commit.commit.author else None,
    }


def raw_pr_data(pr):
    raw = getattr(pr, "raw_data", None)
    if raw is None:
        raw = getattr(pr, "_rawData", None)
    return raw


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python fetch.py owner/repo [output_filename]")
        sys.exit(1)

    repo_full_name = sys.argv[1].strip()
    output_filename = sys.argv[2].strip() if len(sys.argv) == 3 else "data.json"
    token = load_github_token()

    # print(f"Fetching merged PRs for {repo_full_name}...\n")
    merged_prs = fetch_merged_pull_requests(repo_full_name, token)
    # print(f"Found {len(merged_prs)} merged PR(s).\n")

    pr_data = []
    for pr in merged_prs:
        raw = raw_pr_data(pr)
        if raw is None:
            raw = {
                "number": pr.number,
                "title": pr.title,
                "body": pr.body,
                "user": pr.user.login if pr.user else None,
                "merged_at": str(pr.merged_at),
                "state": pr.state,
            }
        pr_data.append(raw)

    output_path = Path(__file__).resolve().parent.parent / output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out_file:
        json.dump(pr_data, out_file, indent=2, default=str)

    print(f"Saved {len(pr_data)} PR entries to {output_path}")

    # Instead of merged_prs, fetch commits
    commits = fetch_commits(repo_full_name, token, max_commits=50)

    for commit in commits:
        print(json.dumps(format_commit_data(commit), indent=2))


if __name__ == "__main__":
    main()
