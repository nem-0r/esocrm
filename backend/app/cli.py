"""Команды, которые выполняет владелец сервера, а не пользователь интерфейса.

Пока команда одна и она обязательная: создать первого руководителя. В чистой
базе войти некому — все остальные учётные записи заводятся уже вошедшим
руководителем, и без этой команды свежая установка остаётся запертой.

Запуск на сервере:

    docker compose -f docker-compose.prod.yml exec api \\
        python -m app.cli create-admin --email you@company.ru --name "Имя Фамилия"

Пароль спрашивается в диалоге и не остаётся ни в истории команд, ни в логах.
Для автоматической установки его можно передать переменной окружения
ADMIN_PASSWORD — тогда команда ничего не спрашивает.
"""

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import User, UserRole
from app.schemas.user import validate_email_format, validate_password_strength
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

    args = parser.parse_args()
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
