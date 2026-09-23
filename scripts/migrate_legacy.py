"""Idempotently migrate the legacy SQLite application into EduRAG.

Example:
  python scripts/migrate_legacy.py --sqlite ../教辅知识库前端开发/server/data.db \
      --uploads ../教辅知识库前端开发/server/uploads
"""
from __future__ import annotations

import argparse
import hashlib
import mimetypes
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from sys import path as sys_path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys_path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select

from backend.app.db import Base, SessionLocal, engine
from backend.app.models import (
    AuditLog, Document, DocumentVersion, IngestionJob, JobStatus, RuleItem, ScriptItem, Section, User,
)
from backend.app.object_store import object_store
from backend.app.routers.documents import normalized_name, safe_filename


def parse_legacy_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = value.replace("/", "-").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def rows(conn: sqlite3.Connection, sql: str):
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []


def migrate(sqlite_path: Path, uploads: Path, *, dry_run: bool = False) -> dict[str, int]:
    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    Base.metadata.create_all(engine)
    counters = {key: 0 for key in ("users", "sections", "documents", "scripts", "rules", "logs", "missing_files")}
    with SessionLocal() as db:
        user_ids: dict[str, int] = {}
        for row in rows(source, "SELECT * FROM users ORDER BY id"):
            user = db.scalar(select(User).where(func.lower(User.username) == row["username"].lower()))
            if not user:
                user = User(
                    username=row["username"], password_hash=row["password"], role=row["role"],
                    created_at=parse_legacy_time(row["created_at"]),
                )
                db.add(user); db.flush(); counters["users"] += 1
            user_ids[row["username"]] = user.id

        fallback_user = next(iter(user_ids.values()), None)
        if fallback_user is None:
            raise RuntimeError("旧数据库没有用户，无法确定内容所有者")

        for row in rows(source, "SELECT * FROM sections ORDER BY sort_order"):
            if not db.get(Section, row["id"]):
                db.add(Section(id=row["id"], name=row["name"], sort_order=row["sort_order"], created_at=parse_legacy_time(row["created_at"])))
                counters["sections"] += 1
        db.flush()

        for row in rows(source, "SELECT * FROM files ORDER BY created_at"):
            if db.get(Document, row["id"]):
                continue
            source_file = uploads / (row["file_path"] or "")
            if not source_file.is_file():
                counters["missing_files"] += 1
                continue
            content = source_file.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            owner_id = user_ids.get(row["owner"], fallback_user)
            document = Document(
                id=row["id"], normalized_name=normalized_name(row["name"]),
                section_id=row["section_id"], owner_id=owner_id,
                created_at=parse_legacy_time(row["created_at"]),
            )
            db.add(document); db.flush()
            version_id = f"ver_migrated_{row['id']}"
            object_key = f"originals/{document.id}/{version_id}/{safe_filename(row['name'])}"
            object_store.put(object_key, content, mimetypes.guess_type(row["name"])[0] or "application/octet-stream")
            version = DocumentVersion(
                id=version_id, document_id=document.id, version_no=1, file_name=row["name"],
                file_type=Path(row["name"]).suffix.lower().lstrip(".") or row["type"],
                size_bytes=len(content), checksum_sha256=checksum, object_key=object_key,
                tags=row["tags"] or "", uploader_id=owner_id,
                uploaded_at=parse_legacy_time(row["created_at"]),
                ingestion_status=JobStatus.queued.value,
            )
            db.add(version)
            db.flush()
            db.add(IngestionJob(document_version_id=version_id, status=JobStatus.queued.value))
            document.current_version_id = None
            counters["documents"] += 1

        script_groups = {row["id"]: row["name"] for row in rows(source, "SELECT id, name FROM script_groups")}
        for row in rows(source, "SELECT * FROM scripts"):
            if not db.get(ScriptItem, row["id"]):
                db.add(ScriptItem(
                    id=row["id"], title=row["title"], content=row["content"] or "",
                    group_name=script_groups.get(row["group_id"], "默认分组"), tags=row["tags"] or "",
                    owner_id=user_ids.get(row["owner"], fallback_user),
                    created_at=parse_legacy_time(row["created_at"]), updated_at=parse_legacy_time(row["created_at"]),
                )); counters["scripts"] += 1

        rule_groups = {row["id"]: row["name"] for row in rows(source, "SELECT id, name FROM rule_groups")}
        for row in rows(source, "SELECT * FROM rules"):
            if not db.get(RuleItem, row["id"]):
                db.add(RuleItem(
                    id=row["id"], title=row["title"], content=row["content"] or "",
                    group_name=rule_groups.get(row["group_id"], "通知规则"), tags=row["tags"] or "",
                    owner_id=user_ids.get(row["owner"], fallback_user),
                    created_at=parse_legacy_time(row["created_at"]), updated_at=parse_legacy_time(row["created_at"]),
                )); counters["rules"] += 1

        existing_legacy_logs = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.detail == "legacy-migration")) or 0
        if not existing_legacy_logs:
            for row in rows(source, "SELECT * FROM logs ORDER BY id"):
                db.add(AuditLog(
                    action=row["action"], target_type=row["target_type"], target_name=row["target_name"],
                    detail="legacy-migration", operator_id=user_ids.get(row["operator"], fallback_user),
                    created_at=parse_legacy_time(row["created_at"]),
                )); counters["logs"] += 1
        if dry_run:
            db.rollback()
        else:
            db.commit()
    source.close()
    return counters


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--uploads", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(migrate(args.sqlite.resolve(), args.uploads.resolve(), dry_run=args.dry_run))
