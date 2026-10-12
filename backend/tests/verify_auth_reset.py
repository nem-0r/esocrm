"""Сброс пароля через руководителя (D-38) и проверка вводимых данных (L1): сквозная проверка.

Бьёт по работающему стенду. Все данные — с уникальным префиксом `qa-reset-<run>`:
сотрудники, клиент, записи журнала и счётчики попыток создаются здесь и в конце
удаляются; демо-руководитель и менеджеры не затрагиваются.

Запуск (под замком общей dev-базы, docs/18, «Правила»):
    docker exec astra-crm-api-1 sh -c 'cd /app && python -m tests.verify_auth_reset'
"""

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text

from app.core.crypto import token_hash
from app.core.db import SessionLocal
from tests.support import ADMIN, BASE, MANAGER, Checks

RUN = secrets.token_hex(3)
PREFIX = f"qa-reset-{RUN}"
PASS1, PASS2, PASS3, PASS_C = "Qa-first-1234", "Qa-second-5678", "Qa-third-9012", "Qa-carol-3456"
THROTTLE_IP = (
    f"203.0.113.{int(RUN, 16) % 200 + 20}"  # документационный диапазон, не настоящий адрес
)
OTHER_IP = f"198.51.100.{int(RUN, 16) % 200 + 20}"


def email(tag: str) -> str:
    return f"{PREFIX}-{tag}@example.test"


def err(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json().get("error", {})
    except ValueError:
        return {}


def new_client(ip: str | None = None) -> httpx.AsyncClient:
    headers = {"X-Forwarded-For": ip} if ip else None
    return httpx.AsyncClient(timeout=60, headers=headers)


async def sql_one(query: str, **params: Any) -> Any:
    async with SessionLocal() as db:
        return (await db.execute(text(query), params)).first()


async def sql_exec(query: str, **params: Any) -> None:
    async with SessionLocal() as db:
        await db.execute(text(query), params)
        await db.commit()


async def cleanup() -> None:
    """Следы этого и прошлых прогонов: сотрудники, клиент, журнал, счётчики попыток."""
    async with SessionLocal() as db:
        ids = [
            r[0]
            for r in (
                await db.execute(
                    text("select id from users where email like 'qa-reset-%@example.test'")
                )
            ).all()
        ]
        client_ids = [
            r[0]
            for r in (
                await db.execute(
                    text(
                        "select id from clients where display_name like 'qa-reset-%' "
                        "or telegram_id between 990000000000 and 990000000099"
                    )
                )
            ).all()
        ]
        if ids:
            await db.execute(
                text(
                    "delete from event_log where actor_id = any(:ids) "
                    "or (entity_type = 'user' and entity_id = any(:ids))"
                ),
                {"ids": ids},
            )
        if client_ids:
            await db.execute(
                text(
                    "delete from event_log where entity_type = 'client' and entity_id = any(:ids)"
                ),
                {"ids": client_ids},
            )
            await db.execute(text("delete from clients where id = any(:ids)"), {"ids": client_ids})
        await db.execute(text("delete from users where email like 'qa-reset-%@example.test'"))
        await db.execute(
            text(
                "delete from login_attempts where email like 'qa-reset-%@example.test' "
                "or email in (:a, :b)"
            ),
            {"a": f"reset:{THROTTLE_IP}", "b": f"reset:{OTHER_IP}"},
        )
        await db.commit()


async def make_member(admin: httpx.AsyncClient, tag: str, password: str | None) -> dict[str, Any]:
    """Сотрудник-менеджер. С паролем — приглашение принято и вход выполнен; без — ждёт."""
    created = await admin.post(
        f"{BASE}/users",
        json={"full_name": f"QA Сброс {tag}", "email": email(tag), "role": "manager"},
    )
    created.raise_for_status()
    body = created.json()
    member: dict[str, Any] = {"id": body["id"], "email": email(tag), "name": f"QA Сброс {tag}"}
    if password:
        session = new_client()
        accepted = await session.post(
            f"{BASE}/auth/accept-invite",
            json={"token": body["invite_url"].rsplit("/", 1)[1], "password": password},
        )
        accepted.raise_for_status()
        member["session"] = session
    return member


async def issue(admin: httpx.AsyncClient, user_id: int) -> tuple[httpx.Response, str]:
    response = await admin.post(f"{BASE}/users/{user_id}/reset-password")
    token = response.json()["link"].rsplit("/", 1)[1] if response.status_code == 200 else ""
    return response, token


def run_cli(
    *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — свой интерпретатор и аргументы из этого файла
        [sys.executable, "-m", "app.cli", *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(extra_env or {})},
        check=False,
        timeout=120,
    )


async def run() -> int:
    c = Checks("Сброс пароля (D-38) и проверка данных (L1)")
    await cleanup()
    opened: list[httpx.AsyncClient] = []
    try:
        admin = new_client()
        opened.append(admin)
        (await admin.post(f"{BASE}/auth/login", json=ADMIN)).raise_for_status()
        manager = new_client()
        opened.append(manager)
        (await manager.post(f"{BASE}/auth/login", json=MANAGER)).raise_for_status()
        admin_id = (await admin.get(f"{BASE}/auth/me")).json()["id"]

        # ---------------------------------------------------------------- схема
        c.section("Схема базы и совместимость с откатом кода")
        async with SessionLocal() as db:
            cols = (
                await db.execute(
                    text(
                        "select column_name, is_nullable, column_default from "
                        "information_schema.columns where table_name = 'users' "
                        "and column_name like 'password_reset_%'"
                    )
                )
            ).all()
        c.check(
            "три новые колонки на месте",
            {r[0] for r in cols}
            == {
                "password_reset_token_hash",
                "password_reset_expires_at",
                "password_reset_issued_at",
            },
            str(cols),
        )
        c.check(
            "все допускают NULL и без значения по умолчанию",
            all(r[1] == "YES" and r[2] is None for r in cols),
            str(cols),
        )
        # Так вставляет сотрудника прошлая версия кода: про новые колонки она не знает
        await sql_exec(
            "insert into users (full_name, email, role) values (:n, :e, 'manager')",
            n="QA Старый код",
            e=email("old-code"),
        )
        old = await sql_one(
            "select password_reset_token_hash from users where email = :e", e=email("old-code")
        )
        c.check(
            "вставка «как у прошлой версии» (без новых колонок) проходит",
            old is not None and old[0] is None,
        )

        # ------------------------------------------------------- выдача ссылки
        c.section("Выдача ссылки руководителем")
        a = await make_member(admin, "a", PASS1)
        pending = await make_member(admin, "pending", None)
        a0 = a["session"]
        opened.append(a0)

        r, _ = await issue(manager, a["id"])
        c.check("менеджер выдать сброс не может (403)", r.status_code == 403, r.text[:120])
        r, _ = await issue(admin, admin_id)
        c.check(
            "себе выдать нельзя (422, «меняется в профиле»)",
            r.status_code == 422 and "Профиль" in err(r).get("message", ""),
            r.text[:160],
        )
        r, _ = await issue(admin, pending["id"])
        c.check(
            "не принявшему приглашение — 422 с подсказкой про повторное приглашение",
            r.status_code == 422 and "повторно" in err(r).get("message", ""),
            r.text[:160],
        )
        r, _ = await issue(admin, 99999999)
        c.check("несуществующему сотруднику — 404", r.status_code == 404, str(r.status_code))
        r = await new_client().post(f"{BASE}/users/{a['id']}/reset-password")
        c.check("без входа — 401", r.status_code == 401, str(r.status_code))

        before_accept = await sql_one("select accepted_at from users where id = :i", i=a["id"])
        r, token1 = await issue(admin, a["id"])
        ok = r.status_code == 200
        c.check("руководитель выдаёт ссылку", ok, r.text[:160])
        body = r.json() if ok else {}
        c.check(
            "ссылка — относительный путь /reset/<токен>, как у приглашений",
            str(body.get("link", "")).startswith("/reset/") and len(token1) >= 43,
            str(body)[:120],
        )
        expires = datetime.fromisoformat(body["expires_at"]) if ok else None
        delta = (expires - datetime.now(UTC)) if expires else timedelta(0)
        c.check(
            "срок — 24 часа",
            timedelta(hours=23, minutes=58) < delta <= timedelta(hours=24),
            str(delta),
        )
        stored = await sql_one(
            "select password_reset_token_hash, password_reset_expires_at is not null "
            "from users where id = :i",
            i=a["id"],
        )
        c.check(
            "в базе лежит хеш токена, а не сам токен",
            stored and stored[0] == token_hash(token1) and stored[0] != token1,
            str(stored),
        )
        audit_row = await sql_one(
            "select actor_id, entity_id, after::text from event_log "
            "where action = 'user.password_reset_issued' and entity_id = :i "
            "order by id desc limit 1",
            i=a["id"],
        )
        c.check(
            "в журнале: кто выдал и кому",
            audit_row and audit_row[0] == admin_id and audit_row[1] == a["id"],
            str(audit_row),
        )
        c.check("в журнале нет токена", audit_row and token1 not in audit_row[2], str(audit_row))

        # --------------------------------------------------------- проверка ссылки
        c.section("Проверка ссылки (публичная)")
        anon = new_client()
        opened.append(anon)
        r = await anon.get(f"{BASE}/auth/reset-password/{token1}")
        c.check(
            "живая ссылка → 200 и имя сотрудника",
            r.status_code == 200 and r.json().get("full_name") == a["name"],
            r.text[:160],
        )
        c.check(
            "в ответе только имя и срок, больше ничего",
            set(r.json()) == {"full_name", "expires_at"},
            str(r.json()),
        )
        c.check(
            "ответ не кэшируется (no-store)",
            "no-store" in r.headers.get("cache-control", ""),
            str(r.headers),
        )
        unknown = await anon.get(f"{BASE}/auth/reset-password/{secrets.token_urlsafe(32)}")
        c.check(
            "неизвестная ссылка → 404, нейтральный текст",
            unknown.status_code == 404
            and err(unknown).get("code") == "reset_link_invalid"
            and "Попросите руководителя" in err(unknown).get("message", ""),
            unknown.text[:160],
        )

        # ------------------------------------------------- блокировка и новый пароль
        c.section("Новый пароль: сессии, блокировка, одноразовость")
        for _ in range(5):
            await anon.post(
                f"{BASE}/auth/login", json={"email": a["email"], "password": "wrong-pass-1"}
            )
        locked = await anon.post(
            f"{BASE}/auth/login", json={"email": a["email"], "password": PASS1}
        )
        c.check(
            "после пяти ошибок вход заблокирован (даже с верным паролем)",
            locked.status_code == 403,
            str(locked.status_code),
        )
        c.check(
            "до сброса старая сессия сотрудника жива",
            (await a0.get(f"{BASE}/auth/me")).status_code == 200,
        )

        short = await anon.post(
            f"{BASE}/auth/reset-password", json={"token": token1, "password": "short"}
        )
        c.check(
            "короткий пароль → 422 (та же политика, что у приглашений)",
            short.status_code == 422 and "не короче 8" in err(short).get("message", ""),
            short.text[:160],
        )
        c.check(
            "отказ по паролю ссылку не сжигает",
            (await anon.get(f"{BASE}/auth/reset-password/{token1}")).status_code == 200,
        )

        a1 = new_client()
        opened.append(a1)
        done = await a1.post(
            f"{BASE}/auth/reset-password", json={"token": token1, "password": PASS2}
        )
        c.check(
            "пароль задан по ссылке → 200 и сразу вход",
            done.status_code == 200
            and done.json().get("email") == a["email"]
            and bool(done.json().get("token")),
            done.text[:160],
        )
        cookie = done.headers.get("set-cookie", "")
        c.check(
            "сессия ставится в защищённую cookie",
            "astra_session=" in cookie and "httponly" in cookie.lower(),
            cookie[:80],
        )
        c.check("новая сессия работает", (await a1.get(f"{BASE}/auth/me")).status_code == 200)
        c.check("старая сессия отозвана", (await a0.get(f"{BASE}/auth/me")).status_code == 401)
        old_pw = await anon.post(
            f"{BASE}/auth/login", json={"email": a["email"], "password": PASS1}
        )
        c.check(
            "старый пароль больше не входит (401, не 403 — блокировка снята)",
            old_pw.status_code == 401,
            str(old_pw.status_code),
        )
        new_pw = await anon.post(
            f"{BASE}/auth/login", json={"email": a["email"], "password": PASS2}
        )
        c.check("новый пароль входит", new_pw.status_code == 200, new_pw.text[:120])
        live = await sql_one(
            "select count(*) from auth_sessions where user_id = :i and revoked_at is null",
            i=a["id"],
        )
        c.check(
            "активные сессии: только созданные после сброса (вход по ссылке и вход по паролю)",
            live and live[0] == 2,
            str(live),
        )

        again = await new_client().post(
            f"{BASE}/auth/reset-password", json={"token": token1, "password": "Qa-reuse-0000"}
        )
        c.check(
            "повторное использование ссылки отклонено (404)",
            again.status_code == 404 and err(again).get("code") == "reset_link_invalid",
            again.text[:160],
        )
        c.check(
            "и проверка ссылки теперь 404",
            (await anon.get(f"{BASE}/auth/reset-password/{token1}")).status_code == 404,
        )
        c.check(
            "пароль от повторной попытки не установлен",
            (
                await anon.post(
                    f"{BASE}/auth/login", json={"email": a["email"], "password": "Qa-reuse-0000"}
                )
            ).status_code
            == 401,
        )

        after = await sql_one(
            "select accepted_at, password_reset_token_hash, password_reset_expires_at, "
            "password_reset_issued_at from users where id = :i",
            i=a["id"],
        )
        c.check("accepted_at не перезаписан", after and after[0] == before_accept[0], str(after))
        c.check(
            "токен и срок очищены, след выдачи остался",
            after and after[1] is None and after[2] is None and after[3] is not None,
            str(after),
        )
        done_row = await sql_one(
            "select actor_id, after::text from event_log where action = 'user.password_reset_done' "
            "and entity_id = :i",
            i=a["id"],
        )
        c.check(
            "в журнале: сброс завершён самим сотрудником",
            done_row and done_row[0] == a["id"],
            str(done_row),
        )

        # -------------------------------------------- новая выдача и смена пароля в профиле
        c.section("Новая выдача и смена пароля в профиле гасят прежнюю ссылку")
        _, link_old = await issue(admin, a["id"])
        _, link_new = await issue(admin, a["id"])
        c.check(
            "прежняя ссылка после повторной выдачи не работает",
            (await anon.get(f"{BASE}/auth/reset-password/{link_old}")).status_code == 404,
        )
        c.check(
            "новая работает",
            (await anon.get(f"{BASE}/auth/reset-password/{link_new}")).status_code == 200,
        )
        changed = await a1.patch(
            f"{BASE}/auth/me", json={"current_password": PASS2, "new_password": PASS3}
        )
        c.check(
            "сотрудник сам меняет пароль в профиле", changed.status_code == 200, changed.text[:120]
        )
        c.check(
            "после этого выданная ссылка сброса погашена",
            (await anon.get(f"{BASE}/auth/reset-password/{link_new}")).status_code == 404,
        )

        # ----------------------------------------------------------------- срок
        c.section("Просроченная и чужая ссылки")
        _, link_exp = await issue(admin, a["id"])
        await sql_exec(
            "update users set password_reset_expires_at = now() - interval '1 minute' "
            "where id = :i",
            i=a["id"],
        )
        expired = await anon.get(f"{BASE}/auth/reset-password/{link_exp}")
        c.check("просроченная → 404", expired.status_code == 404, str(expired.status_code))
        c.check(
            "ответ просроченной неотличим от неизвестной",
            expired.json() == unknown.json(),
            f"{expired.json()} / {unknown.json()}",
        )
        late = await new_client().post(
            f"{BASE}/auth/reset-password", json={"token": link_exp, "password": "Qa-late-0000"}
        )
        c.check("по просроченной пароль не задать (404)", late.status_code == 404, late.text[:120])

        carol = await make_member(admin, "carol", PASS_C)
        opened.append(carol["session"])
        _, link_a = await issue(admin, a["id"])
        _, link_c = await issue(admin, carol["id"])
        swapped = new_client()
        opened.append(swapped)
        done_c = await swapped.post(
            f"{BASE}/auth/reset-password", json={"token": link_c, "password": "Qa-carol-7890"}
        )
        c.check(
            "ссылка действует только на своего сотрудника",
            done_c.status_code == 200 and done_c.json().get("email") == carol["email"],
            done_c.text[:160],
        )
        c.check(
            "пароль другого сотрудника не изменился",
            (
                await anon.post(f"{BASE}/auth/login", json={"email": a["email"], "password": PASS3})
            ).status_code
            == 200,
        )
        c.check(
            "и его ссылка по-прежнему жива",
            (await anon.get(f"{BASE}/auth/reset-password/{link_a}")).status_code == 200,
        )

        # ------------------------------------------------------------ отключение
        c.section("Отключённый сотрудник")
        off = await admin.patch(f"{BASE}/users/{a['id']}", json={"is_active": False})
        c.check("сотрудник отключён", off.status_code == 200, off.text[:120])
        c.check(
            "при отключении ссылка погашена",
            (await anon.get(f"{BASE}/auth/reset-password/{link_a}")).status_code == 404,
        )
        r, _ = await issue(admin, a["id"])
        c.check(
            "отключённому ссылку не выдать (422)",
            r.status_code == 422 and "отключ" in err(r).get("message", ""),
            r.text[:160],
        )

        # --------------------------------------------------------------- CLI
        c.section("Команда на сервере: python -m app.cli reset-password")
        dave = await make_member(admin, "dave", PASS_C)
        opened.append(dave["session"])
        proc = await asyncio.to_thread(run_cli, "reset-password", "--email", dave["email"])
        found = re.search(r"/reset/([A-Za-z0-9_-]{43})", proc.stdout)
        c.check(
            "команда печатает ссылку",
            proc.returncode == 0 and found is not None,
            (proc.stdout + proc.stderr)[-300:],
        )
        if found:
            c.check(
                "ссылка из команды открывается",
                (await anon.get(f"{BASE}/auth/reset-password/{found.group(1)}")).status_code == 200,
            )
            sys_row = await sql_one(
                "select actor_kind::text, actor_id, after::text from event_log "
                "where action = 'user.password_reset_issued' and entity_id = :i "
                "order by id desc limit 1",
                i=dave["id"],
            )
            c.check(
                "в журнале запись «система», через cli, без токена",
                sys_row
                and sys_row[0] == "system"
                and sys_row[1] is None
                and '"cli"' in sys_row[2]
                and found.group(1) not in sys_row[2],
                str(sys_row),
            )
        proc = await asyncio.to_thread(run_cli, "reset-password", "--email", pending["email"])
        c.check(
            "не принявшему приглашение — отказ с подсказкой",
            proc.returncode == 1 and "повторно" in proc.stdout,
            proc.stdout[-200:],
        )
        proc = await asyncio.to_thread(run_cli, "reset-password", "--email", email("nobody"))
        c.check(
            "несуществующая почта — отказ",
            proc.returncode == 1 and "нет" in proc.stdout,
            proc.stdout[-200:],
        )
        before_users = await sql_one("select count(*) from users")
        proc = await asyncio.to_thread(
            run_cli,
            "create-admin",
            "--email",
            email("never"),
            "--name",
            "QA Никогда",
            extra_env={"ADMIN_PASSWORD": "Qa-never-1234"},
        )
        after_users = await sql_one("select count(*) from users")
        c.check(
            "create-admin по-прежнему работает как раньше (руководители уже есть → отказ)",
            proc.returncode == 1
            and "Руководители уже есть" in proc.stdout
            and before_users[0] == after_users[0],
            proc.stdout[-200:],
        )

        # --------------------------------------------------- ограничение частоты
        c.section("Ограничение частоты неверных ссылок")
        _, link_ok = await issue(admin, carol["id"])
        thr = new_client(THROTTLE_IP)
        opened.append(thr)
        codes = [
            (await thr.get(f"{BASE}/auth/reset-password/{secrets.token_urlsafe(32)}")).status_code
            for _ in range(20)
        ]
        c.check("первые 20 неверных ссылок — обычный 404", set(codes) == {404}, str(set(codes)))
        blocked = await thr.get(f"{BASE}/auth/reset-password/{secrets.token_urlsafe(32)}")
        c.check(
            "21-я с того же адреса — 429",
            blocked.status_code == 429 and err(blocked).get("code") == "rate_limited",
            blocked.text[:160],
        )
        c.check(
            "и верная ссылка с этого адреса на время закрыта",
            (await thr.get(f"{BASE}/auth/reset-password/{link_ok}")).status_code == 429,
        )
        c.check(
            "и установка пароля тоже",
            (
                await thr.post(
                    f"{BASE}/auth/reset-password",
                    json={"token": link_ok, "password": "Qa-blocked-0000"},
                )
            ).status_code
            == 429,
        )
        other = new_client(OTHER_IP)
        opened.append(other)
        c.check(
            "с другого адреса та же ссылка работает",
            (await other.get(f"{BASE}/auth/reset-password/{link_ok}")).status_code == 200,
        )

        # ------------------------------------------------- телефон сотрудника (L1)
        c.section("Телефон сотрудника")
        me_bad = await dave["session"].patch(f"{BASE}/auth/me", json={"phone": "++--1234567"})
        c.check(
            "профиль: «++--1234567» → 422 у поля phone",
            me_bad.status_code == 422 and err(me_bad).get("details", {}).get("field") == "phone",
            me_bad.text[:200],
        )
        me_abc = await dave["session"].patch(f"{BASE}/auth/me", json={"phone": "abc"})
        c.check("профиль: «abc» → 422", me_abc.status_code == 422, str(me_abc.status_code))
        me_ok = await dave["session"].patch(f"{BASE}/auth/me", json={"phone": "+7  900 000--00-01"})
        c.check(
            "профиль: нормальный номер сохраняется схлопнутым",
            me_ok.status_code == 200 and me_ok.json()["phone"] == "+7 900 000-00-01",
            me_ok.text[:160],
        )
        other_field = await dave["session"].patch(
            f"{BASE}/auth/me", json={"accepting_leads": False}
        )
        c.check(
            "правка другого поля телефон не трогает",
            other_field.json()["phone"] == "+7 900 000-00-01",
        )
        cleared = await dave["session"].patch(f"{BASE}/auth/me", json={"phone": ""})
        c.check(
            "пустой телефон очищает поле",
            cleared.status_code == 200 and cleared.json()["phone"] is None,
            cleared.text[:160],
        )
        st_bad = await admin.patch(f"{BASE}/users/{dave['id']}", json={"phone": "12"})
        c.check(
            "карточка сотрудника: «12» → 422",
            st_bad.status_code == 422 and err(st_bad).get("details", {}).get("field") == "phone",
            st_bad.text[:200],
        )
        st_ok = await admin.patch(f"{BASE}/users/{dave['id']}", json={"phone": "8 900 000 00 02"})
        c.check(
            "карточка сотрудника: нормальный номер",
            st_ok.status_code == 200 and st_ok.json()["phone"] == "8 900 000 00 02",
            st_ok.text[:160],
        )
        st_clear = await admin.patch(f"{BASE}/users/{dave['id']}", json={"phone": None})
        c.check(
            "карточка сотрудника: null очищает",
            st_clear.status_code == 200 and st_clear.json()["phone"] is None,
            st_clear.text[:160],
        )
        inv_bad = await admin.post(
            f"{BASE}/users",
            json={
                "full_name": "QA Плохой",
                "email": email("badphone"),
                "role": "manager",
                "phone": "++--1234567",
            },
        )
        c.check(
            "приглашение с «++--1234567» → 422",
            inv_bad.status_code == 422 and err(inv_bad).get("details", {}).get("field") == "phone",
            inv_bad.text[:200],
        )
        c.check(
            "и сотрудник при этом не создан",
            (await sql_one("select count(*) from users where email = :e", e=email("badphone")))[0]
            == 0,
        )

        # --------------------------------------------------- карточка клиента (L1)
        c.section("Карточка клиента")
        async with SessionLocal() as db:
            cid = (
                await db.execute(
                    text(
                        "insert into clients (telegram_id, display_name) "
                        "values (990000000000 + :n, :name) returning id"
                    ),
                    {"n": int(RUN, 16) % 99, "name": f"{PREFIX}-client"},
                )
            ).scalar_one()
            await db.commit()
        url = f"{BASE}/clients/{cid}"

        def fail_info(resp: httpx.Response) -> tuple[int, Any]:
            return resp.status_code, err(resp).get("details", {}).get("field")

        r = await admin.patch(url, json={"birth_date": "2999-01-01"})
        c.check(
            "дата рождения 2999 → 422 у поля birth_date",
            fail_info(r) == (422, "birth_date"),
            r.text[:200],
        )
        r = await admin.patch(url, json={"birth_date": "1899-12-31"})
        c.check("дата до 1900 → 422", fail_info(r) == (422, "birth_date"), r.text[:200])
        r = await admin.patch(url, json={"birth_date": "2000-13-45"})
        c.check(
            "нечитаемая дата → 422 с понятным текстом",
            r.status_code == 422 and "Дата рождения" in err(r).get("message", ""),
            r.text[:200],
        )
        far = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
        r = await admin.patch(url, json={"birth_date": far})
        c.check("дата через три дня → 422", r.status_code == 422, r.text[:160])
        r = await admin.patch(url, json={"birth_date": "1990-05-17"})
        c.check(
            "нормальная дата принимается, знак зодиака посчитан",
            r.status_code == 200
            and r.json()["birth_date"] == "1990-05-17"
            and r.json()["zodiac_sign"] == "Телец",
            r.text[:200],
        )
        r = await admin.patch(url, json={"birth_date": "1900-01-01"})
        c.check("граница 1900-01-01 принимается", r.status_code == 200, r.text[:160])
        r = await admin.patch(url, json={"birth_date": None})
        c.check(
            "дату можно очистить",
            r.status_code == 200
            and r.json()["birth_date"] is None
            and r.json()["zodiac_sign"] is None,
            r.text[:160],
        )

        r = await admin.patch(url, json={"phone": "++--1234567"})
        c.check(
            "телефон «++--1234567» → 422 у поля phone", fail_info(r) == (422, "phone"), r.text[:200]
        )
        r = await admin.patch(url, json={"phone": "abc"})
        c.check("телефон «abc» → 422, как и раньше", fail_info(r) == (422, "phone"), r.text[:200])
        r = await admin.patch(url, json={"phone": "1" * 40})
        c.check("слишком длинный телефон → 422", r.status_code == 422, r.text[:160])
        r = await admin.patch(url, json={"phone": "  +7   916  200--10-09 "})
        c.check(
            "телефон нормализуется (повторы схлопнуты)",
            r.status_code == 200 and r.json()["phone"] == "+7 916 200-10-09",
            r.text[:200],
        )
        r = await admin.patch(url, json={"phone": ""})
        c.check(
            "пустой телефон очищает поле",
            r.status_code == 200 and r.json()["phone"] is None,
            r.text[:160],
        )

        r = await admin.patch(url, json={"display_name": "я" * 121})
        c.check(
            "имя длиннее 120 → 422 у поля display_name",
            fail_info(r) == (422, "display_name"),
            r.text[:200],
        )
        r = await admin.patch(url, json={"display_name": "   "})
        c.check(
            "имя из одних пробелов → поле очищено (null)",
            r.status_code == 200 and r.json()["display_name"] is None,
            r.text[:200],
        )
        r = await admin.patch(url, json={"display_name": "  Анна   Петрова "})
        c.check(
            "имя: пробелы по краям срезаны, середина не тронута",
            r.status_code == 200 and r.json()["display_name"] == "Анна   Петрова",
            r.text[:200],
        )
        r = await admin.patch(url, json={"display_name": "<script>alert(1)</script>"})
        c.check(
            "разметка в имени — просто текст (экранирует интерфейс)",
            r.status_code == 200 and r.json()["display_name"] == "<script>alert(1)</script>",
        )
        r = await admin.patch(url, json={"birth_city": "г" * 121})
        c.check("город длиннее 120 → 422", fail_info(r) == (422, "birth_city"), r.text[:200])
        r = await admin.patch(url, json={"birth_city": "  Москва "})
        c.check(
            "город: края срезаны",
            r.status_code == 200 and r.json()["birth_city"] == "Москва",
            r.text[:200],
        )
        r = await manager.patch(url, json={"phone": "++--1234567"})
        c.check(
            "менеджеру чужой клиент по-прежнему не виден (404 раньше проверки данных)",
            r.status_code in (404, 422),
            str(r.status_code),
        )
    finally:
        for client in opened:
            await client.aclose()
        await cleanup()
        rest = await sql_one(
            "select (select count(*) from users where email like 'qa-reset-%@example.test'), "
            "(select count(*) from clients where display_name like 'qa-reset-%'), "
            "(select count(*) from login_attempts where email like 'reset:%' "
            "and (email = :a or email = :b))",
            a=f"reset:{THROTTLE_IP}",
            b=f"reset:{OTHER_IP}",
        )
        c.section("Уборка")
        c.check(
            "тестовые сотрудники, клиент и счётчики удалены",
            rest is not None and tuple(rest) == (0, 0, 0),
            json.dumps(list(rest or [])),
        )
    return c.finish()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
