# -*- coding: utf-8 -*-
# ============================================================================
# 评测和性能脚本共享的样本解析工具
# ============================================================================
# 评测脚本（evaluate_core_chain.py、evaluate_followup_chain.py）、
# 性能基线脚本（collect_performance_baseline.py）都要从同一份 eval_set 里读取
# question、scenario、source、tenant、kb_version 等字段。
# 这里统一解析，避免两个脚本字段口径不一致。
#
# 本模块提供：
#   - EvalCaseRuntime: 一条评测样本在运行时需要传给 QAService 的公共参数
#   - load_eval_items(): 读取评测集并按 limit 截断
# ============================================================================

from __future__ import annotations

# argparse: 命令行参数命名空间（EvalCaseRuntime 从 args 中取默认值）
import argparse

# json: 标准 JSON 序列化（用于读取评测集 JSON）
import json

# time: 时间戳（用于生成唯一的 session_id）
import time

# dataclasses: 数据类定义
from dataclasses import dataclass

# pathlib.Path: 文件路径操作
from pathlib import Path

# typing.Any: 任意类型（用于评测集的 dict 类型注解）
from typing import Any


@dataclass(frozen=True)
class EvalCaseRuntime:
    """一条评测样本在运行时需要传给 QAService 的公共参数。

    字段来源优先级：
      - 评测 JSON 样本中的显式字段（如 source_filter、kb_version）优先
      - 命令行默认值（如 --scenario、--tenant-id）作为 fallback
      - session_id 自动生成（含时间戳和索引，避免多轮冲突）

    Attributes:
        case_id: 评测样本的唯一标识
        question: 用户问题
        scenario_id: 业务场景 ID
        source_filter: 数据源过滤
        tenant_id: 租户标识
        dataset_id: 数据集标识
        visibility: 文档可见性
        user_role: 用户角色
        kb_version: 知识库版本
        session_id: 会话 ID（自动生成，格式：{prefix}-{timestamp}-{index}）
    """

    case_id: str
    question: str
    scenario_id: str | None
    source_filter: str | None
    tenant_id: str | None
    dataset_id: str | None
    visibility: str | None
    user_role: str | None
    kb_version: str | None
    session_id: str

    @classmethod
    def from_item(
        cls,
        item: dict[str, Any],
        index: int,
        args: argparse.Namespace,
        *,
        session_prefix: str,
    ) -> "EvalCaseRuntime":
        """从评测 JSON 样本和命令行默认值中解析运行参数。

        解析规则：
          - question: 优先取 question 字段，fallback 到 query 字段
          - 各数据域字段：JSON 中的值优先，没有则用命令行默认值
          - session_id: 自动生成，格式为 {prefix}-{timestamp}-{index}

        Args:
            item: 评测集 JSON 中的一条样本
            index: 样本序号（从 1 开始）
            args: 命令行参数命名空间（提供默认值）
            session_prefix: session_id 前缀（"eval" / "perf" / "kb-compare" 等）

        Returns:
            EvalCaseRuntime 实例
        """
        question = str(item.get("question") or item.get("query") or "").strip()
        return cls(
            case_id=str(item.get("case_id") or f"case_{index}"),
            question=question,
            scenario_id=item.get("scenario_id") or args.scenario,
            source_filter=item.get("source_filter"),
            tenant_id=item.get("tenant_id") or args.tenant_id,
            dataset_id=item.get("dataset_id") or args.dataset_id,
            visibility=item.get("visibility") or args.visibility,
            user_role=item.get("user_role") or args.user_role,
            kb_version=item.get("kb_version") or args.kb_version,
            session_id=f"{session_prefix}-{int(time.time())}-{index}",
        )

    def service_kwargs(self) -> dict[str, Any]:
        """返回 QAService stream/debug 共用的关键字参数。

        将数据域字段打包为 dict，可直接解包传给
        QAService.stream_query() 或 QAService.debug_retrieval()。
        不包含 question 和 source_filter（这两个是位置参数）。
        """
        return {
            "kb_version": self.kb_version,
            "scenario_id": self.scenario_id,
            "tenant_id": self.tenant_id,
            "dataset_id": self.dataset_id,
            "visibility": self.visibility,
            "user_role": self.user_role,
        }


def load_eval_items(dataset: str | Path, limit: int) -> list[dict[str, Any]]:
    """读取评测集 JSON 文件并按 limit 截断。

    Args:
        dataset: 评测集 JSON 文件路径
        limit: 最多读取的样本数

    Returns:
        样本 dict 列表（长度为 min(文件中的样本数, limit)）
    """
    data = json.loads(Path(dataset).read_text(encoding="utf-8"))
    return list(data[:limit])
