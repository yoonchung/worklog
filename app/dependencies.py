from fastapi import Cookie, Depends, HTTPException
from itsdangerous import BadSignature
from sqlalchemy.orm import Session

from app.auth import decode_session_cookie
from app.database import get_db
from app.models import User


def get_current_user(
    session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_session_cookie(session)
    except BadSignature:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    user = db.get(User, payload["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
