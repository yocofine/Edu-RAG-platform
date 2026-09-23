"""全文总结的 map 阶段：把长文档分批压缩，再交给最终流式回答做 reduce。"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage

from qa_core.llm.client import get_chat_model

_MAP_SYSTEM_PROMPT = """你是文档分段摘要器。只提取输入资料中明确出现的事实、步骤、规则和注意事项。
保留原始编号、Step 编号和关键条件，不补充资料外信息，不回答用户问题，不使用 Markdown。"""


def _batch_documents(documents: list[Document], max_chars: int) -> list[list[Document]]:
    batches: list[list[Document]] = []
    current: list[Document] = []
    current_chars = 0
    for document in documents:
        size = len(document.page_content or "")
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(document)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def summarize_document_batches(
    documents: list[Document],
    *,
    question: str,
    max_batch_chars: int,
) -> list[Document]:
    """分批总结长文档；只有超过一个批次时才额外调用模型。"""
    batches = _batch_documents(documents, max(1000, max_batch_chars))
    if len(batches) <= 1:
        return documents
    model = get_chat_model(streaming=False)
    summaries: list[Document] = []
    for index, batch in enumerate(batches, start=1):
        source_text = "\n\n".join(document.page_content for document in batch if document.page_content)
        response = model.invoke(
            [
                SystemMessage(content=_MAP_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"用户最终要了解：{question}\n\n"
                        f"以下是文档第 {index}/{len(batches)} 段，请生成忠实的分段摘要：\n{source_text}"
                    )
                ),
            ]
        )
        content = str(getattr(response, "content", "") or "").strip()
        if not content:
            content = source_text
        metadata = dict(batch[0].metadata or {})
        metadata.update(
            {
                "content_type": "document_batch_summary",
                "summary_batch_index": index,
                "summary_batch_count": len(batches),
            }
        )
        summaries.append(Document(page_content=content, metadata=metadata))
    return summaries
