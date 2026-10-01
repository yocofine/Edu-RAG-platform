"""为已有话术/规则补建或刷新向量索引。

背景：话术库与规则通知现在分别写入独立的 Milvus 集合（板块级隔离），
检索时由智能搜索的板块选择器切换。历史上它们未参与向量检索，因此需要
一次性把存量条目补进索引；本脚本是幂等 upsert，可重复执行。

用法（在 api 容器内执行，需要 MySQL + Milvus 可用）：

    docker compose run --rm api python scripts/reindex_content_items.py
    docker compose run --rm api python scripts/reindex_content_items.py --kind scripts
"""

from __future__ import annotations

import argparse
from pathlib import Path
from sys import path as sys_path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys_path.insert(0, str(ROOT))

from backend.app.db import SessionLocal
from backend.app.models import RuleItem, ScriptItem
from backend.app.services.content_index import index_content_item

MODELS = {"scripts": ScriptItem, "rules": RuleItem}


def pending_ids(kind: str) -> list[str]:
    """返回该板块未删除的条目主键，按更新时间升序。"""
    model = MODELS[kind]
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(model.id).where(model.deleted_at.is_(None)).order_by(model.updated_at)
            ).all()
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="补建话术/规则向量索引")
    parser.add_argument("--kind", choices=["all", "scripts", "rules"], default="all")
    args = parser.parse_args()

    kinds = list(MODELS) if args.kind == "all" else [args.kind]
    total = 0
    for kind in kinds:
        ids = pending_ids(kind)
        print(f"[{kind}] 待处理 {len(ids)} 条")
        for item_id in ids:
            index_content_item(kind, item_id)
            total += 1
            print(f"  ok {kind} {item_id}")
    print(f"完成：共处理 {total} 条")


if __name__ == "__main__":
    main()
