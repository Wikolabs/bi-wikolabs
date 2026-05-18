"""APScheduler — nightly schema refresh cron job."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def start() -> None:
    from schema_registry import refresh_all

    scheduler = get_scheduler()

    # Full schema rescan every night at 02:00 UTC
    scheduler.add_job(
        refresh_all,
        CronTrigger(hour=2, minute=0),
        id="nightly_schema_refresh",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("Scheduler started — nightly schema refresh at 02:00 UTC")


def stop() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
