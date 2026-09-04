"""Загрузка боевых платёжных реквизитов из таблицы.

Отдельно от демо-данных намеренно. Боевые номера карт и кошельков не должны
попадать ни в сид, ни в репозиторий: файл лежит в `secrets/`, эта папка
в `.gitignore`.

Запуск (папка `secrets/` в корне проекта смонтирована в контейнер как `/secrets:ro`):
    docker compose exec -T api python -m app.seed.load_requisites /secrets/<файл>.xlsx

Скрипт идемпотентный: реквизит опознаётся по четвёрке «страна + способ + тип +
номер», повторный запуск обновляет запись, а не создаёт вторую.
"""

import asyncio
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import PaymentRequisite

# Порядок направлений в списке у менеджера: сначала то, чем платят чаще.
COUNTRY_ORDER = ["РФ", "Казахстан", "Белоруссия", "Украина", "Европа", "Крипта"]


def read_rows(path: Path) -> list[dict[str, str]]:
    """Разбор xlsx без внешних библиотек: это zip с XML внутри."""
    with zipfile.ZipFile(path) as book:
        shared = re.findall(
            r"<t[^>]*>(.*?)</t>", book.read("xl/sharedStrings.xml").decode("utf-8"), re.S
        )
        sheet = book.read("xl/worksheets/sheet1.xml").decode("utf-8")

    def value(cell: str) -> str:
        raw = re.search(r"<v>(.*?)</v>", cell, re.S)
        if not raw:
            return ""
        is_shared = re.search(r't="s"', cell)
        return shared[int(raw.group(1))] if is_shared else raw.group(1)

    def column(cell: str) -> str:
        return re.search(r'r="([A-Z]+)', cell).group(1)

    header = ["country", "method", "kind", "number", "holder", "iban", "bic"]
    letters = ["A", "B", "C", "D", "E", "F", "G"]
    rows: list[dict[str, str]] = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells = re.findall(r"<c[^>]*/>|<c[^>]*>.*?</c>", row, re.S)
        values = {column(c): value(c).strip() for c in cells}
        record = {key: values.get(letter, "") for key, letter in zip(header, letters, strict=True)}
        if record["country"] in ("", "Страна"):
            continue
        rows.append(record)
    return rows


def mask(number: str) -> str:
    """В списке показывается хвост, целиком номер уходит только в счёт клиенту."""
    digits = number.replace(" ", "")
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else digits


def build_title(row: dict[str, str]) -> str:
    parts = [row["country"], row["method"]]
    if row["kind"]:
        parts.append(row["kind"].lower())
    return " · ".join(p for p in parts if p)


def build_details(row: dict[str, str]) -> str:
    """Текст, который уходит клиенту. Ровно то, что нужно для перевода."""
    lines = [f"{row['method']} — {row['kind']}" if row["kind"] else row["method"]]
    if row["holder"]:
        lines.append(f"Получатель: {row['holder']}")
    if row["number"]:
        label = "Адрес кошелька" if row["country"] == "Крипта" else "Номер"
        lines.append(f"{label}: {row['number']}")
    if row["iban"]:
        lines.append(f"IBAN: {row['iban']}")
    if row["bic"]:
        lines.append(f"BIC: {row['bic']}")
    if row["country"] != "Крипта":
        lines.append("В комментарии к переводу укажите код платежа")
    return "\n".join(lines)


async def load(path: Path) -> tuple[int, int]:
    rows = read_rows(path)
    added = updated = 0
    async with SessionLocal() as db:
        for index, row in enumerate(rows):
            country_rank = (
                COUNTRY_ORDER.index(row["country"]) if row["country"] in COUNTRY_ORDER else 99
            )
            fields: dict[str, Any] = {
                "title": build_title(row),
                "bank_name": row["method"] or None,
                "account_masked": mask(row["number"]) if row["number"] else None,
                "country": row["country"],
                "method": row["method"] or None,
                "kind": row["kind"] or None,
                "number": row["number"] or None,
                "holder": row["holder"] or None,
                "iban": row["iban"] or None,
                "bic": row["bic"] or None,
                "details_text": build_details(row),
                "sort_order": country_rank * 100 + index,
                "is_active": True,
            }
            # Опознаём по четвёрке «страна + способ + тип + номер». Тип обязателен:
            # у Kaspi есть и карта, и QR — без него QR затирал карту того же банка.
            existing = await db.scalar(
                select(PaymentRequisite).where(
                    PaymentRequisite.country == row["country"],
                    PaymentRequisite.method == (row["method"] or None),
                    PaymentRequisite.kind == (row["kind"] or None),
                    PaymentRequisite.number == (row["number"] or None),
                )
            )
            if existing is None:
                db.add(PaymentRequisite(**fields))
                added += 1
            else:
                for name, value in fields.items():
                    setattr(existing, name, value)
                updated += 1
        await db.commit()
    return added, updated


def main() -> int:
    if len(sys.argv) < 2:
        print("Укажите путь к файлу: python -m app.seed.load_requisites <файл.xlsx>")
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 2
    added, updated = asyncio.run(load(path))
    print(f"Реквизиты загружены: добавлено {added}, обновлено {updated}.")
    print("Проверьте список в разделе «Настройки» перед первой отправкой счёта.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
