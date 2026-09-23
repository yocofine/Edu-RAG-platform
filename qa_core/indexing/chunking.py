"""文档切分策略。把标准化后的 Document 切成适合 Milvus 检索的父子块。"""

from __future__ import annotations
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from qa_core.config.settings import get_settings
from qa_core.document_metadata import is_reviewed_ocr_metadata, is_table_metadata
from qa_core.utils import stable_hash

CHINESE_SEPARATORS = [
    "\n\n",
    "\n",
    "。", "！", "？", "；",
    ";", ".", "!", "?",
    "，", ",",
    " ",
    "",
    # 原因： 中英文混排文档需要同时支持中文句号/感叹号/问号和英文句点/分号作为切分边界，递归切分器按 separator 顺序优先匹配大粒度分隔符
]

def chunk_identity(page_content: str, metadata: dict) -> tuple[str, str]:
    """基于正文和标准元数据生成 parent_id 与 chunk_id。"""
    parent_content = str(metadata.get("parent_content") or page_content or "").strip()
    if is_table_metadata(metadata):
        parent_id = stable_hash(
            metadata.get("scenario_id"),
            metadata.get("kb_version"),
            metadata.get("embedding_model_version"),
            metadata.get("chunk_schema_version"),
            metadata.get("doc_id"),
            metadata.get("table_id"),
            metadata.get("sheet_name"),
            metadata.get("content_type"),
            metadata.get("row_number"),
            metadata.get("row_start"),
            metadata.get("row_end"),
            parent_content,
        )
        chunk_id = stable_hash(parent_id, parent_content)
        return parent_id, chunk_id

    parent_id = stable_hash(
        metadata.get("scenario_id"),
        metadata.get("kb_version"),
        metadata.get("embedding_model_version"),
        metadata.get("chunk_schema_version"),
        metadata.get("doc_id"),
        parent_content,
    )
    chunk_id = stable_hash(parent_id, page_content)
    return parent_id, chunk_id


def split_documents(documents: list[Document]) -> tuple[list[Document], list[str]]:
    """将文档切成可检索的子块并保留父块上下文。子块用于精确召回，parent_content 保存在 metadata 中。
    Returns (chunks_list, ids_list)."""
    # 原因： parent-child 分别切分使子块保持精确命中而父块提供完整上下文窗口，比单一切片在精确召回率和上下文完整性之间取得更好平衡
    settings = get_settings()

    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.parent_chunk_size,
        chunk_overlap=settings.parent_overlap,
        separators=CHINESE_SEPARATORS,
    )
    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.child_chunk_size,
        chunk_overlap=settings.child_overlap,
        separators=CHINESE_SEPARATORS,
    )

    chunks: list[Document] = []
    ids: list[str] = []
    global_parent_index = 0
    global_chunk_order = 0
    for document_part_index, doc in enumerate(documents):
        file_type = str(doc.metadata.get("file_type", "")).lower()
        parent_docs: list[Document]
        if is_table_metadata(doc.metadata) or is_reviewed_ocr_metadata(doc.metadata):
            # 表格行和已复核 OCR 文本都是治理后的完整证据单元。
            # 表格不能被拆散行列关系；OCR 复核稿不能丢失复核状态、置信度和原始文件说明。
            parent_content = str(doc.page_content or "").strip()
            if not parent_content:
                continue
            metadata = dict(doc.metadata or {})
            metadata["parent_content"] = parent_content
            metadata.update(
                {
                    "document_part_index": document_part_index,
                    "parent_index": global_parent_index,
                    "child_index": 0,
                    "chunk_order": global_chunk_order,
                }
            )
            parent_id, chunk_id = chunk_identity(parent_content, metadata)
            metadata.update(
                {
                    "parent_id": parent_id,
                    "chunk_id": chunk_id,
                }
            )
            chunks.append(Document(page_content=parent_content, metadata=metadata))
            ids.append(chunk_id)
            global_parent_index += 1
            global_chunk_order += 1
            continue
        elif file_type == ".md":

            # 不再按 Markdown 标题预切分：标题元数据（h1/h2/h3）全项目未消费，
            # 而短章节会产生大量几字到几十字的碎块，直接按父块尺寸递归切分更均匀。




            header_docs = [doc]
            for header_doc in header_docs:
                # header_docs 现在就是原文档本身，这里回填 metadata 只是保留原结构

                header_doc.metadata.update(doc.metadata)
            parent_docs = parent_splitter.split_documents(header_docs)
        else:
            parent_docs = parent_splitter.split_documents([doc])

        for parent_doc in parent_docs:
            parent_content = parent_doc.page_content
            # parent_id 和 chunk_id 都纳入 kb_version、embedding_model_version 和 chunk_schema_version。
            # 这样同一个文件在两个知识库版本里可以同时存在，不会因为内容相同而主键冲突。
            child_docs = child_splitter.split_documents([parent_doc])
            for child_index, child_doc in enumerate(child_docs):
                # chunk_id 由父块和子块内容共同决定。同一文件未变化时 id 稳定；文件变化时
                # id 会变化，配合 manifest 删除旧 chunk 后重建。
                metadata = dict(child_doc.metadata or {})
                metadata["parent_content"] = parent_content
                metadata.update(
                    {
                        "document_part_index": document_part_index,
                        "parent_index": global_parent_index,
                        "child_index": child_index,
                        "chunk_order": global_chunk_order,
                    }
                )
                parent_id, chunk_id = chunk_identity(child_doc.page_content, metadata)
                metadata.update(
                    {
                        "parent_id": parent_id,
                        "chunk_id": chunk_id,
                    }
                )
                chunks.append(Document(page_content=child_doc.page_content, metadata=metadata))
                ids.append(chunk_id)
                global_chunk_order += 1
            global_parent_index += 1
    return chunks, ids


