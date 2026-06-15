import os
import secrets

import httpx
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

load_dotenv()

router = APIRouter(prefix="/auth")

GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get(
    "GITHUB_REDIRECT_URI", "http://localhost:8000/auth/callback"
)
FERNET_KEY = os.environ.get("FERNET_KEY", "")
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "")

SESSION_COOKIE = "session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _fernet() -> Fernet:
    if not FERNET_KEY:
        raise RuntimeError("FERNET_KEY not set in environment")
    return Fernet(FERNET_KEY.encode())


def _signer() -> URLSafeTimedSerializer:
    if not SESSION_SECRET_KEY:
        raise RuntimeError("SESSION_SECRET_KEY not set in environment")
    return URLSafeTimedSerializer(SESSION_SECRET_KEY)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


def create_session_cookie(user_id: int) -> str:
    return _signer().dumps({"user_id": user_id})


def decode_session_cookie(value: str) -> dict:
    return _signer().loads(value, max_age=SESSION_MAX_AGE)


@router.get("/github")
async def github_login():
    """Redirect the user to GitHub's OAuth authorization page."""
    if not GITHUB_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID not configured")

    # Sign a random nonce as the state to prevent CSRF
    state = _signer().dumps(secrets.token_hex(16))
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={GITHUB_REDIRECT_URI}"
        f"&scope=repo"
        f"&state={state}"
    )
    return RedirectResponse(url)


@router.get("/callback")
async def github_callback(code: str, state: str, db: Session = Depends(get_db)):
    """Exchange the GitHub callback code for an access token and create a session."""
    # Validate state to prevent CSRF
    try:
        _signer().loads(state, max_age=300)
    except BadSignature:
        raise HTTPException(status_code=400, detail="Invalid or expired state parameter")

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
            timeout=10.0,
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=400,
            detail=f"GitHub did not return an access token: {token_data.get('error_description', token_data)}",
        )

    # Fetch the authenticated GitHub user
    async with httpx.AsyncClient() as client:
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10.0,
        )

    if user_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch GitHub user info")

    github_user = user_resp.json()
    github_id = github_user["id"]
    github_username = github_user["login"]

    # Encrypt token before storing
    encrypted_token = encrypt_token(access_token)

    # Upsert user record
    user = db.query(User).filter_by(github_id=github_id).first()
    if user:
        user.access_token = encrypted_token
        user.github_username = github_username
    else:
        user = User(
            github_id=github_id,
            github_username=github_username,
            access_token=encrypted_token,
        )
        db.add(user)
    db.commit()
    db.refresh(user)

    # Issue a signed session cookie and redirect to home
    response = RedirectResponse(url="/")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_cookie(user.id),
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return response
