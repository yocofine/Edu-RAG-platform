"""MySQL 存储基类：延迟创建 SQLAlchemy 引擎。"""
from __future__ import annotations

import re

from sqlalchemy import create_engine, text

from qa_core.config.settings import get_settings


_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_sql_identifier(value: str, *, label: str = "SQL identifier") -> str:
    """校验可拼接到 SQL 中的表名/索引名，避免配置项注入 SQL。"""
    if not _SQL_IDENTIFIER_RE.fullmatch(value or ""):
        raise ValueError(f"{label} 不合法：{value!r}")
    return value

class _MySqlStore:
    """MySQL 存储的轻量基类。

    基类统一加载项目配置并延迟创建 SQLAlchemy 引擎，子类只需要关心自己的表名和业务方法。
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._engine = None

    @property
    def engine(self):
        """延迟创建带连接健康检查的 SQLAlchemy 同步引擎。"""
        if self._engine is None:
            # 延迟创建 SQLAlchemy 引擎（带连接健康检查）
            self._engine = create_engine(self.settings.mysql_sync_uri, pool_pre_ping=True)
        return self._engine

    def _execute_ddl(self, sql: str) -> None:
        """在隐式事务中执行 DDL 语句，例如 CREATE TABLE。"""
        with self.engine.begin() as conn:
            # 在隐式事务中执行 DDL
            conn.execute(text(sql))
