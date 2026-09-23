"""问题检索范围识别与同文档切片排序。

第一阶段语义检索负责定位文档；本模块决定第二、三阶段应当只补相邻切片、
展开完整流程，还是读取整篇文档供分段总结。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from langchain_core.documents import Document

from qa_core.retrieval.results import RetrievalHit

RetrievalScope = Literal["specific", "complete_process", "full_summary"]

_FULL_SUMMARY_RE = re.compile(
    r"(全文|整篇|整份|整个文档|整本|全篇).{0,8}(总结|概括|归纳|梳理|主要内容)|"
    r"(总结|概括|归纳|梳理).{0,8}(全文|整篇|整份|整个文档|整本|全篇)"
)
_PROCESS_NOUN_RE = re.compile(r"(流程|步骤|阶段|环节|操作顺序|办理顺序)")
_COMPLETE_PROCESS_RE = re.compile(
    r"(完整|全部|所有|全套|全程|整个|整体|具体|详细).{0,8}(流程|步骤|阶段|环节)|"
    r"(流程|步骤|阶段|环节).{0,8}(是什么|有哪些|包括什么|怎么走|如何进行|完整|全部|所有|全套|全程)"
)
_STEP_RE = re.compile(r"(?:step\s*|步骤\s*|第\s*)(\d{1,3})(?:\s*步)?", re.IGNORECASE)


def infer_retrieval_scope(query: str) -> RetrievalScope:
    """把问题分为具体问答、完整流程和全文总结三类。"""
    normalized = re.sub(r"\s+", "", str(query or "").lower())
    if _FULL_SUMMARY_RE.search(normalized):
        return "full_summary"
    if _COMPLETE_PROCESS_RE.search(normalized):
        return "complete_process"
    # “包课辅导流程”“报销步骤”这类名词式短查询，本身就在询问完整过程。
    if len(normalized) <= 24 and _PROCESS_NOUN_RE.search(normalized):
        return "complete_process"
    return "specific"


def target_document_metadata(hits: list[RetrievalHit]) -> dict[str, str]:
    """按命中分数聚合，选择最可信的文档版本并返回可追踪元数据。"""
    grouped_scores: dict[str, float] = defaultdict(float)
    grouped_best: dict[str, tuple[float, dict]] = {}
    for rank, hit in enumerate(hits):
        metadata = dict(hit.document.metadata or {})
        identity = str(metadata.get("document_version_id") or metadata.get("doc_id") or "").strip()
        if not identity:
            continue
        # 多个切片共同命中比偶然的单个高分更可靠；排名衰减避免尾部噪声支配结果。
        contribution = max(float(hit.score), 0.0) / (1.0 + rank * 0.15)
        grouped_scores[identity] += contribution
        previous = grouped_best.get(identity)
        if previous is None or hit.score > previous[0]:
            grouped_best[identity] = (float(hit.score), metadata)
    if not grouped_scores:
        return {}
    identity = max(grouped_scores, key=grouped_scores.get)
    metadata = grouped_best[identity][1]
    return {
        key: str(metadata.get(key) or "").strip()
        for key in ("document_id", "document_version_id", "doc_id", "section_id", "file_name")
        if str(metadata.get(key) or "").strip()
    }


def document_order_key(document: Document) -> tuple[int, int, int, str]:
    """生成同一文档内的稳定顺序；兼容尚未回填 chunk_order 的旧切片。"""
    metadata = document.metadata or {}
    explicit = metadata.get("chunk_order")
    if explicit is not None:
        try:
            return (0, int(explicit), int(metadata.get("child_index") or 0), "")
        except (TypeError, ValueError):
            pass
    content = str(metadata.get("parent_content") or document.page_content or "")
    step = _STEP_RE.search(content)
    if step:
        return (1, int(step.group(1)), 0, str(metadata.get("parent_id") or ""))
    for key in ("page_number", "page", "page_index"):
        if metadata.get(key) is not None:
            try:
                return (2, int(metadata[key]), 0, str(metadata.get("parent_id") or ""))
            except (TypeError, ValueError):
                continue
    return (3, 0, 0, str(metadata.get("parent_id") or metadata.get("chunk_id") or ""))


def deduplicate_parent_documents(documents: list[Document]) -> list[Document]:
    """把同一父块的多个子块合并为一个有序证据单元。"""
    ordered = sorted(documents, key=document_order_key)
    result: list[Document] = []
    seen: set[str] = set()
    for document in ordered:
        metadata = dict(document.metadata or {})
        content = str(metadata.get("parent_content") or document.page_content or "").strip()
        key = str(metadata.get("parent_id") or content)
        if not content or key in seen:
            continue
        seen.add(key)
        result.append(Document(page_content=content, metadata=metadata))
    return result


def select_complete_process_documents(documents: list[Document], *, fallback_limit: int = 24) -> list[Document]:
    """提取连续 Step/步骤编号覆盖的整段父块，保留步骤之间的续写内容。"""
    parents = deduplicate_parent_documents(documents)
    numbered: list[tuple[int, int]] = []
    for index, document in enumerate(parents):
        content = str(document.page_content or "")
        matches = [int(value) for value in _STEP_RE.findall(content)]
        if matches:
            numbered.append((index, min(matches)))
    if not numbered:
        return parents[:fallback_limit]
    # 一个文档可能有多套流程。按父块位置分簇，选择包含编号最多、跨度最长的一簇。
    clusters: list[list[tuple[int, int]]] = []
    for item in numbered:
        if not clusters or item[0] - clusters[-1][-1][0] > 4:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    best = max(clusters, key=lambda cluster: (len({step for _, step in cluster}), cluster[-1][0] - cluster[0][0]))
    start = best[0][0]
    # 多带一个父块，覆盖最后一步在下一父块中的续写；仍受 RetrievalPlan 总预算限制。
    end = min(len(parents), best[-1][0] + 2)
    return parents[start:end]


def select_neighbor_documents(
    all_documents: list[Document],
    anchor_documents: list[Document],
    *,
    window: int,
) -> list[Document]:
    """按锚点相关性优先补齐相邻父块，避免文档前部内容挤掉真正命中的证据。"""
    parents = deduplicate_parent_documents(all_documents)
    if not parents:
        return []
    index_by_key = {
        str((doc.metadata or {}).get("parent_id") or doc.page_content): index
        for index, doc in enumerate(parents)
    }
    selected_indexes: set[int] = set()
    ordered_indexes: list[int] = []
    for anchor in anchor_documents:
        key = str((anchor.metadata or {}).get("parent_id") or (anchor.metadata or {}).get("parent_content") or anchor.page_content)
        index = index_by_key.get(key)
        if index is None:
            continue
        # 先放语义命中的父块，再按距离交替补前后邻居。多个锚点保持重排后的
        # 相关性顺序；同一父块只出现一次。最终上下文截断时不会先吃掉低相关前置块。
        offsets = [0]
        for distance in range(1, max(window, 0) + 1):
            offsets.extend((-distance, distance))
        for offset in offsets:
            candidate = index + offset
            if candidate < 0 or candidate >= len(parents) or candidate in selected_indexes:
                continue
            selected_indexes.add(candidate)
            ordered_indexes.append(candidate)
    return [parents[index] for index in ordered_indexes]
