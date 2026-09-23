"""话术库与规则通知的向量索引清理。

注意：话术（scripts）和规则通知（rules）**不再写入向量库**。
它们只在业务库里维护、供前端浏览，不参与 RAG 检索。
本模块因此只保留「清理历史遗留 chunk」的能力。
"""

from __future__ import annotations

import json


def remove_business_content(kind: str, chunk_ids_json: str) -> None:
    """按 chunk id 删除历史遗留在向量库中的话术/规则片段。

    早期版本会把话术和规则通知索引进 doc collection，导致它们出现在 RAG 召回结果里。
    现在改为不入库，但已经写进去的 chunk 仍需清理，所以保留这个函数。

    参数：
        kind: 话术/规则类别；当前不再影响删除逻辑，保留形参以兼容既有调用点。
        chunk_ids_json: 记录在业务表 index_chunk_ids 字段里的 JSON 数组字符串。
    """
    ids = json.loads(chunk_ids_json or "[]")
    if not ids:
        return
    from qa_core.retrieval.factory import get_doc_store
    from qa_core.scenarios.registry import resolve_scenario

    from ..config import get_settings

    scenario = resolve_scenario(get_settings().active_scenario_id)
    get_doc_store(scenario.doc_collection).delete_ids(ids)
