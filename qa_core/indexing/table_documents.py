"""表格资料转换为 LangChain Document。

真实企业资料里，很多关键知识不是自然段，而是表格：材料清单、验收项、付款节点、
评分表、赔付字段、单证字段。普通段落切分会破坏行列关系，所以表格需要先转换成
带表头、行号和单元格键值的结构化文本，再进入统一的 normalize/split/Milvus 入库链路。
"""

from __future__ import annotations

from io import StringIO

from .encoding import decode_bytes
import re
from pathlib import Path
import pandas as pd
from langchain_core.documents import Document
from qa_core.indexing.ocr_review import is_reviewed_ocr_text

TABLE_SUFFIXES = {".csv", ".xlsx", ".xls"}
# 载体形式词：只有出现在文件名时才视为扫描件/图片件风险。
# 这些词在正文里常作为业务名词出现（如"补充声明扫描件""营业执照扫描件"），
# 不代表文件本身是扫描件，放在正文匹配会误判，导致干净的结构化资料被门禁拦下。
OCR_CARRIER_RE = re.compile(r"(扫描件|扫描版|图片PDF|图片 PDF)", re.IGNORECASE)
# 噪声特征词：出现在正文即视为 OCR 噪声风险（实际识别错误的元描述）。
OCR_NOISE_RE = re.compile(r"(OCR|断行|错字|O 和 0|噪声)", re.IGNORECASE)

def is_table_file(path: Path) -> bool:
    """判断文件是否属于表格型资料。"""
    return path.suffix.lower() in TABLE_SUFFIXES

def looks_like_ocr_risk(path: Path, text: str) -> bool:
    """识别疑似 OCR 或扫描件风险。
    该函数只做风险识别，不执行 OCR。扫描件进入默认主链路前必须先人工复核或走独立
    OCR 清洗流程，避免把错字、断行和金额识别错误直接写进 active 知识库。

    区分两类信号避免误判：
    - 载体形式词（扫描件/图片PDF）只在文件名命中才算风险，正文里多是业务名词。
    - 噪声特征词（OCR/断行/错字等）在正文命中即算风险。
    """
    if is_reviewed_ocr_text(text):
        return False
    if OCR_CARRIER_RE.search(path.name):
        return True
    return bool(OCR_NOISE_RE.search(text[:2000]))

def looks_like_table_text(path: Path, text: str) -> bool:
    """识别普通文本中是否包含表格特征。
    Markdown 表格仍可由普通 Markdown loader 处理；这里额外给质量报告打标，方便
    后续判断是否需要把某份文档拆成独立表格资料。

    注意这里不按"清单、台账、表格"这类普通业务词直接判定。企业制度里经常写
    "问题清单、图纸台账、修改表格"，这些是自然语言，不代表文件真的具有行列结构。
    如果误报，质量报告会把正常 Markdown 当成表格资料，反而增加复核噪声。
    """
    sample = text[:4000]
    if is_table_file(path):
        return True
    markdown_table_lines = [line for line in sample.splitlines() if line.count("|") >= 2]
    if len(markdown_table_lines) >= 2:
        return True
    comma_like_rows = [line for line in sample.splitlines() if line.count(",") >= 2]
    return len(comma_like_rows) >= 2

def load_table_file(path: Path) -> list[Document]:
    """把 CSV/Excel 表格转换为保留行列语义的 Document 列表。

    CSV 默认按整表读取；Excel 会逐个 sheet 读取。每张表生成三级证据：表级摘要、
    行级数据、带重叠的关联行组。这样既能精确查找单行，也能回答整表概览和跨行比较。
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        decoded = decode_bytes(path.read_bytes())
        frames = [("csv", pd.read_csv(StringIO(decoded.text)))]
    elif suffix == ".xlsx":
        frames = list(pd.read_excel(path, sheet_name=None, engine="openpyxl").items())
    elif suffix == ".xls":
        frames = list(pd.read_excel(path, sheet_name=None, engine="xlrd").items())
    else:
        raise ValueError(f"不支持的表格文件类型：{path}")

    documents: list[Document] = []
    for sheet_name, frame in frames:
        normalized = _normalize_frame(frame)
        headers = [str(column) for column in normalized.columns]
        table_id = _table_id(path, str(sheet_name))
        records = normalized.to_dict(orient="records")
        if not records:
            continue

        documents.append(_table_summary_document(path, str(sheet_name), table_id, headers, records))
        normalized_rows: list[tuple[int, dict[str, str]]] = []
        for row_number, row in enumerate(records, start=1):
            cells = {str(key): _cell_text(value) for key, value in row.items()}
            if not any(cells.values()):
                continue
            normalized_rows.append((row_number, cells))
            cell_lines = [f"- {key}：{value}" for key, value in cells.items() if value]
            content = "\n".join(
                [
                    f"表格文件：{path.name}",
                    f"工作表：{sheet_name}",
                    f"表头：{' / '.join(headers)}",
                    f"行号：{row_number}",
                    "单元格：",
                    *cell_lines,
                ]
            )
            documents.append(
                Document(
                    page_content=content,
                    metadata={
                        "content_type": "table_row",
                        "table_id": table_id,
                        "sheet_name": str(sheet_name),
                        "row_number": row_number,
                        "row_count": len(normalized),
                        "column_count": len(headers),
                        "table_headers": " | ".join(headers),
                    },
                )
            )
        documents.extend(
            _table_row_group_documents(
                path, str(sheet_name), table_id, headers, normalized_rows,
            )
        )
    return documents


def _table_summary_document(
    path: Path,
    sheet_name: str,
    table_id: str,
    headers: list[str],
    records: list[dict],
) -> Document:
    """Create one table-level retrieval unit without asking an LLM to invent a summary."""
    samples: list[str] = []
    for row_number, row in enumerate(records[:3], start=1):
        values = [f"{key}：{_cell_text(value)}" for key, value in row.items() if _cell_text(value)]
        if values:
            samples.append(f"第{row_number}行：" + "；".join(values))
    content = "\n".join([
        f"表格文件：{path.name}",
        f"工作表：{sheet_name}",
        f"表格概览：共{len(records)}行、{len(headers)}列",
        f"表头：{' / '.join(headers)}",
        "示例数据：",
        *samples,
    ])
    return Document(
        page_content=content,
        metadata={
            "content_type": "table_summary",
            "table_id": table_id,
            "sheet_name": sheet_name,
            "row_count": len(records),
            "column_count": len(headers),
            "table_headers": " | ".join(headers),
        },
    )


def _table_row_group_documents(
    path: Path,
    sheet_name: str,
    table_id: str,
    headers: list[str],
    rows: list[tuple[int, dict[str, str]]],
    *,
    group_size: int = 5,
    overlap: int = 1,
) -> list[Document]:
    """Create adjacent-row groups for comparisons while keeping stable row ranges."""
    if len(rows) <= 1:
        return []
    documents: list[Document] = []
    step = max(1, group_size - overlap)
    for start in range(0, len(rows), step):
        group = rows[start:start + group_size]
        if len(group) < 2:
            continue
        row_lines = []
        for row_number, cells in group:
            values = [f"{key}：{value}" for key, value in cells.items() if value]
            row_lines.append(f"第{row_number}行：" + "；".join(values))
        first_row, last_row = group[0][0], group[-1][0]
        documents.append(Document(
            page_content="\n".join([
                f"表格文件：{path.name}", f"工作表：{sheet_name}",
                f"表头：{' / '.join(headers)}", f"关联行：{first_row}-{last_row}", *row_lines,
            ]),
            metadata={
                "content_type": "table_row_group",
                "table_id": table_id,
                "sheet_name": sheet_name,
                "row_start": first_row,
                "row_end": last_row,
                "row_count": len(rows),
                "column_count": len(headers),
                "table_headers": " | ".join(headers),
            },
        ))
    return documents

def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """清理表格空行空列，并把缺失表头补成稳定列名。"""
    data = frame.dropna(how="all").dropna(axis=1, how="all").fillna("")
    columns: list[str] = []
    for index, column in enumerate(data.columns, start=1):
        name = str(column).strip()
        if not name or name.lower().startswith("unnamed:"):
            name = f"列{index}"
        columns.append(name)
    data.columns = columns
    return data

def _cell_text(value: object) -> str:
    """把单元格值转换成适合检索的短文本。"""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text

def _table_id(path: Path, sheet_name: str) -> str:
    """生成稳定表格标识，便于质量报告和来源回查。"""
    return f"{path.stem}:{sheet_name}"
