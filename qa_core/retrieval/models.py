"""检索链路模型加载器，集中管理 embedding 和 CrossEncoder。
进程级重资源使用 lru_cache 缓存，不缓存用户级结果。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

# 防止 transformers 将 SentencePiece 转 Tiktoken 失败导致崩溃，需在导入前设置
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder

from qa_core.config.logging_config import get_logger
from qa_core.config.settings import get_settings


logger = get_logger(__name__)


def resolve_device() -> str:
    """选择本机可用推理设备（CUDA > CPU），CPU 是正常执行设备而非降级方案。

    BGE embedding 和 CrossEncoder 在典型批大小下 CPU 推理延迟完全可接受，
    所以 CPU 是一等执行设备；CUDA 只是可有可无的加速，不是必要条件。
    这样设计避免了对 GPU 环境的硬依赖，降低部署门槛。
    """
    return "cuda" if torch.cuda.is_available() else "cpu"


@lru_cache(maxsize=1)
def get_embeddings():
    """返回已缓存的 BGE 向量模型，用于 Milvus 稠密向量检索。

    模型加载涉及从磁盘读取权重文件到 GPU/CPU 内存（数百 MB），开销巨大。
    lru_cache 保证整个进程生命周期只加载一次，所有请求共享同一个模型实例。
    """
    # 加载应用全局设置（embedding 模型路径等配置）
    settings = get_settings()
    model_path = Path(settings.embedding_model_path)
    if not model_path.exists():
        raise RuntimeError(f"Embedding model path does not exist: {model_path}")
    # 创建 BGE HuggingFaceEmbeddings 实例，用于生成稠密向量
    return HuggingFaceEmbeddings(
        model_name=str(model_path),
        # 自动选择 CUDA 或 CPU 作为推理设备
        model_kwargs={"device": resolve_device(), "local_files_only": True},
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache(maxsize=1)
def _get_local_reranker():
    """返回已缓存的 CrossEncoder 重排模型，用于 Milvus 召回后的二阶段精细排序。

    与 get_embeddings 同理：CrossEncoder 权重文件通常在 1GB 以上，加载是进程级
    重操作。lru_cache 确保只加载一次，所有检索请求复用同一个重排模型实例。
    """
    # 加载应用全局设置（reranker 模型路径等配置）
    settings = get_settings()
    model_path = Path(settings.reranker_model_path)
    if not model_path.exists():
        raise RuntimeError(f"Reranker model path does not exist: {model_path}")
    vocab_file = model_path / "sentencepiece.bpe.model"
    if not vocab_file.exists():
        raise RuntimeError(f"Reranker tokenizer vocab file does not exist: {vocab_file}")
    # 创建 CrossEncoder 重排模型实例，用于 Milvus 召回后的二阶段精细排序
    return CrossEncoder(
        str(model_path),
        # 自动选择 CUDA 或 CPU 作为推理设备
        device=resolve_device(),
        local_files_only=True,
        tokenizer_kwargs={
            "use_fast": False,
            # 新版 transformers 读 tokenizer_config.json 相对路径可能拼错，显式传完整路径
            "vocab_file": str(vocab_file),
        },
    )


class ApiReranker:
    """调用远端 rerank API 的重排器。

    对外暴露与本地 ``sentence_transformers.CrossEncoder`` 一致的 ``predict(pairs)`` 接口，
    因此 ``rerank_hits()`` 无需感知后端差异，直接替换即可。

    设计要点：
      * ``rerank_hits`` 传入的 pairs 共用同一个 query，所以一次请求就能完成整批打分；
      * 远端返回的是「按分数降序」的结果，必须用返回项里的 index 还原到入参顺序，
        否则分数会和候选错位（这是最容易写错的地方）；
      * 远端失败时可回退本地 CrossEncoder，本地模型按需懒加载，不在启动时白占内存。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        fallback_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._fallback_factory = fallback_factory
        self._fallback_model: Any = None

    @property
    def endpoint(self) -> str:
        """DashScope 原生 rerank 接口地址（注意不是 OpenAI 兼容模式的 /compatible-mode）。"""
        return f"{self.base_url}/api/v1/services/rerank/text-rerank/text-rerank"

    def _get_fallback(self) -> Any:
        """懒加载本地回退模型：只有真的需要降级时才把 1GB+ 的权重读进内存。"""
        if self._fallback_model is None and self._fallback_factory is not None:
            self._fallback_model = self._fallback_factory()
        return self._fallback_model

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        """为若干 (query, passage) 对打分，返回顺序与入参严格一致。

        参数：
            pairs: [(query, passage), ...]，通常共用同一个 query。

        返回：
            与 pairs 等长的相关性分数列表。

        异常：
            RuntimeError: 远端调用失败且未配置本地回退。
        """
        pairs = list(pairs)
        if not pairs:
            return []
        queries = {str(pair[0]) for pair in pairs}
        documents = [str(pair[1] or "") for pair in pairs]
        try:
            if len(queries) != 1:
                # 多 query 场景（当前链路不会出现）远端一次请求无法覆盖，直接走本地
                raise RuntimeError(f"远端重排要求同一批次共用同一个 query，实际收到 {len(queries)} 个")
            return self._predict_remote(str(pairs[0][0]), documents)
        except Exception as exc:  # noqa: BLE001
            fallback = self._get_fallback()
            if fallback is None:
                raise RuntimeError(f"远端重排失败且未启用本地回退：{exc}") from exc
            logger.warning("远端重排失败（%s: %s），已回退本地 CrossEncoder。", type(exc).__name__, exc)
            return fallback.predict(pairs)

    def _predict_remote(self, query: str, documents: list[str]) -> list[float]:
        """调用一次远端 rerank 接口并把结果还原成入参顺序。"""
        payload = {
            "model": self.model,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": len(documents)},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        results = (body.get("output") or {}).get("results") or []
        if not results:
            raise RuntimeError(f"远端重排返回空结果：{str(body)[:200]}")
        scores = [0.0] * len(documents)
        for item in results:
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(scores):
                scores[index] = float(item.get("relevance_score") or 0.0)
        return scores


@lru_cache(maxsize=2)
def get_reranker():
    """按配置返回重排器：本地 CrossEncoder 或远端 rerank API 客户端。

    返回：
        实现了 predict(pairs) 的重排器实例，可直接交给 rerank_hits() 使用。

    异常：
        RuntimeError: 配置为 api 但未提供 RERANK_API_KEY。
    """
    settings = get_settings()
    backend = str(getattr(settings, "rerank_backend", "") or "local").strip().lower()
    if backend != "api":
        return _get_local_reranker()
    api_key = str(getattr(settings, "rerank_api_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("RERANK_BACKEND=api 但未配置 RERANK_API_KEY，无法调用远端重排。")
    fallback_factory = _get_local_reranker if getattr(settings, "rerank_api_fallback_local", True) else None
    logger.info(
        "重排后端已切换为远端 API：model=%s endpoint=%s 本地回退=%s",
        getattr(settings, "rerank_api_model", ""),
        getattr(settings, "rerank_api_base_url", ""),
        bool(fallback_factory),
    )
    return ApiReranker(
        base_url=str(getattr(settings, "rerank_api_base_url", "https://dashscope.aliyuncs.com")),
        api_key=api_key,
        model=str(getattr(settings, "rerank_api_model", "qwen3.7-text-rerank")),
        timeout=float(getattr(settings, "rerank_api_timeout", 30.0) or 30.0),
        fallback_factory=fallback_factory,
    )
