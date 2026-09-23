"""检索查询扩展工具：为同一检索意图生成少量同义检索表达（如"Webhook" → "回调"），不改变问题含义。
"""

from __future__ import annotations
import re
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from qa_core.config.logging_config import get_logger
from qa_core.config.rules import QueryVariantReplacementRule, get_rule_config
from qa_core.prompts.constants import QUERY_VARIANT_SYSTEM_PROMPT
from qa_core.config.settings import get_settings
from qa_core.llm.client import get_chat_model
logger = get_logger(__name__)

FOLLOW_UP_REWRITE_MARKERS = ("追问：", "追问:")

# 复合业务问题通常同时携带“产品/场景限定词”和真正需要检索的动作，例如：
# “某产品的学生要更换老师怎么操作”。若只用整句检索，产品名容易压过动作语义。
# 这里维护的是跨业务通用的参与者、动作和问法，不绑定某一个产品或某一个答案。
_PROCESS_ACTORS = (
    "学生", "学员", "客户", "用户", "老师", "讲师", "导师", "订单", "课程",
    "文件", "资料", "账号", "系统",
)
_PROCESS_ACTIONS = tuple(sorted((
    "更换", "换老师", "换讲师", "换导师", "换课", "退款", "退费", "取消", "删除",
    "修改", "上传", "下载", "提交", "申请", "审批", "安排", "跟进", "投诉", "续费",
    "开票", "报销", "登录", "注册", "预警", "结课", "匹配", "派单", "拉群", "沟通",
    "确认", "新增", "创建", "导入", "导出", "解绑", "绑定", "转单", "换单",
), key=len, reverse=True))
_PROCESS_QUESTION_MARKERS = (
    "怎么操作", "如何操作", "怎么处理", "如何处理", "怎么办", "怎么做", "如何做",
    "怎么走", "如何进行",
)

class QueryVariants(BaseModel):
    """LLM 输出检索表达时使用的 Pydantic 结构化模型，避免模型输出解释性文本。
    """

    queries: list[str] = Field(default_factory=list, description="等价检索表达")


def generate_query_variants(query: str, *, enabled: bool, allow_short_structured: bool = False) -> list[str]:
    """为同一检索意图生成少量同义表达（如"流程"→"SOP"），提升召回而不改变问题含义。
    """
    # 加载应用全局设置（retrieval_variant_max 等检索配置）
    settings = get_settings()
    cleaned = query.strip()
    # 功能禁用或无可变体空间时仅用原问题检索，避免无关变体稀释召回精度
    if not enabled or not cleaned or settings.retrieval_variant_max <= 0:
        return [cleaned]

    # 复合动作型问题先做零成本语义聚焦。该步骤必须位于“短问题跳过扩展”之前，
    # 否则“某产品 + 某角色 + 某动作 + 怎么操作”会因字数短而只检索原句。
    focused_variants = _focused_process_variants(cleaned, settings.retrieval_variant_max)
    if len(focused_variants) > 1:
        return focused_variants

    # 普通短结构化问题保持克制；已追问改写的问题仍允许规则变体，避免上下文锚点丢失同义召回机会。
    if (
        _looks_like_short_structured_question(cleaned)
        and not allow_short_structured
        and not _is_rewritten_follow_up_query(cleaned)
    ):
        return [cleaned]

    # 第一层：先用确定性本地规则（零成本）为高频业务术语生成同义变体
    # 规则生成足够变体时直接返回，跳过第二层 LLM 调用，兼顾延迟与成本
    heuristic_variants = _heuristic_variants(cleaned, settings.retrieval_variant_max)
    if len(heuristic_variants) > 1:
        return heuristic_variants

    # 关闭 LLM 兜底时只用原问题检索：命中本地规则的变体已在上面返回，这里直接回落，
    # 避免每次提问都多一次 3-4 秒的大模型往返。
    if not settings.retrieval_variants_llm_enabled:
        return [cleaned]

    variants = [cleaned]
    # 第二层：规则未覆盖的新领域词或罕见表达回退 LLM 扩展，避免召回覆盖率因规则缺失而下降
    model = get_chat_model(streaming=False).with_structured_output(QueryVariants)
    # 调用 LLM 生成等价检索表达（如同义词、不同说法），不改变用户问题含义
    result = model.invoke(
        [
            SystemMessage(content=QUERY_VARIANT_SYSTEM_PROMPT),
            HumanMessage(content=f"原问题：{cleaned}\n最多生成 {settings.retrieval_variant_max} 条检索表达。"),
        ]
    )
    for item in result.queries:
        candidate = str(item).strip()
        if candidate and candidate not in variants:
            variants.append(candidate)
        if len(variants) >= settings.retrieval_variant_max + 1:
            break
    return variants


def _heuristic_variants(query: str, max_extra: int) -> list[str]:
    """用配置中的确定性规则为高频业务知识说法生成同义变体。"""
    variants = [query]
    rules = get_rule_config().query_variants

    def add(candidate: str) -> None:
        """在保持顺序和上限的前提下，追加非空不重复变体。"""
        candidate = candidate.strip()
        if candidate and candidate not in variants and len(variants) < max_extra + 1:
            variants.append(candidate)

    for rule in rules.replacements:
        if not rule.matches(query):
            continue
        for old, new in rule.replacements:
            add(_replace_term(query, old, new, rule))
    return variants


def _focused_process_variants(query: str, max_extra: int) -> list[str]:
    """从复合业务问题中抽取动作主干，降低产品名、场景名对召回排序的干扰。"""
    variants = [query]
    compact = re.sub(r"\s+", "", query).strip("，。！？!?、,.;；：:")
    if not compact or not any(marker in compact for marker in _PROCESS_QUESTION_MARKERS):
        return variants

    action_positions = [
        (compact.find(action), action)
        for action in _PROCESS_ACTIONS
        if compact.find(action) >= 0
    ]
    if not action_positions:
        return variants
    action_index, _ = min(action_positions, key=lambda item: item[0])

    # 优先保留离动作最近的业务参与者，删除其前面的产品名、板块名等限定噪音。
    actor_positions = [compact.rfind(actor, 0, action_index + 1) for actor in _PROCESS_ACTORS]
    actor_positions = [position for position in actor_positions if position >= 0]
    start = max(actor_positions) if actor_positions else action_index
    focused = compact[start:]

    def add(candidate: str) -> None:
        candidate = candidate.strip("，。！？!?、,.;；：:")
        if candidate and candidate not in variants and len(variants) < max_extra + 1:
            variants.append(candidate)

    canonical = focused
    for marker in _PROCESS_QUESTION_MARKERS:
        canonical = canonical.replace(marker, "")
    canonical = re.sub(r"(学生|学员|客户|用户)(?:要|想|需要|希望|应该|可以)", r"\1", canonical)
    canonical = canonical.strip("的 ")
    if canonical and not re.search(r"(流程|步骤)$", canonical):
        canonical += "处理流程"
    add(canonical)
    # 复合问题只增加一个高度聚焦的动作表达：原问题负责保留产品/场景范围，
    # 核心变体负责定位具体流程，避免为了近义改写额外增加多次向量检索延迟。
    if len(variants) == 1:
        add(focused)
    return variants


def _looks_like_short_structured_question(query: str) -> bool:
    """判断问题的常见同义说法是否已被配置规则覆盖，无需进一步 LLM 扩展。"""
    return get_rule_config().query_variants.is_short_structured_question(query)


def _is_rewritten_follow_up_query(query: str) -> bool:
    """判断是否为追问改写产物，例如"报销流程是什么；追问：那审批呢"。"""
    return any(marker in query for marker in FOLLOW_UP_REWRITE_MARKERS)


def _replace_term(query: str, old: str, new: str, rule: QueryVariantReplacementRule) -> str:
    """Apply one configured replacement, optionally case-insensitive."""

    if not rule.ignore_case:
        return query.replace(old, new)
    return re.sub(re.escape(old), new, query, flags=re.IGNORECASE)

