"""话术库与规则通知的向量索引维护。

话术（scripts）和规则通知（rules）与文件、FAQ 一样进入 Milvus，但按板块写入**独立集合**，
实现板块级隔离：

  - 话术 -> ``scenario.script_collection``，检索时按 ``owner_id`` 过滤（沿用话术库的隔离语义）
  - 规则 -> ``scenario.rule_collection``，全员可读（沿用规则通知不隔离的既有语义）

切分策略见 :func:`qa_core.indexing.chunking.split_content_item`：条目默认整条一个 chunk，
仅超长条目才递归切分。本模块同时保留清理早期历史遗留 chunk 的能力。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.documents import Document

from qa_core.config.logging_config import get_logger
from qa_core.config.settings import get_settings
from qa_core.governance.data_scope import resolve_data_scope
from qa_core.indexing.chunking import split_content_item
from qa_core.retrieval.factory import collection_has_data, get_doc_store
from qa_core.scenarios.registry import resolve_scenario
from qa_core.utils import stable_hash

from ..db import SessionLocal
from ..models import RuleItem, ScriptItem

logger = get_logger(__name__)

ContentKind = Literal["scripts", "rules"]

_MODEL_FOR_KIND = {"scripts": ScriptItem, "rules": RuleItem}
_CONTENT_TYPE_FOR_KIND = {"scripts": "script", "rules": "rule"}


def _collection_for(kind: ContentKind, scenario: Any) -> str:
    """返回话术/规则对应的独立集合名；未配置时返回空串。"""
    return scenario.script_collection if kind == "scripts" else scenario.rule_collection


def _delete_ids(collection: str, ids: list[str]) -> None:
    """从一个集合按主键删除 chunk；集合不存在或为空时静默跳过。"""
    if not collection or not ids:
        return
    try:
        if not collection_has_data(collection):
            return
        get_doc_store(collection).delete_ids(ids)
    except Exception:  # noqa: BLE001
        logger.exception("删除话术/规则向量片段失败：collection=%s count=%s", collection, len(ids))


def _build_documents(
    kind: ContentKind,
    snapshot: dict[str, Any],
    scenario: Any,
) -> tuple[list[Document], list[str]]:
    """把一条话术/规则快照转换成待写入的 chunk 与主键列表。"""
    title = str(snapshot.get("title") or "").strip()
    content = str(snapshot.get("content") or "").strip()
    # title 参与向量检索（标题通常是最强的语义词），tags/group_name 只作 metadata。
    text = "\n".join(part for part in (title, content) if part).strip()
    parts = split_content_item(text)
    if not parts:
        return [], []

    settings = get_settings()
    scope = resolve_data_scope(visibility="internal", user_roles=["admin", "user"])
    domain = scope.metadata(allowed_roles=["admin", "user"])
    # 板块级数据集隔离：dataset_id 直接使用板块标识，检索时用同值过滤。
    domain["dataset_id"] = kind

    documents: list[Document] = []
    ids: list[str] = []
    total = len(parts)
    for index, part in enumerate(parts):
        chunk_id = stable_hash(
            scenario.scenario_id,
            kind,
            snapshot.get("id"),
            settings.content_chunk_schema_version,
            snapshot.get("updated_at") or "",
            index,
            part,
        )
        metadata = {
            "content_item_id": snapshot.get("id"),
            "chunk_id": chunk_id,
            "chunk_index": index,
            "chunk_count": total,
            "content_type": _CONTENT_TYPE_FOR_KIND[kind],
            "source": kind,
            # file_name 决定引用标签展示的名称，这里用条目标题。
            "file_name": title or str(snapshot.get("id") or ""),
            "title": title,
            "group_name": str(snapshot.get("group_name") or ""),
            "tags": str(snapshot.get("tags") or ""),
            "owner_id": str(snapshot.get("owner_id") or ""),
            "status": "published",
            "scenario_id": scenario.scenario_id,
            **domain,
        }
        documents.append(Document(page_content=part, metadata=metadata))
        ids.append(chunk_id)
    return documents, ids


def index_content_item(kind: ContentKind, item_id: str) -> None:
    """为一条话术/规则建立或刷新向量索引（幂等 upsert）。

    条目被逻辑删除或不存在时，会清掉它已有的 chunk。

    参数：
        kind: ``scripts`` 或 ``rules``。
        item_id: 业务表主键。
    """
    model = _MODEL_FOR_KIND[kind]
    scenario = resolve_scenario(get_settings().active_scenario_id)
    collection = _collection_for(kind, scenario)
    if not collection:
        logger.warning("场景 %s 未配置 %s 集合，跳过往量索引", scenario.scenario_id, kind)
        return

    with SessionLocal() as db:
        item = db.get(model, item_id)
        if item is None or item.deleted_at is not None:
            # 条目已不存在：清掉历史 chunk 后返回。
            old_ids = json.loads(getattr(item, "index_chunk_ids", "[]") or "[]") if item else []
            _delete_ids(collection, old_ids)
            return
        snapshot = {
            "id": item.id,
            "title": item.title,
            "content": item.content,
            "group_name": item.group_name,
            "tags": item.tags,
            "owner_id": item.owner_id,
            "updated_at": item.updated_at.isoformat() if item.updated_at else "",
            "old_ids": json.loads(item.index_chunk_ids or "[]"),
        }

    documents, ids = _build_documents(kind, snapshot, scenario)
    store = get_doc_store(collection)
    if documents:
        store.add_documents(documents, ids=ids)
    stale_ids = [chunk_id for chunk_id in snapshot["old_ids"] if chunk_id not in set(ids)]
    if stale_ids:
        # 同时清理本板块集合与早期版本写入的 doc collection，避免旧内容残留可检索。
        remove_business_content(kind, json.dumps(stale_ids))

    with SessionLocal() as db:
        item = db.get(model, item_id)
        if item is None or item.deleted_at is not None:
            # 写入过程中条目被删除：回滚本次写入，避免残留可检索内容。
            if ids:
                _delete_ids(collection, ids)
            return
        item.index_chunk_ids = json.dumps(ids)
        db.commit()


def delete_content_item(kind: ContentKind, item_id: str) -> None:
    """删除一条话术/规则的向量索引，并清空业务表里的 chunk 记录。"""
    model = _MODEL_FOR_KIND[kind]
    with SessionLocal() as db:
        item = db.get(model, item_id)
        if item is None:
            return
        ids = json.loads(item.index_chunk_ids or "[]")
        item.index_chunk_ids = "[]"
        db.commit()
    # 同时清本板块集合和早期版本写入的 doc collection。
    remove_business_content(kind, json.dumps(ids))


def remove_business_content(kind: str, chunk_ids_json: str) -> None:
    """按 chunk id 删除话术/规则的向量片段（兼容既有调用点）。

    同时清理两类集合：当前板块集合，以及早期版本曾写入的 doc collection，
    避免旧内容仍能被检索到。

    参数：
        kind: 话术/规则类别标识（``scripts`` / ``rules``）。
        chunk_ids_json: 记录在业务表 index_chunk_ids 字段里的 JSON 数组字符串。
    """
    ids = json.loads(chunk_ids_json or "[]")
    if not ids:
        return
    scenario = resolve_scenario(get_settings().active_scenario_id)
    _delete_ids(_collection_for(kind, scenario), ids)
    # 早期版本把话术/规则写进 doc collection，这里顺手清理历史残留。
    _delete_ids(scenario.doc_collection, ids)
