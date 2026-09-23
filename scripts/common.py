"""脚本层公共工具。

`scripts/` 里的文件越来越多，如果每个脚本都重复处理 JSON 读写、UTF-8 输出、命令执行
和报告保存，阅读时会被样板代码淹没。本模块只放“脚本基础设施”，不放 RAG 业务
规则；业务规则仍留在各自脚本里。
"""

from __future__ import annotations

# json: 标准 JSON 序列化
import json

# os: 操作系统接口（子进程环境变量继承）
import os

# subprocess: 子进程管理（subprocess.run 执行外部命令）
import subprocess

# sys: 系统功能（sys.stdout.reconfigure 设置 UTF-8 编码）
import sys

# time: 时间功能（time.perf_counter 高精度计时）
import time

# dataclasses: 数据类定义（CommandStepResult）
from dataclasses import dataclass

# datetime: 日期时间（UTC 时间戳）
from datetime import datetime, timezone

# pathlib.Path: 文件路径操作
from pathlib import Path

# typing.Any: 任意类型
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
@dataclass(frozen=True)
class CommandStepResult:
    """一条命令式验收步骤的执行结果。"""

    name: str
    command: list[str]
    ok: bool
    elapsed_ms: float
    stdout_preview: str
    stderr_preview: str
    returncode: int


def configure_utf8_stdio() -> None:
    """把脚本标准输出统一成 UTF-8。

    Windows PowerShell 的默认编码可能不是 UTF-8，一键验收里又会输出中文 JSON。这里集中
    处理，避免每个脚本单独写一遍编码保护。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def read_json_file(path: str | Path) -> dict[str, Any]:
    """读取 JSON 文件并返回对象。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def utc_now() -> str:
    """返回 UTC 时间字符串。

    发布、验收和质量报告都用这个函数生成时间，避免每个脚本重复导入 datetime，也避免
    一部分报告使用本地时间、一部分报告使用 UTC。
    """
    return datetime.now(timezone.utc).isoformat()


def write_json_file(path: str | Path, payload: dict[str, Any]) -> str:
    """把对象写成中文友好的 JSON 文件，并返回写入路径。"""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(output_path)


def print_json(payload: dict[str, Any]) -> None:
    """按统一格式打印 JSON。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def write_optional_json(path: str | Path | None, payload: dict[str, Any]) -> None:
    """当调用方提供路径时写 JSON；未提供时什么都不做。"""
    if path:
        write_json_file(path, payload)


def preview_text(text: str, limit: int = 1200) -> str:
    """截断长输出，避免验收报告被命令日志撑爆。"""
    compact = (text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "\n..."


def run_command_step(name: str, command: list[str], *, preview_limit: int = 1200) -> CommandStepResult:
    """运行一个验收命令并记录结果。

    这里不用 shell 拼接命令，是为了减少 Windows 下路径、引号和转义问题。每个步骤独立
    执行，某一步失败后仍继续跑后续步骤，最后统一汇总失败项。
    """
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return CommandStepResult(
        name=name,
        command=command,
        ok=completed.returncode == 0,
        elapsed_ms=elapsed_ms,
        stdout_preview=preview_text(completed.stdout, preview_limit),
        stderr_preview=preview_text(completed.stderr, preview_limit),
        returncode=completed.returncode,
    )
