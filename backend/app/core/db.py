from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings
from app.gateway import topology

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PKMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


# Порог, ниже которого не опускаем пул на отдельный процесс шлюза, даже если
# бюджет, поделённый на число процессов, вышел бы меньше: нескольким
# одновременным запросам внутри одного процесса (аренда + разбор outbox
# нескольких аккаунтов параллельно) нужно хотя бы это, иначе они бы просто
# стояли в очереди друг за другом за место в пуле.
_MIN_GATEWAY_POOL_PER_WORKER = 2


def _pool_kwargs() -> dict[str, int]:
    """api/scheduler — как задано в настройках, процесс всегда один.

    gateway — контейнер поднимает несколько процессов (`topology.worker_count`,
    зависит от числа ядер), и каждый из них здесь же, при своём импорте этого
    модуля, строит СВОЙ движок с собственным пулом. Без деления апгрейд
    сервера (больше ядер → больше процессов) незаметно умножал бы суммарное
    число соединений на N, вплоть до исчерпания лимита Postgres — настройка
    задавалась один раз под старое железо и никогда не пересчитывалась.
    Деление превращает `db_pool_size`/`db_max_overflow` в общий бюджет на
    контейнер, а не бюджет на процесс: подставили сервер с другим числом
    ядер — бюджет остался прежним, просто иначе поделился.
    """
    if settings.service_role != "gateway":
        return {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow}
    workers = topology.worker_count()
    return {
        "pool_size": max(_MIN_GATEWAY_POOL_PER_WORKER, settings.db_pool_size // workers),
        "max_overflow": max(_MIN_GATEWAY_POOL_PER_WORKER, settings.db_max_overflow // workers),
    }


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    **_pool_kwargs(),
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
