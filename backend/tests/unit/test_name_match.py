"""Единое сопоставление поискового запроса с клиентом (D-35).

Здесь — разбор запроса и сборка условий без базы. Что условия действительно
находят в Postgres, проверяет сквозной набор `tests/verify_search.py`.
"""

import pytest
from sqlalchemy.dialects import postgresql

from app.services import name_match as nm

# ------------------------------------------------------------ нормализация


def test_normalize_lowercases_folds_yo_and_collapses_spaces():
    assert nm.normalize("  Ёлка   СЕРГЕЕВА \t") == "елка сергеева"


def test_normalize_handles_none_and_empty():
    assert nm.normalize(None) == ""
    assert nm.normalize("   ") == ""


def test_normalize_composes_decomposed_unicode():
    # «й» из двух символов (и + краткая) — так иногда приходят имена из Telegram.
    assert nm.normalize("Ной") == nm.normalize("Ной")


# ------------------------------------------------------------ разбор запроса


def texts(query: str) -> list[str]:
    return [term.text for term in nm.parse(query)]


def test_parse_splits_into_words_and_drops_leading_at():
    assert texts("@Ksenia") == ["ksenia"]
    assert texts("Ксения   Сергеева") == ["ксения", "сергеева"]
    assert texts("Ксения @ksenia_s") == ["ксения", "ksenia_s"]


def test_parse_yo_in_query_equals_ye():
    assert texts("Ёж") == texts("еж") == ["еж"]


def test_parse_ignores_pure_punctuation_and_empty_query():
    assert nm.parse("") == []
    assert nm.parse("   ") == []
    assert nm.parse("@") == []
    assert nm.parse("@@ - ()") == []
    assert texts("Ксения - Сергеева") == ["ксения", "сергеева"]


def test_parse_removes_repeated_words_keeping_order():
    assert texts("анна Анна ИВАНОВА анна") == ["анна", "иванова"]


def test_parse_limits_words_and_word_length():
    many = " ".join(f"слово{i}" for i in range(30))
    assert len(nm.parse(many)) == nm.MAX_TERMS
    assert len(nm.parse("я" * 500)[0].text) == nm.MAX_WORD_LENGTH


def test_parse_word_order_does_not_matter_for_the_set_of_terms():
    assert {t.text for t in nm.parse("Сергеева Ксения")} == {
        t.text for t in nm.parse("Ксения Сергеева")
    }


# ------------------------------------------------------------ телефон и id


def test_digit_word_of_three_or_more_digits_is_a_phone_fragment():
    [term] = nm.parse("958")
    assert term.digits == "958"
    assert term.ident == 958


def test_short_digit_word_is_only_an_id_not_a_phone_fragment():
    [term] = nm.parse("12")
    assert term.digits is None
    assert term.ident == 12


def test_formatted_phone_becomes_one_digit_term():
    [term] = nm.parse("+7 (958) 142-61-76")
    assert term.digits == "79581426176"
    assert term.ident is None  # 11 цифр — это не id клиента
    [dashed] = nm.parse("142-61-76")
    assert dashed.digits == "1426176"


def test_phone_pieces_next_to_a_name_are_glued_together():
    terms = nm.parse("Мария +7 958")
    assert [t.text for t in terms][0] == "мария"
    assert terms[1].digits == "7958"
    assert len(terms) == 2


def test_id_is_exact_only_up_to_ten_digits():
    assert nm.parse("1234567890")[0].ident == 1234567890
    assert nm.parse("12345678901")[0].ident is None


def test_long_digit_word_is_also_a_telegram_id_candidate():
    assert nm.parse("123456")[0].tg_id == 123456
    assert nm.parse("12345")[0].tg_id is None
    assert nm.parse("1" * 19)[0].tg_id is None


def test_word_with_letters_and_digits_is_plain_text():
    [term] = nm.parse("user007")
    assert term.text == "user007"
    assert term.digits is None
    assert term.ident is None


# ------------------------------------------------------------ экранирование


def test_escape_like_escapes_wildcards_and_backslash():
    assert nm.escape_like("50%_off\\") == "50\\%\\_off\\\\"
    assert nm.like_contains("a%b") == "%a\\%b%"


def test_wildcards_in_query_are_data_not_patterns():
    [term] = nm.parse("100%")
    built = nm.client_match_sql("100%")
    assert built is not None
    sql, params = built
    assert params["nm0t"] == "%100\\%%"
    assert "100%" not in sql
    assert term.text == "100%"


# ------------------------------------------------------------ SQL-фрагмент


def test_sql_is_parameterized_and_never_inlines_user_text():
    evil = "x'); drop table clients; --"
    built = nm.client_match_sql(evil)
    assert built is not None
    sql, params = built
    assert "drop" not in sql
    assert "x'" not in sql
    assert any("drop" in str(v) for v in params.values())
    # Все кавычки в тексте — наши константы: пустая строка, ё/е, \D, g.
    stripped = sql.replace("''", "").replace("'ё'", "").replace("'е'", "")
    assert "'" not in stripped.replace("'\\D'", "").replace("'g'", "")


def test_sql_empty_query_gives_none():
    assert nm.client_match_sql("") is None
    assert nm.client_match_sql("  @ ") is None


def test_sql_every_word_gets_its_own_and_group():
    sql, params = nm.client_match_sql("Ксения Сергеева")  # type: ignore[misc]
    assert params["nm0t"] == "%ксения%"
    assert params["nm1t"] == "%сергеева%"
    assert sql.count(" and ") == 1  # две группы, склеенные «и»
    for column in ("display_name", "tg_first_name", "tg_last_name", "tg_username"):
        assert f"cl.{column}" in sql
    # Слово из букв не ищется ни в телефоне, ни по id.
    assert "regexp_replace" not in sql
    assert "cl.id" not in sql


def test_sql_word_order_gives_same_params_set():
    first = nm.client_match_sql("Сергеева Ксения")
    second = nm.client_match_sql("Ксения Сергеева")
    assert first and second
    assert sorted(first[1].values()) == sorted(second[1].values())


def test_sql_digits_use_phone_digits_and_exact_id():
    sql, params = nm.client_match_sql("958 142")  # type: ignore[misc]
    assert params["nm0d"] == "%958142%"  # «958 142» склеено в один номер
    assert "regexp_replace" in sql
    assert "cl.id = :nm0i" in sql


def test_sql_phone_and_id_can_be_switched_off():
    sql, params = nm.client_match_sql("958142", phone=False, ids=False)  # type: ignore[misc]
    assert "regexp_replace" not in sql
    assert "cl.id" not in sql
    assert "cl.telegram_id" not in sql
    assert not any(key.endswith(("d", "i", "g")) for key in params)


def test_sql_alias_prefix_and_extras():
    built = nm.client_match_sql(
        "разбор",
        "client",
        prefix="fm",
        extra_text=("at.file_name",),
        extra_exists=(lambda p: f"exists (select 1 from x where x.n like {p})",),  # noqa: S608
    )
    assert built is not None
    sql, params = built
    assert "client.display_name" in sql
    assert "at.file_name" in sql
    assert "exists (select 1 from x where x.n like :fm0t)" in sql
    assert params == {"fm0t": "%разбор%"}


def test_text_match_without_client():
    built = nm.text_match_sql("Марина Иванова", ("u.full_name", "u.email"), prefix="mg")
    assert built is not None
    sql, params = built
    assert "cl." not in sql
    assert "u.full_name" in sql and "u.email" in sql
    assert params == {"mg0t": "%марина%", "mg1t": "%иванова%"}


def test_client_name_ladder_sql_follows_model_ladder():
    sql = nm.client_name_sql("cl")
    assert sql.index("display_name") < sql.index("tg_first_name") < sql.index("tg_username")
    assert sql.index("tg_username") < sql.index("telegram_id")


# ------------------------------------------------------------ ORM-условие


def compiled(query: str, **kwargs) -> tuple[str, dict]:
    condition = nm.client_match_orm(query, **kwargs)
    assert condition is not None
    result = condition.compile(dialect=postgresql.dialect())
    return str(result), dict(result.params)


def test_orm_condition_covers_all_fields_and_is_and_of_words():
    sql, params = compiled("Ксения Сергеева")
    for column in ("display_name", "tg_first_name", "tg_last_name", "tg_username"):
        assert f"clients.{column}" in sql
    assert " AND " in sql
    values = list(params.values())
    assert "%ксения%" in values and "%сергеева%" in values


def test_orm_condition_yo_folded_and_escaped():
    _, params = compiled("Ёлка_100%")
    assert "%елка\\_100\\%%" in params.values()


def test_orm_empty_query_gives_none():
    assert nm.client_match_orm("") is None
    assert nm.client_match_orm(" @ ") is None


def test_orm_condition_phone_digits_and_id():
    sql, params = compiled("958142")
    assert "regexp_replace" in sql
    assert "clients.id" in sql
    assert 958142 in params.values()


@pytest.mark.parametrize("query", ["Ксения", "@ksenia", "Сергеева Ксения", "+7 958 142", "703"])
def test_sql_and_orm_variants_agree_on_term_count(query):
    sql_built = nm.client_match_sql(query)
    assert sql_built is not None
    assert nm.client_match_orm(query) is not None
    assert len(nm.parse(query)) >= 1
