"""Process queued/interrupted ingestion jobs sequentially outside an HTTP request."""
from __future__ import annotations

import asyncio
from pathlib import Path
from sys import path as sys_path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys_path.insert(0, str(ROOT))

from backend.app.db import SessionLocal
from backend.app.models import IngestionJob, JobStatus
from backend.app.services.ingestion import process_ingestion


async def main() -> None:
    with SessionLocal() as db:
        job_ids = db.scalars(
            select(IngestionJob.id).where(
                IngestionJob.status.in_([JobStatus.queued.value, JobStatus.interrupted.value])
            ).order_by(IngestionJob.created_at)
        ).all()
    for job_id in job_ids:
        await process_ingestion(job_id)


if __name__ == "__main__":
    asyncio.run(main())

