"""медиа, пересылка, справочник услуг

Одна миграция на доработки 27.09.2026 (docs/14-release-plan-2026-09-27.md, §10).
Только расширяющая: новые колонки допускают NULL или имеют значение по умолчанию,
ничего не удаляется и не переименовывается. Поэтому откат кода на прошлую версию
после выкатки безопасен — старый код новых колонок просто не видит.

- messages: `meta` (переслано от, контакт, геопозиция, «удалено в Telegram»),
  `tg_extra_ids` (все номера Telegram одного сообщения CRM — альбомы);
- тип message_kind: + `audio`, `sticker` — про запас, этот релиз их не пишет:
  предыдущая версия таких значений не знает, и откат на неё уронил бы чаты с
  ними. Точный вид медиа — в `attachments.kind`;
- attachments: `kind`, `waveform`, `thumb_key`, `status`, `meta`; `storage_key`
  может быть пустым (файл докачивается или слишком велик для CRM);
- services: справочник услуг;
- deal_items: `service_id`, `list_price` — связь со справочником и цена по прайсу
  на момент продажи.

Значения типа Postgres назад не удаляются: ALTER TYPE ... DROP VALUE в Postgres
нет, а пересоздание типа ради отката — риск без пользы (лишние значения ничему
не мешают).

Revision ID: 9448414132fb
Revises: a4f7c8e21d90
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9448414132fb"
down_revision: str | None = "a4f7c8e21d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE — в отдельном блоке с автокоммитом: так значение
    # фиксируется сразу и не зависит от судьбы остальной миграции.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE message_kind ADD VALUE IF NOT EXISTS 'audio'")
        op.execute("ALTER TYPE message_kind ADD VALUE IF NOT EXISTS 'sticker'")

    op.add_column("messages", sa.Column("meta", postgresql.JSONB(), nullable=True))
    op.add_column(
        "messages", sa.Column("tg_extra_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True)
    )

    op.create_index(
        "ix_messages_tg_message_id",
        "messages",
        ["tg_message_id"],
        postgresql_where=sa.text("tg_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_messages_tg_extra_ids",
        "messages",
        ["tg_extra_ids"],
        postgresql_using="gin",
        postgresql_where=sa.text("tg_extra_ids IS NOT NULL"),
    )

    op.add_column("attachments", sa.Column("kind", sa.String(20), nullable=True))
    op.add_column("attachments", sa.Column("waveform", sa.LargeBinary(), nullable=True))
    op.add_column("attachments", sa.Column("thumb_key", sa.String(500), nullable=True))
    op.add_column(
        "attachments",
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
    )
    op.add_column("attachments", sa.Column("meta", postgresql.JSONB(), nullable=True))
    op.alter_column("attachments", "storage_key", existing_type=sa.String(500), nullable=True)
    op.create_index(
        "ix_attachments_pending",
        "attachments",
        ["status"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # Вид старых вложений — по типу файла. Голосовым считаем только Ogg из
    # голосового сообщения: mp3, отправленный раньше из CRM, тоже числился
    # «голосовым» (тип audio/*), хотя клиенту ушёл обычным файлом.
    op.execute(
        """
        update attachments a set kind = case
            when a.mime_type like 'image/%' then 'photo'
            when a.mime_type like 'video/%' then 'video'
            when a.mime_type like 'audio/%' and m.kind = 'voice'
                 and (a.mime_type like 'audio/ogg%' or a.mime_type = 'audio/opus') then 'voice'
            when a.mime_type like 'audio/%' then 'audio'
            else 'document'
        end
        from messages m
        where m.id = a.message_id and a.kind is null
        """
    )
    # Вид самих сообщений не трогаем: mp3, числившийся «голосовым», в списке
    # чатов теперь подписан по виду вложения («Аудио»), а значение `audio` в
    # messages.kind прежняя версия прочитать не смогла бы (см. MessageKind).

    op.create_table(
        "services",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("price", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_services"),
    )
    op.create_index(
        "uq_services_name_alive",
        "services",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.add_column("deal_items", sa.Column("service_id", sa.BigInteger(), nullable=True))
    op.add_column("deal_items", sa.Column("list_price", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_deal_items_service_id",
        "deal_items",
        "services",
        ["service_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_deal_items_service_id", "deal_items", ["service_id"])


def downgrade() -> None:
    op.drop_index("ix_deal_items_service_id", table_name="deal_items")
    op.drop_constraint("fk_deal_items_service_id", "deal_items", type_="foreignkey")
    op.drop_column("deal_items", "list_price")
    op.drop_column("deal_items", "service_id")

    op.drop_index("uq_services_name_alive", table_name="services")
    op.drop_table("services")

    op.execute("update messages set kind = 'voice' where kind = 'audio'")
    op.execute("update messages set kind = 'document' where kind = 'sticker'")

    op.drop_index("ix_attachments_pending", table_name="attachments")
    # Вложения без файла (докачка не завершилась, файл больше лимита) прежняя
    # схема хранить не умеет — у неё storage_key обязателен.
    op.execute("delete from attachments where storage_key is null")
    op.alter_column("attachments", "storage_key", existing_type=sa.String(500), nullable=False)
    op.drop_column("attachments", "meta")
    op.drop_column("attachments", "status")
    op.drop_column("attachments", "thumb_key")
    op.drop_column("attachments", "waveform")
    op.drop_column("attachments", "kind")

    op.drop_index("ix_messages_tg_extra_ids", table_name="messages")
    op.drop_index("ix_messages_tg_message_id", table_name="messages")
    op.drop_column("messages", "tg_extra_ids")
    op.drop_column("messages", "meta")
