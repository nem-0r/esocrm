"""Одно сопоставление поискового запроса с клиентом (D-35).

Раньше в глобальном поиске, в разделе «Клиенты» и в списке чатов были три
разных набора полей и три разных способа сравнения. Из-за этого «Сергеева»
не находилась нигде (у всех клиентов пусто `display_name`, а имя из Telegram
лежит в `tg_first_name` / `tg_last_name`), а «Ксения Сергеева» не находилась
даже там, где имя вписано полностью.

Правила, одинаковые везде:

* запрос приводится к нижнему регистру, «ё» считается «е» (и в запросе, и в
  сравниваемых полях), пробелы схлопываются, ведущий «@» снимается;
* запрос делится на слова, и **каждое слово должно найтись** (И) хотя бы в
  одном из полей: `display_name`, `tg_first_name`, `tg_last_name`,
  `tg_username`, телефон, числовой id клиента. Порядок слов не важен;
* телефон сравнивается по цифрам: слово из трёх и более цифр ищется внутри
  цифровой записи номера, как бы тот ни был отформатирован;
* числа подряд («+7 958 142-61-76») склеиваются в один номер;
* id клиента — точное совпадение (до десяти цифр), чтобы «84271» не находило
  сорок клиентов, у которых эти цифры где-то внутри номера;
* `%`, `_` и `\\` в запросе — обычные символы, а не шаблон.

Модуль отдаёт условие в двух видах: для ORM (`client_match_orm`) и текстом SQL
с параметрами (`client_match_sql`, `text_match_sql`) — глобальный поиск написан
сырым SQL. Подстановки пользовательского ввода в текст SQL нет: в строку идут
только имена колонок и параметров, значения — отдельными параметрами.

Транслитерация («Ксения» ↔ «Ksenia») сюда не входит — это отдельная функция.
"""

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, func, literal_column, or_

from app.models import Client

MAX_QUERY_LENGTH = 255
MAX_TERMS = 8
MAX_WORD_LENGTH = 64
PHONE_MIN_DIGITS = 3
ID_MAX_DIGITS = 10
TELEGRAM_ID_MIN_DIGITS = 6
TELEGRAM_ID_MAX_DIGITS = 18

# Поля клиента, где ищется слово как подстрока. Телефон и id — отдельно.
CLIENT_TEXT_COLUMNS = ("display_name", "tg_first_name", "tg_last_name", "tg_username")

# Слово, состоящее из цифр и телефонной пунктуации и содержащее хоть одну цифру.
_PHONE_WORD = re.compile(r"[+()\-.\d]*\d[+()\-.\d]*")


@dataclass(frozen=True)
class Term:
    """Одно слово запроса и то, чем его можно искать помимо подстроки в имени."""

    text: str
    # Цифры телефона: есть, если слово — номер или его часть из 3+ цифр.
    digits: str | None = None
    # Точный id клиента (до 10 цифр).
    ident: int | None = None
    # Точный telegram_id: у клиента без имени и @username именно он показан как имя.
    tg_id: int | None = None


def normalize(value: str | None) -> str:
    """Нижний регистр, «ё» → «е», один пробел между словами."""
    if not value:
        return ""
    folded = unicodedata.normalize("NFC", value).lower().replace("ё", "е")
    return " ".join(folded.split())


def _number_term(digits: str) -> Term:
    digits = digits[:MAX_WORD_LENGTH]
    canonical = str(int(digits)) == digits  # «007» — не id клиента 7
    return Term(
        text=digits,
        digits=digits if len(digits) >= PHONE_MIN_DIGITS else None,
        ident=int(digits) if canonical and len(digits) <= ID_MAX_DIGITS else None,
        tg_id=(
            int(digits)
            if canonical and TELEGRAM_ID_MIN_DIGITS <= len(digits) <= TELEGRAM_ID_MAX_DIGITS
            else None
        ),
    )


def parse(query: str | None) -> list[Term]:
    """Запрос → слова. Пустой список, если искать нечего."""
    words = [w.lstrip("@") for w in normalize((query or "")[:MAX_QUERY_LENGTH]).split(" ")]
    words = [w for w in words if any(ch.isalnum() for ch in w)]

    terms: list[Term] = []
    number = ""  # подряд идущие «телефонные» слова склеиваются в один номер

    def flush() -> None:
        nonlocal number
        if number:
            terms.append(_number_term(number))
            number = ""

    for word in words:
        if _PHONE_WORD.fullmatch(word):
            number += re.sub(r"\D", "", word)
        else:
            flush()
            terms.append(Term(text=word[:MAX_WORD_LENGTH]))
    flush()

    unique = list(dict.fromkeys(terms))
    return unique[:MAX_TERMS]


def escape_like(value: str) -> str:
    """В шаблоне LIKE служебные `%`, `_` и `\\` (она же escape по умолчанию) — экранируем."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    return f"%{escape_like(value)}%"


# --- имя клиента для выдачи --------------------------------------------------


def client_name_sql(alias: str = "cl") -> str:
    """SQL-двойник `Client.name`: имя менеджера → «имя фамилия» из Telegram →
    @username → telegram_id. Результат поиска обязан показывать то же имя, что
    список клиентов и карточка, а не пустой `display_name`."""
    a = alias
    return (
        f"coalesce(nullif({a}.display_name, ''), "
        f"nullif(concat_ws(' ', nullif({a}.tg_first_name, ''), nullif({a}.tg_last_name, '')), ''), "
        f"'@' || nullif({a}.tg_username, ''), "
        f"cast({a}.telegram_id as text))"
    )


# --- SQL-фрагмент (для запросов на text()) ------------------------------------


def norm_sql(expr: str) -> str:
    """Приведение колонки к тому же виду, что и слово запроса."""
    return f"translate(lower(coalesce({expr}, '')), 'ё', 'е')"


def _digits_sql(alias: str) -> str:
    return rf"regexp_replace(coalesce({alias}.phone, ''), '\D', '', 'g')"


def _group_sql(  # noqa: PLR0913
    term: Term,
    index: int,
    *,
    alias: str | None,
    prefix: str,
    phone: bool,
    ids: bool,
    extra_text: Sequence[str],
    extra_exists: Sequence[Callable[[str], str]],
) -> tuple[str, dict[str, Any]]:
    like = f"{prefix}{index}t"
    params: dict[str, Any] = {like: like_contains(term.text)}
    columns = [f"{alias}.{name}" for name in CLIENT_TEXT_COLUMNS] if alias else []
    parts = [f"{norm_sql(expr)} like :{like}" for expr in (*columns, *extra_text)]
    parts += [build(f":{like}") for build in extra_exists]
    if alias and phone and term.digits:
        params[f"{prefix}{index}d"] = f"%{term.digits}%"
        parts.append(f"{_digits_sql(alias)} like :{prefix}{index}d")
    if alias and ids and term.ident is not None:
        params[f"{prefix}{index}i"] = term.ident
        parts.append(f"{alias}.id = :{prefix}{index}i")
    if alias and ids and term.tg_id is not None:
        params[f"{prefix}{index}g"] = term.tg_id
        parts.append(f"{alias}.telegram_id = :{prefix}{index}g")
    return "(" + " or ".join(parts) + ")", params


def client_match_sql(  # noqa: PLR0913
    query: str | None,
    alias: str = "cl",
    *,
    prefix: str = "nm",
    phone: bool = True,
    ids: bool = True,
    extra_text: Sequence[str] = (),
    extra_exists: Sequence[Callable[[str], str]] = (),
) -> tuple[str, dict[str, Any]] | None:
    """Условие «клиент подходит под запрос» для запроса на `text()`.

    `alias` — как клиент назван в запросе. `prefix` — приставка имён параметров,
    чтобы несколько условий жили в одном запросе. `extra_text` — другие колонки
    (имя файла), в которых слово тоже можно найти; `extra_exists` — функции,
    которым передают имя параметра `:pN` и которые возвращают SQL-условие
    (позиции сделки). None — искать нечего, условие не нужно.
    """
    terms = parse(query)
    if not terms:
        return None
    groups: list[str] = []
    params: dict[str, Any] = {}
    for index, term in enumerate(terms):
        group, group_params = _group_sql(
            term,
            index,
            alias=alias,
            prefix=prefix,
            phone=phone,
            ids=ids,
            extra_text=extra_text,
            extra_exists=extra_exists,
        )
        groups.append(group)
        params.update(group_params)
    return "(" + " and ".join(groups) + ")", params


def text_match_sql(
    query: str | None, columns: Sequence[str], *, prefix: str = "tm"
) -> tuple[str, dict[str, Any]] | None:
    """То же правило слов для произвольных текстовых колонок (сотрудники)."""
    terms = parse(query)
    if not terms:
        return None
    groups: list[str] = []
    params: dict[str, Any] = {}
    for index, term in enumerate(terms):
        group, group_params = _group_sql(
            term,
            index,
            alias=None,
            prefix=prefix,
            phone=False,
            ids=False,
            extra_text=columns,
            extra_exists=(),
        )
        groups.append(group)
        params.update(group_params)
    return "(" + " and ".join(groups) + ")", params


# --- ORM-условие -------------------------------------------------------------


def _norm_orm(column: Any) -> ColumnElement[str]:
    return func.translate(
        func.lower(func.coalesce(column, literal_column("''"))),
        literal_column("'ё'"),
        literal_column("'е'"),
    )


def client_match_orm(
    query: str | None, *, phone: bool = True, ids: bool = True
) -> ColumnElement[bool] | None:
    """То же условие для запросов на ORM (список клиентов, список чатов)."""
    terms = parse(query)
    if not terms:
        return None
    phone_digits = func.regexp_replace(
        func.coalesce(Client.phone, literal_column("''")),
        literal_column(r"'\D'"),
        literal_column("''"),
        literal_column("'g'"),
    )
    groups: list[ColumnElement[bool]] = []
    for term in terms:
        like = like_contains(term.text)
        options: list[ColumnElement[bool]] = [
            _norm_orm(getattr(Client, name)).like(like) for name in CLIENT_TEXT_COLUMNS
        ]
        if phone and term.digits:
            options.append(phone_digits.like(f"%{term.digits}%"))
        if ids and term.ident is not None:
            options.append(Client.id == term.ident)
        if ids and term.tg_id is not None:
            options.append(Client.telegram_id == term.tg_id)
        groups.append(or_(*options))
    return and_(*groups)
