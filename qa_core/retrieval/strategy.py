"""RAG 链路动态检索计划，将意图结果转为检索参数。

它把意图识别结果转换成具体检索参数，例如 top_k、阈值、是否查询 FAQ/文档集合。
不同问题类型（FAQ、追问、短问题、表格问题）会经过四层决策链得到不同策略。

决策层次：
    1. 意图分支：直接回答、FAQ、知识查询或追问。
    2. 短问题保护：短句歧义大，限制文档检索并提高直出门槛。
    3. 风险类别：费用、合规、排障、总结类问题使用不同参数。
    4. 表格偏好：表格类查询扩大文档候选池。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from qa_core.config.settings import get_settings
from qa_core.intent.classifier import IntentResult
from qa_core.intent.question_category import infer_question_category, is_table_query
from qa_core.retrieval.scope import infer_retrieval_scope


@dataclass(frozen=True)
class RetrievalPlan:
    """单个用户问题对应的具体检索参数。QAService 消费此计划而非直接读取 settings，便于策略调整和诊断。

    字段说明：
      - run_faq/run_doc：是否查询 FAQ/文档集合。
      - faq_top_k/doc_top_k：FAQ 和文档初始召回数量。
      - rerank：是否启用 CrossEncoder 重排。
      - faq_direct_threshold：FAQ 直出最低分数阈值。
      - final_context_top_n：最终进入 LLM 上下文的最多片段数。
      - min_context_score：进入上下文的最低相关性分数。
      - max_context_chars/max_context_doc_chars：总上下文和单文档字符上限。
      - use_query_variants：是否生成查询变体。
      - question_category：问题风险类别。
      - prefer_table：是否偏向表格行资料。
      - faq_direct_exact_only：是否只允许精确 FAQ 直出，禁用相似分数直出。
      - reason：检索策略原因标签，用于诊断。
    """

    run_faq: bool
    run_doc: bool
    faq_top_k: int
    doc_top_k: int
    rerank: bool
    faq_direct_threshold: float
    final_context_top_n: int
    min_context_score: float
    max_context_chars: int
    max_context_doc_chars: int
    use_query_variants: bool
    question_category: str
    prefer_table: bool
    faq_direct_exact_only: bool
    reason: str
    # 三阶段检索范围：具体问题补相邻块；流程问题展开同文档流程；全文总结读取整篇。
    retrieval_scope: str = "specific"
    neighbor_window: int = 1
    document_fetch_limit: int = 2000

    def as_dict(self) -> dict:
        """返回可 JSON 序列化的检索计划，供诊断接口使用。

        返回：
            RetrievalPlan 字段字典。
        """
        return asdict(self)


# ── Rule table helpers ──────────────────────────────────────────────

PlanParams = dict[str, Any]
DIRECT_INTENTS = {"GREETING", "HUMAN_SERVICE", "OUT_OF_SCOPE"}


@dataclass(frozen=True)
class PlanPatch:
    """检索计划规则补丁，只描述需要调整的字段。"""

    reason: str
    replace_reason: bool = False
    run_faq: bool | None = None
    run_doc: bool | None = None
    faq_top_k: int | None = None
    faq_top_k_min: int | None = None
    doc_top_k: int | None = None
    doc_top_k_min: int | None = None
    doc_top_k_max: int | None = None
    final_context_top_n_min: int | None = None
    direct_threshold: float | None = None
    direct_threshold_min: float | None = None
    faq_direct_exact_only: bool | None = None


def _intent_rules(settings) -> dict[str, PlanPatch]:
    """返回意图到检索参数补丁的映射。"""
    return {
        "FAQ_QUERY": PlanPatch(
            reason="faq_first",
            replace_reason=True,
            doc_top_k=max(8, settings.doc_top_k // 2),
            direct_threshold=max(0.62, settings.faq_direct_score_threshold - 0.08),
        ),
        "KNOWLEDGE_QUERY": PlanPatch(
            reason="knowledge_doc_enriched",
            replace_reason=True,
            doc_top_k_min=max(settings.doc_top_k, settings.doc_complex_query_top_k),
            final_context_top_n_min=5,
        ),
        "FOLLOW_UP": PlanPatch(
            reason="history_aware_follow_up",
            replace_reason=True,
            faq_top_k=max(settings.faq_top_k, 24),
            doc_top_k_min=max(settings.doc_top_k, settings.doc_complex_query_top_k),
            final_context_top_n_min=5,
            direct_threshold_min=max(settings.faq_direct_score_threshold, 0.78),
        ),
    }


def _category_rules(settings) -> dict[str, PlanPatch]:
    """返回问题类别到检索参数补丁的映射。"""
    expanded_doc = settings.doc_complex_query_top_k
    return {
        "pricing": PlanPatch(
            reason="pricing_guard",
            faq_top_k_min=settings.faq_top_k,
            doc_top_k_min=expanded_doc,
            final_context_top_n_min=6,
            direct_threshold_min=0.84,
        ),
        "compliance": PlanPatch(
            reason="compliance_guard",
            doc_top_k_min=expanded_doc,
            final_context_top_n_min=6,
            direct_threshold_min=0.86,
        ),
        "troubleshooting": PlanPatch(
            reason="troubleshooting_expanded",
            doc_top_k_min=expanded_doc,
            final_context_top_n_min=6,
        ),
        "summary": PlanPatch(
            reason="summary_expanded",
            doc_top_k_min=expanded_doc,
            final_context_top_n_min=6,
        ),
    }


def _base_params(settings, is_short: bool) -> PlanParams:
    """构建检索计划的基础参数。"""
    return {
        "run_faq": True,
        "run_doc": True,
        "faq_top_k": settings.faq_short_query_top_k if is_short else settings.faq_top_k,
        "doc_top_k": settings.doc_top_k,
        "final_context_top_n": settings.final_context_top_n,
        "direct_threshold": settings.faq_direct_score_threshold,
        "faq_direct_exact_only": False,
        "reason": "balanced_retrieval",
    }


def _apply_patch(params: PlanParams, patch: PlanPatch) -> PlanParams:
    """把一条规则补丁应用到参数字典。"""
    if patch.run_faq is not None:
        params["run_faq"] = patch.run_faq
    if patch.run_doc is not None:
        params["run_doc"] = patch.run_doc
    if patch.faq_top_k is not None:
        params["faq_top_k"] = patch.faq_top_k
    if patch.faq_top_k_min is not None:
        params["faq_top_k"] = max(params["faq_top_k"], patch.faq_top_k_min)
    if patch.doc_top_k is not None:
        params["doc_top_k"] = patch.doc_top_k
    if patch.doc_top_k_min is not None:
        params["doc_top_k"] = max(params["doc_top_k"], patch.doc_top_k_min)
    if patch.doc_top_k_max is not None:
        params["doc_top_k"] = min(params["doc_top_k"], patch.doc_top_k_max)
    if patch.final_context_top_n_min is not None:
        params["final_context_top_n"] = max(params["final_context_top_n"], patch.final_context_top_n_min)
    if patch.direct_threshold is not None:
        params["direct_threshold"] = patch.direct_threshold
    if patch.direct_threshold_min is not None:
        params["direct_threshold"] = max(params["direct_threshold"], patch.direct_threshold_min)
    if patch.faq_direct_exact_only is not None:
        params["faq_direct_exact_only"] = patch.faq_direct_exact_only
    params["reason"] = patch.reason if patch.replace_reason else f"{params['reason']}_{patch.reason}"
    return params


def _apply_plan_rules(
    params: PlanParams,
    *,
    intent: IntentResult,
    is_short: bool,
    question_category: str,
    prefer_table: bool,
    settings,
) -> PlanParams:
    """按固定顺序应用意图、短问题、类别和表格规则。"""
    if intent.direct_answer or intent.intent in DIRECT_INTENTS:
        return _apply_patch(
            params,
            PlanPatch(
                reason="direct_answer_no_retrieval",
                replace_reason=True,
                run_faq=False,
                run_doc=False,
                direct_threshold=1.0,
            ),
        )

    intent_patch = _intent_rules(settings).get(intent.intent)
    if intent_patch:
        _apply_patch(params, intent_patch)
    if is_short and intent.intent != "FOLLOW_UP":
        _apply_patch(
            params,
            PlanPatch(
                reason="short_query_guard",
                doc_top_k_max=max(12, settings.final_context_top_n * 2),
                direct_threshold_min=0.78,
            ),
        )
    category_patch = _category_rules(settings).get(question_category)
    if category_patch:
        _apply_patch(params, category_patch)
    if prefer_table and params["run_doc"]:
        _apply_patch(
            params,
            PlanPatch(
                reason="table_row_preferred",
                doc_top_k_min=settings.doc_complex_query_top_k,
                final_context_top_n_min=7,
                faq_direct_exact_only=True,
            ),
        )
    return params


def build_retrieval_plan(query: str, intent: IntentResult) -> RetrievalPlan:
    """根据问题形态和意图结果构建检索策略。按 5 层决策逐层收紧参数。（★★★ 核心）

    执行流程：
      1. 初始化默认参数：问题清洗、类别识别、表格偏好识别、短句识别。
      2. 应用意图分支：直接回答关闭检索，FAQ/知识/追问分别调整召回量和阈值。
      3. 应用短问题保护：短句提高 FAQ 直出门槛并收缩文档召回。
      4. 应用风险类别：费用、合规、排障、总结问题扩大召回或提高阈值。
      5. 应用表格偏好：表格问题扩大文档候选并禁用模糊 FAQ 直出。
      6. 组装不可变 RetrievalPlan；知识查询和追问启用查询变体。

    参数：
        query: 用户原始问题。
        intent: 意图识别输出的 IntentResult.

    返回：
        QAService 执行检索所需的完整 RetrievalPlan。
    """
    settings = get_settings()
    compact_query = query.strip()
    # 推断问题风险类别（pricing/compliance/troubleshooting/summary/other）—— 风险类别驱动检索阈值和回答模板
    question_category = infer_question_category(compact_query)
    # 判断是否为表格类查询 —— 表格问题需扩大候选集并禁用模糊 FAQ 直出，语义相似但不同列的表格内容误导性极强
    prefer_table = is_table_query(compact_query)
    retrieval_scope = infer_retrieval_scope(compact_query)
    is_short = len(compact_query) <= settings.short_query_max_chars

    params = _apply_plan_rules(
        _base_params(settings, is_short),
        intent=intent,
        is_short=is_short,
        question_category=question_category,
        prefer_table=prefer_table,
        settings=settings,
    )

    final_context_top_n = params['final_context_top_n']
    max_context_chars = settings.max_prompt_context_chars
    max_context_doc_chars = settings.max_context_doc_chars
    if retrieval_scope == "complete_process":
        # 完整流程需要容纳同一章节中的连续父块，不能继续受默认 4~6 块限制。
        final_context_top_n = max(final_context_top_n, settings.process_context_top_n)
        max_context_chars = max(max_context_chars, settings.process_context_max_chars)
    elif retrieval_scope == "full_summary":
        # 全文总结先获取整篇，后续在 summary map-reduce 中压缩，不直接一次塞给最终模型。
        final_context_top_n = max(final_context_top_n, settings.summary_document_top_n)
        max_context_chars = max(max_context_chars, settings.summary_document_max_chars)

    return RetrievalPlan(
        run_faq=params['run_faq'],
        run_doc=params['run_doc'],
        faq_top_k=params['faq_top_k'],
        doc_top_k=params['doc_top_k'],
        rerank=True,
        faq_direct_threshold=params['direct_threshold'],
        final_context_top_n=final_context_top_n,
        min_context_score=settings.rag_min_score_threshold,
        max_context_chars=max_context_chars,
        max_context_doc_chars=max_context_doc_chars,
        use_query_variants=intent.intent in {"KNOWLEDGE_QUERY", "FOLLOW_UP"},
        question_category=question_category,
        prefer_table=prefer_table,
        faq_direct_exact_only=params['faq_direct_exact_only'],
        reason=params['reason'],
        retrieval_scope=retrieval_scope,
        neighbor_window=settings.document_neighbor_window,
        document_fetch_limit=settings.document_fetch_limit,
    )
