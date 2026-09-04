import asyncio

from sqlalchemy import text

from app.core.db import SessionLocal


async def main() -> None:
    async with SessionLocal() as db:
        for ids in ([], [1, 2]):
            try:
                value = (
                    await db.execute(
                        text(
                            "select count(*) from conversations c "
                            "where 1=1 and c.account_id = any(:account_ids)"
                        ),
                        {"account_ids": ids},
                    )
                ).scalar_one()
                print(f"account_ids={ids!r} -> {value}")
            except Exception as exc:  # noqa: BLE001
                print(f"account_ids={ids!r} -> ОШИБКА {type(exc).__name__}: {exc}")


asyncio.run(main())
