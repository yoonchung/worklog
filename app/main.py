import logging

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature

load_dotenv()

from app.auth import decode_session_cookie, router as auth_router
from app.export import router as export_router
from app.repos import router as repos_router
from app.summaries import router as summaries_router

logger = logging.getLogger(__name__)

app = FastAPI(title="WorkLog")
templates = Jinja2Templates(directory="templates")

app.include_router(auth_router)
app.include_router(repos_router)
app.include_router(summaries_router)
app.include_router(export_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    return RedirectResponse(url="/auth/github")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    session = request.cookies.get("session")
    if session:
        try:
            decode_session_cookie(session)
            return RedirectResponse(url="/repos")
        except BadSignature:
            pass
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
