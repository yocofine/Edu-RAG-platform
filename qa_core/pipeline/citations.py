"""最终答案引用来源强约束：模型漏写来源编号时在末尾补充"参考来源"，确保证据链完整。
"""

from __future__ import annotations
import re
from typing import Any

from langchain_core.documents import Document

from qa_core.document_metadata import format_source_label, is_table_document

CITATION_RE = re.compile(r"\[\d+\]")
TABLE_CELL_RE = re.compile(r"^-\s*(?P<key>[^:：]{1,40})[:：]\s*(?P<value>.+?)\s*$")
MARKDOWN_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.MULTILINE)
MARKDOWN_RULE_RE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)
MARKDOWN_BOLD_RE = re.compile(r"\*{1,2}([^*\n]+)\*{1,2}")


def normalize_answer_text(answer: str) -> str:
    """答案格式确定性归一化：去掉来源编号和 Markdown 标记，保证前端展示与历史存档都是纯文本。
    """
    text = CITATION_RE.sub("", answer or "")
    # 去掉行首的 Markdown 标题符号（#、##、###、#### 等）
    text = MARKDOWN_HEADING_RE.sub("", text)
    # 去掉整行的 Markdown 分隔线（---、***、___）
    text = MARKDOWN_RULE_RE.sub("", text)
    # 去掉 Markdown 加粗/斜体标记，保留内部文字
    text = MARKDOWN_BOLD_RE.sub(r"\1", text)
    # 去掉符号被移除后残留在中文标点前的空格
    text = re.sub(r"[ \t]+([，。；：！？、,.!?;:])", r"\1", text)
    # 压缩连续空行，避免留下大片空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def source_reference_label(doc: Document, index: int) -> str:
    """生成简短来源标签（文件名/FAQ 标准问题；表格资料附加 sheet 和行号）。
    """
    metadata: dict[str, Any] = dict(doc.metadata or {})
    # 从元数据中提取可读来源标签（文件名或表格 sheet+行号）
    return format_source_label(metadata)


def extract_table_cells(doc: Document) -> list[tuple[str, str]]:
    """从表格行文本中提取"列名-单元格值"键值对。
    """
    cells: list[tuple[str, str]] = []
    for line in str(doc.page_content or "").splitlines():
        match = TABLE_CELL_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key").strip()
        value = match.group("value").strip()
        if key and value:
            cells.append((key, value))
    return cells


def build_table_row_detail(doc: Document, index: int) -> str:
    """构造一条可直接追加到答案末尾的表格行要点。

    例: "表格行要点：状态：进行中；金额：5000 [1]"
    """
    # 从表格行文本中提取"列名-单元格值"键值对
    cells = extract_table_cells(doc)
    if not cells:
        return ""
    detail = "；".join(f"{key}：{value}" for key, value in cells[:6])
    return f"表格行要点：{detail}"


def needs_table_row_detail(answer: str, doc: Document) -> bool:
    """判断模型自由文本是否遗漏了表格行中的键值对，以决定是否需要后处理补全。
    """
    # 从表格行文本中提取"列名-单元格值"键值对
    cells = extract_table_cells(doc)
    if not cells:
        return False
    return any(value not in answer for _, value in cells)


def has_source_citation(answer: str) -> bool:
    """判断答案中是否已经包含 `[数字]` 形式的来源编号。"""
    return bool(CITATION_RE.search(answer or ""))


def enforce_table_row_details(answer: str, context_docs: list[Document]) -> str:
    """LLM 文本生成易丢弃半结构化表格单元格（状态/金额），后处理确定性补全保证信息不遗漏。
    """
    details: list[str] = []
    for index, doc in enumerate(context_docs, start=1):
        # 只补全表格文档中模型未覆盖的行，非表格文档或已含单元格值的行无需处理
        if not is_table_document(doc) or not needs_table_row_detail(answer, doc):
            continue
        # 为漏掉的表格行构造要点详情（含来源编号）
        detail = build_table_row_detail(doc, index)
        if detail:
            details.append(detail)
        if len(details) >= 1:
            break
    if not details:
        return answer
    return f"{answer}\n\n" + "\n".join(details)


def enforce_answer_citations(answer: str, context_docs: list[Document]) -> str:
    """后处理保证每条答案都有可追溯的来源编号，不依赖模型在生成时主动遵守引用格式。
    """
    clean_answer = normalize_answer_text(answer)
    # 无答案或无上下文文档时无需补充来源
    if not clean_answer or not context_docs:
        return clean_answer
    # 确保表格类答案不丢失核心单元格信息（状态/金额/责任人等）
    clean_answer = enforce_table_row_details(clean_answer, context_docs)
    # 模型已自行给出参考来源时不再重复追加
    if "参考来源" in clean_answer:
        return clean_answer
    # 同一文件的连续父块只展示一次来源，避免完整流程回答重复三遍相同文件名。
    labels: list[str] = []
    for index, doc in enumerate(context_docs, start=1):
        label = source_reference_label(doc, index)
        if label not in labels:
            labels.append(label)
        if len(labels) >= 3:
            break
    references = "；".join(labels)
    return f"{clean_answer}\n\n参考来源：{references}"
