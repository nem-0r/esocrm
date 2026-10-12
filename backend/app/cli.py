"""Команды, которые выполняет владелец сервера, а не пользователь интерфейса.

Команд две. Первая обязательная: создать первого руководителя. В чистой
базе войти некому — все остальные учётные записи заводятся уже вошедшим
руководителем, и без этой команды свежая установка остаётся запертой.

Запуск на сервере:

    docker compose -f docker-compose.prod.yml exec api \\
        python -m app.cli create-admin --email you@company.ru --name "Имя Фамилия"

Пароль спрашивается в диалоге и не остаётся ни в истории команд, ни в логах.
Для автоматической установки его можно передать переменной окружения
ADMIN_PASSWORD — тогда команда ничего не спрашивает.

Вторая — для руководителя, который забыл пароль (D-38). Выдать ссылку ему
некому, поэтому её выдаёт тот, у кого есть доступ к серверу:

    docker compose -f docker-compose.prod.yml exec api \\
        python -m app.cli reset-password --email you@company.ru

Команда печатает одноразовую ссылку на новый пароль вида `https://сайт/reset#<токен>`
(24 часа, один раз; токен после «#» не уходит на сервер и не попадает в журналы); открыв
её, человек задаёт пароль и входит, все его прежние сессии завершаются. Работает
для любого активного сотрудника, принявшего приглашение, — не только руководителя.
"""

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import User, UserRole
from app.schemas.user import validate_email_format, validate_password_strength
from app.services import user_service
from app.services.auth_service import hash_password, normalize_email


async def create_admin(email: str, full_name: str, password: str | None, force: bool) -> int:
    email = normalize_email(validate_email_format(email))
    full_name = full_name.strip()
    if not full_name:
        print("Укажите имя: --name «Имя Фамилия»")
        return 2

    async with SessionLocal() as db:
        existing_admins = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN, User.deleted_at.is_(None))
        )
        if existing_admins and not force:
            # Команда для первого входа, а не для раздачи прав: второго
            # руководителя заводит первый — через интерфейс, с записью в журнал.
            print(
                f"Руководители уже есть ({existing_admins}). "
                "Новых заводите в разделе «Сотрудники». "
                "Если доступ потерян целиком — повторите с --force."
            )
            return 1

        taken = await db.scalar(select(User).where(User.email == email))
        if taken is not None:
            print(f"Учётная запись {email} уже существует.")
            return 1

        if password is None:
            password = getpass.getpass("Пароль (не короче 8 символов): ")
            if password != getpass.getpass("Повторите пароль: "):
                print("Пароли не совпали.")
                return 2
        validate_password_strength(password)

        db.add(
            User(
                full_name=full_name,
                email=email,
                password_hash=await hash_password(password),
                role=UserRole.ADMIN,
                is_active=True,
                # Приглашение не выдаём: человек уже знает свой пароль.
                accepted_at=func.now(),
            )
        )
        await db.commit()

    print(f"Руководитель {full_name} <{email}> создан. Войдите и смените пароль в профиле.")
    return 0


def _site_origin(explicit: str | None) -> str | None:
    """Адрес сайта для полной ссылки: из `--site`, иначе первый боевой адрес из
    CORS_ORIGINS (на проде там https://<домен>, localhost туда не пускают)."""
    if explicit:
        return explicit.rstrip("/")
    origins = settings.cors_origin_list
    real = [o for o in origins if "localhost" not in o and "127.0.0.1" not in o]
    chosen = (real or origins or [None])[0]
    return chosen.rstrip("/") if chosen else None


def full_link(origin: str | None, path: str) -> str:
    """Полная ссылка для сотрудника: `https://сайт/reset#<токен>`. Без адреса сайта —
    относительная, её дополняет человек."""
    return f"{origin}{path}" if origin else path


async def reset_password(email: str, site: str | None) -> int:
    email = normalize_email(validate_email_format(email))

    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
        if user is None:
            print(f"Учётной записи {email} нет.")
            return 1
        try:
            # actor=None: выдаёт сервер, а не вошедший руководитель — поэтому можно
            # и самому руководителю. В журнале это запись «системы» (via: cli).
            issued = await user_service.issue_password_reset_for(db, user, actor=None)
        except AppError as exc:
            print(exc.message)
            return 1

    origin = _site_origin(site)
    link = full_link(origin, issued.link)
    print(
        f"Одноразовая ссылка для нового пароля — {user.full_name} <{email}>.\n"
        "Действует 24 часа и срабатывает один раз; прежняя ссылка, если была, отключена.\n\n"
        f"    {link}\n\n"
        "Передайте её лично. После сохранения пароля все сессии сотрудника завершатся."
    )
    if not origin:
        print("Адрес сайта не определён — добавьте его перед ссылкой или укажите --site.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    admin = commands.add_parser("create-admin", help="Создать первого руководителя")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True, help="Имя и фамилия")
    admin.add_argument(
        "--force",
        action="store_true",
        help="Создать, даже если руководители уже есть (восстановление доступа)",
    )

    reset = commands.add_parser(
        "reset-password", help="Выдать одноразовую ссылку на новый пароль (забыл пароль)"
    )
    reset.add_argument("--email", required=True)
    reset.add_argument(
        "--site",
        help="Адрес сайта для полной ссылки (по умолчанию — из CORS_ORIGINS), например https://esocrm.com",
    )

    args = parser.parse_args()
    if args.command == "reset-password":
        try:
            return asyncio.run(reset_password(args.email, args.site))
        except ValueError as exc:
            print(str(exc))
            return 2
    if args.command == "create-admin":
        try:
            return asyncio.run(
                create_admin(args.email, args.name, os.environ.get("ADMIN_PASSWORD"), args.force)
            )
        except ValueError as exc:
            print(str(exc))
            return 2
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
