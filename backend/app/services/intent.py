import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from ..config import get_settings
from ..schemas import IntentFilters, IntentResult


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是教辅知识库的意图路由器。只输出JSON，不回答用户问题。
可选意图：FILE_SEARCH、FILE_PREVIEW、FILE_DOWNLOAD、KNOWLEDGE_QUERY、SUMMARY、COMPARISON、CHAT、GREETING、THANKS、GOODBYE、CAPABILITY、OUT_OF_SCOPE。

路由原则：
1. “找/查/最新/最近上传的文件”通常是FILE_SEARCH；“打开/预览第几个”是FILE_PREVIEW；“下载”是FILE_DOWNLOAD。
2. 只要用户可能在询问已上传资料中的业务知识、业务流程、产品服务、学生/学员处理、老师/讲师安排、订单、课程、论文辅导、客户管理、售后、系统操作、培训、制度或内部规范，就归为KNOWLEDGE_QUERY。
3. 不要因为不认识“论文大礼包”等内部业务名词，或不确定知识库是否真的包含答案，就判为OUT_OF_SCOPE；存在业务相关可能性时必须先进入KNOWLEDGE_QUERY检索验证。
4. 总结资料是SUMMARY；比较资料是COMPARISON。
5. 问候和自我介绍类是GREETING，例如“你好”“早上好”“你好，你是谁”；致谢是THANKS；告别是GOODBYE；询问助手能做什么是CAPABILITY。
6. 其余不需要知识库支撑的日常对话是CHAT。不要把问候、致谢、告别、能力询问或普通闲聊归为KNOWLEDGE_QUERY。
7. 只有能够明确确认与教辅业务、已上传资料和文件管理完全无关的问题，才是OUT_OF_SCOPE，例如天气、彩票、股票行情、菜谱或体育赛果。

示例：
- “论文大礼包学生要换老师怎么操作” -> KNOWLEDGE_QUERY
- “学生要换老师怎么操作” -> KNOWLEDGE_QUERY
- “系统客户管理” -> KNOWLEDGE_QUERY
- “包课辅导的流程是什么” -> KNOWLEDGE_QUERY
- “今天北京天气怎么样” -> OUT_OF_SCOPE

filters只允许section、uploader、latest、limit。不要生成文件ID、文件名、URL或不存在的条件。
输出格式：{"intent":"...","confidence":0.0,"query":"核心查询词","filters":{"section":null,"uploader":null,"latest":false,"limit":10},"requires_history":false}
"""


# 意图模型无法看到知识库目录。它把陌生的内部业务词误判为越界时，使用一层
# 确定性保护将问题送入检索；明确属于外部话题的问题仍保留 OUT_OF_SCOPE。
_KNOWLEDGE_DOMAIN_TERMS = (
    "教辅", "教务", "学员", "学生", "老师", "讲师", "导师", "课程", "课时",
    "排课", "辅导", "包课", "论文", "作业", "考试", "订单", "客户", "售后",
    "产品", "服务", "培训", "资料", "手册", "规则", "制度", "流程", "规范",
    "系统操作", "客户管理", "换老师", "更换老师", "退款", "退费", "大礼包",

)

_EXPLICIT_OUT_OF_SCOPE_TERMS = (
    "天气", "彩票", "双色球", "股票", "基金", "币价", "加密货币", "菜谱",
    "体育赛果", "球赛比分", "游戏攻略", "星座运势",
)


def _should_route_to_knowledge(query: str) -> bool:
    """识别可能属于内部教辅业务的表达，避免在检索前被误判为越界。"""
    normalized = "".join(char for char in query.lower() if not char.isspace())
    if any(term in normalized for term in _EXPLICIT_OUT_OF_SCOPE_TERMS):
        return False
    return any(term in normalized for term in _KNOWLEDGE_DOMAIN_TERMS)


def _apply_out_of_scope_guard(query: str, result: IntentResult) -> IntentResult:
    """仅纠正可能的业务问题；不覆盖明确无关问题及其他正常路由结果。"""
    if result.intent != "OUT_OF_SCOPE" or not _should_route_to_knowledge(query):
        return result
    return result.model_copy(update={
        "intent": "KNOWLEDGE_QUERY",
        "query": result.query.strip() or query,
        "confidence": min(result.confidence, 0.85),
    })


def _usage_payload(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    return {
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


class IntentClassifier:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.llm_base_url)
        # 进程内意图识别缓存：相同问题 + 相同上下文不重复调用大模型
        self._cache: dict[tuple[str, str], IntentResult] = {}

    async def classify(self, query: str, history_summary: str = "") -> IntentResult:
        """带进程内缓存的意图识别入口，相同问题与上下文不重复调用大模型。"""
        key = (query, history_summary)
        cached = self._cache.get(key)
        if cached is not None:
            return cached.model_copy(update={"token_usage": _usage_payload(None)})
        result = await self._classify(query, history_summary)
        if len(self._cache) >= 512:
            self._cache.clear()
        self._cache[key] = result
        return result

    async def _classify(self, query: str, history_summary: str = "") -> IntentResult:
        # 高频且无歧义的交互优先确定性分流；其余表达仍交给大模型做语义识别。
        normalized = "".join(
            char for char in query.lower()
            if not char.isspace() and char not in "，。！？!?、,.;；：:~～"
        )
        direct_intents = {
            "你好": "GREETING", "您好": "GREETING", "你好你是谁": "GREETING",
            "早上好": "GREETING", "上午好": "GREETING", "下午好": "GREETING", "晚上好": "GREETING",
            "谢谢": "THANKS", "感谢": "THANKS", "多谢": "THANKS", "谢谢你": "THANKS",
            "再见": "GOODBYE", "拜拜": "GOODBYE", "回头见": "GOODBYE",
            "你能干什么": "CAPABILITY", "你能做什么": "CAPABILITY", "你会什么": "CAPABILITY",
            "有什么功能": "CAPABILITY",
        }
        if normalized in direct_intents:
            return IntentResult(
                intent=direct_intents[normalized], confidence=1.0, query=query,
                requires_history=False,
            )
        # “最新/最近上传的文件”是确定性的文件列表请求，不需要调用意图模型，
        # 也不能把整句话当作文件名关键词，否则 SQL 文件名过滤会返回空结果。
        latest_phrases = {
            "最新文件", "最近文件", "最新上传", "最近上传", "最新上传文件",
            "最近上传文件", "最新上传的文件", "最近上传的文件", "刚上传的文件",
        }
        if normalized in latest_phrases or any(
            phrase in normalized for phrase in ("最新上传的文件", "最近上传的文件", "最新上传文件", "最近上传文件")
        ):
            return IntentResult(
                intent="FILE_SEARCH", confidence=1.0, query="",
                filters=IntentFilters(latest=True), requires_history=False,
            )
        if not self.settings.dashscope_api_key:
            raise RuntimeError("尚未配置DASHSCOPE_API_KEY，无法执行大模型意图识别")
        prompt = f"用户输入：{query}\n会话上下文：{history_summary or '无'}"
        last_error: Exception | None = None
        # 推理模型的 reasoning 会先占用 completion 额度：额度不足时 content 会返回空串，
        # 而 json.loads("") 得到 {}，直接送去校验只会报"缺 intent/confidence/query"，
        # 掩盖了真实症状。因此这里显式判定空响应，重试时逐步加大额度。
        for attempt in range(2):
            try:
                response = await self.client.chat.completions.create(
                    model=self.settings.llm_intent_model,
                    temperature=0,
                    max_tokens=900 * (attempt + 1),
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                content = response.choices[0].message.content or ""
                payload = json.loads(content or "{}")
                if not isinstance(payload, dict) or not payload.get("intent"):
                    raise ValueError(
                        f"意图模型未返回有效 JSON（content 长度 {len(content)}，"
                        f"finish_reason={response.choices[0].finish_reason}）"
                    )
                payload["token_usage"] = _usage_payload(response.usage)
                result = IntentResult.model_validate(payload)
                return _apply_out_of_scope_guard(query, result)
            except Exception as exc:
                last_error = exc
                prompt += "\n上次输出不符合JSON Schema，请严格修正。"
        # 兜底：意图识别失败不应该让整条问答链路直接不可用。默认按知识查询继续，
        # 由下游检索自己判断有没有依据，并在 trace 中留下告警。
        logger.warning("意图识别失败，回退为默认知识查询：%s", last_error)
        return IntentResult(
            intent="KNOWLEDGE_QUERY",
            confidence=0.3,
            query=query,
            requires_history=False,
            token_usage=_usage_payload(None),
        )

    async def reply_chat(self, query: str, history_summary: str = "") -> str:
        """直接处理无需知识库支撑的普通对话，避免误入 RAG 检索链路。"""
        response = await self.client.chat.completions.create(
            model=self.settings.llm_model,
            temperature=0.4,
            max_tokens=1600,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是教辅知识库的智能助手。请用简洁、专业、友好的中文回复普通对话。"
                        "不要声称已经检索文件或引用知识库内容；如用户需要查资料，可自然提示其描述文件或知识问题。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"会话上下文：{history_summary or '无'}\n用户输入：{query}",
                },
            ],
        )
        return (response.choices[0].message.content or "您好，请问有什么可以帮您？").strip()

    async def stream_chat(self, query: str, history_summary: str = "") -> AsyncIterator[dict[str, Any]]:
        """流式生成无需知识库支撑的普通对话。"""
        stream = await self.client.chat.completions.create(
            model=self.settings.llm_model,
            temperature=0.4,
            max_tokens=1600,
            stream=True,
            stream_options={"include_usage": True},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是教辅知识库的智能助手。请用简洁、专业、友好的中文回复普通对话。"
                        "不要声称已经检索文件或引用知识库内容。"
                    ),
                },
                {"role": "user", "content": f"会话上下文：{history_summary or '无'}\n用户输入：{query}"},
            ],
        )
        async for chunk in stream:
            content = chunk.choices[0].delta.content if chunk.choices else None
            if content:
                yield {"content": content}
            if getattr(chunk, "usage", None) is not None:
                yield {"usage": _usage_payload(chunk.usage)}


intent_classifier = IntentClassifier()
