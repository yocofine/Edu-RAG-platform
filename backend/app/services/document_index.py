from __future__ import annotations

import json
import re
from pathlib import Path
from collections.abc import Callable

from langchain_core.documents import Document

from ..config import get_settings


PAGE_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*第\s*(\d+)\s*页\s*$")


def split_page_marked_documents(documents: list[Document]) -> list[Document]:
    """把解析稿中的“第 N 页”区段恢复成独立 Document，并写入零基 page_index。"""
    result: list[Document] = []
    for document in documents:
        content = str(document.page_content or "")
        matches = list(PAGE_HEADING_RE.finditer(content))
        if not matches:
            result.append(document)
            continue
        prefix = content[:matches[0].start()].strip()
        for index, match in enumerate(matches):
            page_number = int(match.group(1))
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            page_content = content[match.start():end].strip()
            if index == 0 and prefix:
                page_content = f"{prefix}\n\n{page_content}"
            if not page_content:
                continue
            metadata = dict(document.metadata or {})
            metadata.update(
                {
                    "page": page_number - 1,
                    "page_index": page_number - 1,
                    "page_number": page_number,
                }
            )
            result.append(Document(page_content=page_content, metadata=metadata))
    return result


def index_document(
    path: Path,
    *,
    version_id: str,
    metadata_overrides: dict | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    batch_size: int = 32,
) -> list[str]:
    from qa_core.governance.data_scope import resolve_data_scope
    from qa_core.governance.kb_versions import get_kb_version_store
    from qa_core.indexing.chunking import split_documents
    from qa_core.indexing.document_loaders import load_file
    from qa_core.indexing.document_normalizer import normalize_documents
    from qa_core.retrieval.factory import get_doc_store
    from qa_core.scenarios.registry import resolve_scenario

    scenario = resolve_scenario(get_settings().active_scenario_id)
    versions = get_kb_version_store(scenario.scenario_id)
    kb_version = versions.ensure_version(
        None, create_new=False, description="在线文件增量入库", created_by="edu-rag-api"
    )
    scope = resolve_data_scope(visibility="internal", user_roles=["admin", "user"])
    raw_documents = split_page_marked_documents(load_file(path))
    documents = normalize_documents(
        raw_documents, path, "documents", kb_version.kb_version,
        scenario.scenario_id, scope, ["admin", "user"],
    )
    for document in documents:
        document.metadata["doc_id"] = f"file:{version_id}"
        document.metadata["document_version_id"] = version_id
        # 在线入库实际索引的是临时的 `ver_xxx.md`。必须用数据库中的原始文件信息
        # 覆盖临时路径元数据，否则检索引用会显示内部版本文件名，且无法按业务文档追溯。
        document.metadata.update(metadata_overrides or {})
    chunks, ids = split_documents(documents)
    if chunks:
        store = get_doc_store(scenario.doc_collection)
        inserted_ids: list[str] = []
        try:
            total = len(chunks)
            safe_batch_size = max(1, int(batch_size))
            for start in range(0, total, safe_batch_size):
                end = min(start + safe_batch_size, total)
                batch_ids = ids[start:end]
                store.add_documents(chunks[start:end], ids=batch_ids)
                inserted_ids.extend(batch_ids)
                if progress_callback is not None:
                    progress_callback(end, total)
        except Exception:
            if inserted_ids:
                store.delete_ids(inserted_ids)
            raise
    try:
        versions.activate_version(kb_version.kb_version)
    except Exception:
        pass
    return ids


def remove_document_chunks(chunk_ids_json: str) -> None:
    ids = json.loads(chunk_ids_json or "[]")
    if not ids:
        return
    from qa_core.retrieval.factory import get_doc_store
    from qa_core.scenarios.registry import resolve_scenario
    scenario = resolve_scenario(get_settings().active_scenario_id)
    get_doc_store(scenario.doc_collection).delete_ids(ids)
