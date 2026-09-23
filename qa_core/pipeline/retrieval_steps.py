"""RAG 主流程中的检索执行步骤。

`steps.py` 负责意图、改写、Prompt 等准备工作；本文件只负责把准备好的
`RetrievalPreparation` 落到 FAQ 和文档检索上。这样阅读主链路时可以清楚区分：
先决定“怎么查”，再执行“真正去哪里查”。
"""

from __future__ import annotations

import re

from qa_core.governance.data_scope import escape_expr_value
from qa_core.pipeline.context import direct_faq_answer
from qa_core.pipeline.runtime import RAGQueryContext
from qa_core.pipeline.steps import RetrievalPreparation
from qa_core.retrieval.factory import collection_has_data, get_doc_store, get_faq_store
from qa_core.retrieval.results import RetrievalHit, RetrievalResult
from qa_core.retrieval.scope import (
    deduplicate_parent_documents,
    select_complete_process_documents,
    select_neighbor_documents,
    target_document_metadata,
)


PAGE_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s*第\s*(\d+)\s*页\s*$")


def annotate_page_numbers(documents: list) -> list:
    """按 chunk_order 传播解析稿中的“第 N 页”标记，兼容已经入库的旧切片。"""
    current_page: int | None = None
    ordered = sorted(
        documents,
        key=lambda item: int((item.metadata or {}).get("chunk_order") or 0),
    )
    for document in ordered:
        metadata = dict(document.metadata or {})
        content = str(metadata.get("parent_content") or document.page_content or "")
        markers = PAGE_HEADING_RE.findall(content)
        if markers:
            current_page = int(markers[-1])
        elif current_page is None:
            explicit = metadata.get("page_number")
            zero_based = metadata.get("page")
            if explicit not in (None, ""):
                current_page = int(explicit)
            elif zero_based not in (None, ""):
                current_page = int(zero_based) + 1
        if current_page is not None:
            metadata["page_number"] = current_page
            document.metadata = metadata
    return ordered


def search_faq(context: RAGQueryContext, prepared: RetrievalPreparation) -> RetrievalResult:
    """按检索计划执行 FAQ 混合检索，并把耗时、最高分写入检索诊断信息。★★★ 核心

    使用场景：
    - FAQ 标准问答优先召回，用于判断是否可以直接返回标准答案；
    - FAQ 未直出时，FAQ 片段仍可和文档片段一起进入 Prompt，补充标准口径。

    为什么单独封装：FAQ 检索涉及 fast path 结果复用、source 过滤、版本过滤和数据隔离。
    这些属于“执行检索”的细节，不应该挤在 `rag.py` 的流程编排里。
    """

    def do_search() -> RetrievalResult:
        if not prepared.plan.run_faq:
            return RetrievalResult(query=prepared.rewritten_query, source_type="faq")
        # 上面的 return 只在 run_faq=False 时执行；FAQ 集合为空或不存在时同样跳过，
        # 避免空集合检索还要承担集合初始化的高开销
        if not collection_has_data(context.scenario.faq_collection):
            context.retrieval_info["faq_skipped_empty_collection"] = True
            return RetrievalResult(query=prepared.rewritten_query, source_type="faq")
        if (
            context.fast_faq_result is not None
            and prepared.rewritten_query == context.query
            and prepared.query_variants == [context.query]
            and prepared.effective_source_filter == context.fast_faq_source_filter
        ):
            context.retrieval_info["faq_reused_from_fast_path"] = True
            return context.fast_faq_result
        context.retrieval_info["faq_reused_from_fast_path"] = False
        return get_faq_store(context.scenario.faq_collection).search_many(
            prepared.query_variants,
            k=prepared.plan.faq_top_k,
            source_filter=None,
            kb_version=context.active_kb_version,
            valid_sources=None,
            data_scope=context.data_scope,
            source_type="faq",
            rerank=prepared.plan.rerank,
        )

    faq_result = context.run_stage("faq_retrieval", do_search)
    context.retrieval_info["faq_elapsed_ms"] = round(faq_result.elapsed_ms, 2)
    context.retrieval_info["faq_top_score"] = faq_result.top_score
    return faq_result


def get_faq_direct_answer(
    context: RAGQueryContext,
    prepared: RetrievalPreparation,
    faq_result: RetrievalResult,
) -> str | None:
    """判断 FAQ top 命中是否足够可靠，可靠时直接返回标准答案。★★★ 核心

    使用场景：用户问的是制度型、流程型、标准口径型问题，例如“报销需要哪些材料”。
    如果 FAQ 中已经有高置信标准答案，就不必再让 LLM 重新生成，减少幻觉和延迟。
    """

    threshold = float("inf") if prepared.plan.faq_direct_exact_only else prepared.plan.faq_direct_threshold
    return direct_faq_answer(
        context.query,
        faq_result.top_document,
        faq_result.top_score,
        threshold,
    )


def search_doc(context: RAGQueryContext, prepared: RetrievalPreparation) -> RetrievalResult:
    """按检索计划执行文档混合检索，并把耗时、最高分写入检索诊断信息。★★★ 核心

    使用场景：
    - FAQ 没有直接命中，需要从制度、合同、规范、表格等正文资料里召回证据；
    - 表格问题、复杂资料问题通常依赖文档检索而不是 FAQ 直出。
    """

    def do_search() -> RetrievalResult:
        if not prepared.plan.run_doc:
            return RetrievalResult(query=prepared.rewritten_query, source_type="doc")
        store = get_doc_store(context.scenario.doc_collection)
        initial = store.search_many(
            prepared.query_variants,
            k=prepared.plan.doc_top_k,
            source_filter=prepared.effective_source_filter,
            kb_version=context.active_kb_version,
            valid_sources=context.scenario.valid_sources,
            data_scope=context.data_scope,
            source_type="doc",
            rerank=prepared.plan.rerank,
        )
        if not initial.hits:
            return initial

        # 第一阶段已经用全库语义检索定位候选；随后锁定得分聚合最高的文档版本。
        target = target_document_metadata(initial.hits)
        version_id = target.get("document_version_id")
        doc_id = target.get("doc_id")
        if not version_id and not doc_id:
            context.retrieval_info["document_expansion_skipped"] = "missing_document_identity"
            return initial

        context.retrieval_info.update(
            {
                "retrieval_scope": prepared.plan.retrieval_scope,
                "target_document": target,
            }
        )
        all_chunks = annotate_page_numbers(store.fetch_document_chunks(
            document_version_id=version_id,
            doc_id=doc_id,
            source_filter=prepared.effective_source_filter,
            kb_version=context.active_kb_version,
            valid_sources=context.scenario.valid_sources,
            data_scope=context.data_scope,
            limit=prepared.plan.document_fetch_limit,
        ))
        if not all_chunks:
            context.retrieval_info["document_expansion_skipped"] = "document_chunks_not_found"
            return initial

        score = max(initial.top_score, prepared.plan.min_context_score + 0.01)
        scope = prepared.plan.retrieval_scope
        if scope == "full_summary":
            expanded_docs = deduplicate_parent_documents(all_chunks)
        elif scope == "complete_process":
            expanded_docs = select_complete_process_documents(
                all_chunks,
                fallback_limit=prepared.plan.final_context_top_n,
            )
        else:
            # 具体问题在已锁定的文档版本内再次做语义检索，再补齐命中父块前后邻居。
            identity_field = "document_version_id" if version_id else "doc_id"
            identity_value = version_id or doc_id or ""
            extra_expr = f'{identity_field} == "{escape_expr_value(identity_value)}"'
            within_document = store.search_many(
                prepared.query_variants,
                k=prepared.plan.doc_top_k,
                source_filter=prepared.effective_source_filter,
                kb_version=context.active_kb_version,
                valid_sources=context.scenario.valid_sources,
                data_scope=context.data_scope,
                source_type="doc",
                rerank=prepared.plan.rerank,
                extra_expr=extra_expr,
            )
            anchors = [hit.document for hit in within_document.hits] or [hit.document for hit in initial.hits]
            expanded_docs = select_neighbor_documents(
                all_chunks,
                anchors,
                window=prepared.plan.neighbor_window,
            )
            if not expanded_docs:
                return within_document if within_document.hits else initial

        context.retrieval_info["document_chunk_count"] = len(all_chunks)
        context.retrieval_info["expanded_parent_count"] = len(expanded_docs)
        return RetrievalResult(
            hits=[
                RetrievalHit(
                    document=type(document)(
                        page_content=document.page_content,
                        metadata={**(document.metadata or {}), "retrieval_expansion": scope},
                    ),
                    score=score,
                )
                for document in expanded_docs
            ],
            query=initial.query,
            source_type="doc",
            elapsed_ms=initial.elapsed_ms,
        )

    doc_result = context.run_stage("doc_retrieval", do_search)
    context.retrieval_info["doc_elapsed_ms"] = round(doc_result.elapsed_ms, 2)
    context.retrieval_info["doc_top_score"] = doc_result.top_score
    return doc_result
