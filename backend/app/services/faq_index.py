from __future__ import annotations

import json

from langchain_core.documents import Document

from qa_core.governance.data_scope import resolve_data_scope
from qa_core.governance.kb_versions import resolve_active_kb_version, version_metadata
from qa_core.retrieval.factory import get_faq_store
from qa_core.scenarios.registry import resolve_scenario
from qa_core.utils import stable_hash

from ..config import get_settings
from ..db import SessionLocal
from ..models import FAQItem


def index_faq_item(item_id: str) -> None:
    with SessionLocal() as db:
        item = db.get(FAQItem, item_id)
        if item is None or item.deleted_at is not None:
            return
        question = item.question.strip()
        answer = item.answer.strip()
        category = item.category.strip() or "通用"
        tags = item.tags.strip()
        old_ids = json.loads(item.index_chunk_ids or "[]")

    scenario = resolve_scenario(get_settings().active_scenario_id)
    kb_version = resolve_active_kb_version(None, scenario.scenario_id)
    scope = resolve_data_scope(visibility="internal", user_roles=["admin", "user"])
    chunk_id = stable_hash(kb_version, scenario.scenario_id, item_id, question, answer, category, tags)
    document = Document(
        page_content=question,
        metadata={
            "faq_id": item_id,
            "scenario_id": scenario.scenario_id,
            "standard_question": question,
            "answer": answer,
            "source": "faq",
            "subject_name": category,
            "tags": tags,
            "status": "published",
            "content_type": "faq",
            **scope.metadata(allowed_roles=["admin", "user"]),
            **version_metadata(kb_version, scenario.scenario_id),
        },
    )
    store = get_faq_store(scenario.faq_collection)
    store.add_documents([document], ids=[chunk_id])
    stale_ids = [value for value in old_ids if value != chunk_id]
    if stale_ids:
        store.delete_ids(stale_ids)

    with SessionLocal() as db:
        item = db.get(FAQItem, item_id)
        if item is None or item.deleted_at is not None:
            store.delete_ids([chunk_id])
            return
        item.index_chunk_ids = json.dumps([chunk_id])
        db.commit()


def remove_faq_item(chunk_ids_json: str) -> None:
    ids = json.loads(chunk_ids_json or "[]")
    if not ids:
        return
    scenario = resolve_scenario(get_settings().active_scenario_id)
    get_faq_store(scenario.faq_collection).delete_ids(ids)
