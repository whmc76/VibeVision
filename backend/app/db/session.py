from collections.abc import Generator
from secrets import token_hex

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


if is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_db_and_tables() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()


def _ensure_sqlite_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    required_columns_by_table = {
        "generation_tasks": {
            "public_id": "VARCHAR(24)",
            "telegram_chat_id": "VARCHAR(64)",
            "telegram_message_id": "VARCHAR(64)",
            "bonus_credit_cost": "INTEGER NOT NULL DEFAULT 0",
            "paid_credit_cost": "INTEGER NOT NULL DEFAULT 0",
        },
        "users": {
            "is_admin": "BOOLEAN NOT NULL DEFAULT 0",
            "is_hidden": "BOOLEAN NOT NULL DEFAULT 0",
            "daily_bonus_balance": "INTEGER NOT NULL DEFAULT 0",
            "daily_bonus_allowance": "INTEGER NOT NULL DEFAULT 0",
            "daily_bonus_granted_on": "DATE",
            "subscription_plan": "VARCHAR(64)",
            "subscription_expires_at": "DATETIME",
            "total_recharge_usd_cents": "INTEGER NOT NULL DEFAULT 0",
        },
    }

    with engine.begin() as connection:
        for table_name, required_columns in required_columns_by_table.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in required_columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                    )
        if "generation_tasks" in existing_tables:
            used_public_ids = {
                row[0]
                for row in connection.execute(
                    text("SELECT public_id FROM generation_tasks WHERE public_id IS NOT NULL")
                )
            }
            for (task_id,) in connection.execute(
                text("SELECT id FROM generation_tasks WHERE public_id IS NULL")
            ):
                public_id = token_hex(6)
                while public_id in used_public_ids:
                    public_id = token_hex(6)
                used_public_ids.add(public_id)
                connection.execute(
                    text("UPDATE generation_tasks SET public_id = :public_id WHERE id = :id"),
                    {"public_id": public_id, "id": task_id},
                )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_generation_tasks_public_id "
                    "ON generation_tasks (public_id)"
                )
            )
