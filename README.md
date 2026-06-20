# WorkLog — GitHub Activity Summarizer

Fetches merged pull requests from a GitHub repository, summarizes each one into resume-ready bullets using Claude (or a local LLM), and persists the results to PostgreSQL.

## Setup

**1. Python environment**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**2. Environment variables** — create a `.env` file:
```
# CLI scripts (fetch + summarize without the web app)
GITHUB_TOKEN=your_github_pat
ANTHROPIC_API_KEY=your_anthropic_key

# Web app — GitHub OAuth
GITHUB_CLIENT_ID=your_oauth_app_client_id
GITHUB_CLIENT_SECRET=your_oauth_app_client_secret
GITHUB_REDIRECT_URI=http://localhost:8000/auth/callback

# Web app — session + token encryption
SESSION_SECRET_KEY=any_long_random_string
FERNET_KEY=<generated — see below>

# Optional: use a local LLM (e.g. LM Studio) instead of Anthropic
LOCAL_BASE_URL=http://localhost:1234
LOCAL_API_KEY=your_local_key
```

To generate a `FERNET_KEY`:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The web app requires a GitHub OAuth app. Create one at [github.com/settings/developers](https://github.com/settings/developers) with callback URL `http://localhost:8000/auth/callback`.

**3. PostgreSQL**
```bash
brew install postgresql@18
brew services start postgresql@18
createdb worklog_db
export DATABASE_URL="postgresql://$(whoami)@localhost:5432/worklog_db"
python3 scripts/db_create_tables.py
```

## Web app

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000), login with GitHub, connect a repository, and click **Sync** to fetch and summarize its merged PRs. View, flag, and annotate summaries at `/summaries`.

## CLI usage

**Full pipeline** — fetch, summarize, and save to database:
```bash
python3 scripts/run_pipeline.py owner/repo
```

**Fetch only** — writes `pull_requests.json`:
```bash
python3 scripts/fetch.py owner/repo
```

**Summarize only** — reads `pull_requests.json` by default, writes `summaries.json`:
```bash
python3 scripts/summarize.py [input.json] [output.json]
```

**Two-step without database:**
```bash
python3 scripts/fetch.py owner/repo && python3 scripts/summarize.py
```

## Output

Each summary includes: `summary`, `resume_bullet`, `is_resume_worthy`, `technical_points`, `notes`, and `confidence_score`.

The pipeline writes two JSON files alongside the database records: `db_pull_requests.json` and `db_summaries.json`.
