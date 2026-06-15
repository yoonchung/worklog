import logging

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

load_dotenv()

from app.auth import router as auth_router
from app.repos import router as repos_router

logger = logging.getLogger(__name__)

app = FastAPI(title="WorkLog")
templates = Jinja2Templates(directory="templates")

app.include_router(auth_router)
app.include_router(repos_router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    return RedirectResponse(url="/auth/github")


@app.get("/")
async def root():
    return {"message": "WorkLog is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
