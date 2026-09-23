"""将 MySQL 中的原始文件信息补写到已发布的 Milvus 文档切片。"""

from __future__ import annotations

import json

from sqlalchemy import select

from backend.app.db import SessionLocal
from backend.app.models import Document, DocumentVersion, JobStatus
from qa_core.retrieval.factory import get_doc_store
from qa_core.scenarios.registry import resolve_scenario


def main() -> None:
    scenario = resolve_scenario("education_kb")
    store = get_doc_store(scenario.doc_collection)
    documents = 0
    chunks = 0
    with SessionLocal() as db:
        rows = db.execute(
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Document.deleted_at.is_(None),
                Document.current_version_id == DocumentVersion.id,
                DocumentVersion.ingestion_status == JobStatus.published.value,
            )
        ).all()
        for version, document in rows:
            chunk_ids = json.loads(version.index_chunk_ids or "[]")
            if not chunk_ids:
                continue
            updated = store.update_document_metadata(
                chunk_ids,
                {
                    "document_id": document.id,
                    "document_version_id": version.id,
                    "file_name": version.file_name,
                    "original_file_name": version.file_name,
                    "file_type": f".{version.file_type.lstrip('.')}",
                    "original_file_type": version.file_type,
                    "section_id": document.section_id,
                    "uploader": version.uploader.username,
                },
            )
            # index_chunk_ids 按原始入库顺序保存，可为旧切片恢复稳定顺序；新入库会直接写入这些字段。
            store.update_chunk_metadata(
                {
                    chunk_id: {"chunk_order": index, "legacy_order_backfilled": True}
                    for index, chunk_id in enumerate(chunk_ids)
                }
            )
            if updated:
                documents += 1
                chunks += updated
                print(f"updated {version.file_name}: {updated} chunks")
    print(f"completed: {documents} documents, {chunks} chunks")


if __name__ == "__main__":
    main()
