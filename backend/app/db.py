from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate_content_group_uniqueness() -> None:
    """把旧的「全局分组名唯一」升级为「用户+类型+分组名唯一」。"""
    inspector = inspect(engine)
    if "content_groups" not in inspector.get_table_names():
        return
    constraints = {item.get("name") for item in inspector.get_unique_constraints("content_groups")}
    constraints.update(
        item.get("name") for item in inspector.get_indexes("content_groups") if item.get("unique")
    )
    old_name = "uq_content_group_kind_name"
    new_name = "uq_content_group_owner_kind_name"
    if old_name not in constraints and new_name in constraints:
        return
    dialect = engine.dialect.name
    with engine.begin() as connection:
        if old_name in constraints:
            if dialect in {"mysql", "mariadb"}:
                connection.execute(text(f"ALTER TABLE content_groups DROP INDEX {old_name}"))
            elif dialect == "postgresql":
                connection.execute(text(f"ALTER TABLE content_groups DROP CONSTRAINT {old_name}"))
        if new_name not in constraints and dialect in {"mysql", "mariadb", "postgresql"}:
            connection.execute(text(
                f"ALTER TABLE content_groups ADD CONSTRAINT {new_name} UNIQUE (owner_id, kind, name)"
            ))
