import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler()


def _run_all_syncs():
    from sqlalchemy.orm import joinedload

    from app.auth import decrypt_token
    from app.database import SessionLocal
    from app.models import Repository
    from app.models.sync_log import SyncLog
    from app.sync import run_sync

    db = SessionLocal()
    try:
        repos = db.query(Repository).options(joinedload(Repository.user)).all()
        logger.info("Auto-sync starting for %d repo(s)", len(repos))
        for repo in repos:
            log: SyncLog
            try:
                access_token = decrypt_token(repo.user.access_token)
                result = run_sync(repo, access_token, db)
                log = SyncLog(
                    repo_id=repo.id,
                    synced_at=datetime.now(UTC),
                    prs_synced=result["synced"],
                    prs_skipped=result["skipped"],
                    status="success",
                )
                logger.info(
                    "Auto-sync %s: %d synced, %d skipped",
                    repo.full_name,
                    result["synced"],
                    result["skipped"],
                )
            except Exception as exc:
                db.rollback()
                logger.error("Auto-sync failed for %s: %s", repo.full_name, exc)
                log = SyncLog(
                    repo_id=repo.id,
                    synced_at=datetime.now(UTC),
                    prs_synced=0,
                    prs_skipped=0,
                    status="error",
                    error_message=str(exc)[:500],
                )
            db.add(log)
            db.commit()
    finally:
        db.close()


def start_scheduler():
    _scheduler.add_job(
        _run_all_syncs,
        "interval",
        hours=24,
        id="auto_sync",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started — auto-sync every 24 hours")


def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown()
        logger.info("Scheduler stopped")
