from functools import lru_cache
from urllib.parse import unquote, urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Минимальная длина ключей. 32 символа — это выход `openssl rand -hex 16`;
# в инструкции просим hex 32 (64 символа), но нижнюю границу держим здесь.
MIN_SECRET_LENGTH = 32

# Значения, которые встречались в .env.example и в умолчаниях кода. Ни одно
# из них не должно доехать до продакшена: они лежат в открытом репозитории,
# и любой, кто видел исходники, сможет подписать себе сессию администратора
# или расшифровать сессии Telegram.
KNOWN_WEAK_SECRETS = frozenset(
    {
        "dev_secret_key_change_me",
        "dev_encryption_key_change_me",
        "dev_secret_key_change_me_0123456789abcdef",
        "dev_encryption_key_change_me_0123456789ab",
        "change_me",
        "changeme",
        "secret",
        "secret_key",
        "astra",
        "test",
    }
)

# Подстроки-маркеры заглушек: CHANGE_ME_..., ..._change_me, dev_..._key и т.п.
WEAK_SECRET_MARKERS = ("change_me", "changeme", "example", "заглушка", "placeholder")

# Учётные данные инфраструктуры: пароль Postgres и ключи MinIO. К ним не
# предъявляется требование длины (это не подписывающие ключи, а пароли, которые
# заводит администратор), но значения из репозитория в продакшене недопустимы:
# сервер поднимает базу и хранилище с ними, и любой, кто получил доступ к сети
# контейнеров или к ssh-туннелю, входит по паролю из .env.example.
KNOWN_WEAK_INFRA_SECRETS = frozenset(
    {
        "astra",
        "astra_local_pwd",
        "astra_minio",
        "astra_minio_secret",
        "postgres",
        "password",
        "minio",
        "minioadmin",
        "root",
        "admin",
    }
)


def _infra_problems(name: str, value: str, hint: str) -> list[str]:
    """Претензии к паролю инфраструктуры: только заглушки и значения из репозитория.

    Пустое значение и длина здесь намеренно не проверяются: пароль может
    приходить из внешнего managed-Postgres или из сертификата, и лишний запрет
    сломал бы рабочее развёртывание. Ловим ровно то, что обещано в README —
    старт на значениях по умолчанию.
    """
    lowered = value.strip().lower()
    if not lowered:
        return []
    if lowered in KNOWN_WEAK_INFRA_SECRETS or any(m in lowered for m in WEAK_SECRET_MARKERS):
        return [
            f"{name} остался значением из репозитория ({value.strip()}). "
            f"Оно известно всем, у кого есть доступ к исходникам. {hint}"
        ]
    return []


def _password_from_url(url: str) -> str:
    """Пароль из строки подключения. Непонятный формат — пустая строка."""
    try:
        raw = urlsplit(url).password
    except ValueError:
        return ""
    return unquote(raw) if raw else ""


class ConfigError(RuntimeError):
    """Конфигурация непригодна для запуска.

    Не наследуется от ValueError намеренно: pydantic перехватывает ValueError
    в валидаторах и заворачивает его в ValidationError с трассировкой на
    пол-экрана. Здесь нужен ровно один читаемый абзац текста.
    """


def _secret_problems(name: str, value: str, hint: str) -> list[str]:
    """Список претензий к одному секрету. Пустой список — секрет годный."""
    problems: list[str] = []
    lowered = value.strip().lower()

    if not lowered:
        problems.append(f"{name} не задан. {hint}")
        return problems

    if lowered in KNOWN_WEAK_SECRETS or any(m in lowered for m in WEAK_SECRET_MARKERS):
        problems.append(
            f"{name} совпадает с примером из .env.example и известен всем, "
            f"у кого есть доступ к репозиторию. {hint}"
        )
    elif len(value) < MIN_SECRET_LENGTH:
        problems.append(
            f"{name} короче {MIN_SECRET_LENGTH} символов "
            f"(сейчас {len(value)}). {hint}"
        )
    return problems


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    demo_mode: bool = True
    log_level: str = "INFO"
    service_role: str = "api"

    database_url: str = "postgresql+asyncpg://astra:astra@db:5432/astra"
    redis_url: str = "redis://cache:6379/0"

    s3_endpoint_url: str = "http://storage:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "astra_minio"
    s3_secret_key: str = "astra_minio_secret"  # noqa: S105 — локальный MinIO, не секрет продакшена
    s3_bucket: str = "astra-files"
    s3_region: str = "us-east-1"

    secret_key: str = "dev_secret_key_change_me"  # noqa: S105 — значение по умолчанию для локального запуска
    encryption_key: str = "dev_encryption_key_change_me"
    session_ttl_days: int = 30
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"

    # пороги, продублированные в settings-таблице; здесь — значения по умолчанию
    login_max_attempts: int = 5
    login_lockout_minutes: int = 30
    presence_ttl_seconds: int = 300
    gateway_lease_seconds: int = 30
    gateway_heartbeat_seconds: int = 10
    gateway_capacity: int = 25

    # Ключи приложения Telegram общие для всех аккаунтов: на доске руководитель
    # вводит только название, номер и этап воронки. Значения берутся с
    # my.telegram.org и живут в окружении, а не в форме подключения.
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    # Выход в сеть для MTProto. Пусто — напрямую. Формат: socks5://user:pass@host:port
    # По решению D-21 у каждого номера должен быть свой мобильный прокси; пока
    # значение одно на установку, поаккаунтные адреса добавятся вместе с закупкой.
    telegram_proxy: str = ""

    # Робокасса: пароли не в базе и не в репозитории — только в окружении сервера
    # (docs/11-payments-architecture.md, разд. 7). Пусто = способ оплаты «Ссылка»
    # выключен в интерфейсе честно, а не показан нерабочей кнопкой.
    robokassa_merchant_login: str = ""
    robokassa_password1: str = ""
    robokassa_password2: str = ""
    robokassa_is_test: bool = True

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def _blank_api_id_is_zero(cls, value: object) -> object:
        """`TELEGRAM_API_ID=` без значения — обычное состояние до получения ключей.

        Пустая строка не разбирается в число, и раньше от этого падал каждый
        процесс, который читает настройки: переменная объявлена, но не заполнена.
        Пустое значение равнозначно отсутствующему.
        """
        return 0 if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> "Settings":
        """В продакшене не даём стартовать на настройках для разработки.

        Проверка намеренно жёсткая и срабатывает при импорте настроек, то есть
        до того, как поднимется хоть один сетевой порт. Тихо запуститься с
        демо-ключами хуже, чем не запуститься совсем: система с известным
        SECRET_KEY взламывается подделкой cookie, а с известным ENCRYPTION_KEY
        из базы достаются рабочие сессии Telegram живых менеджеров.

        В режимах local/development/test ничего не проверяется.
        """
        if self.app_env != "production":
            return self

        gen = "Сгенерируйте новое значение: openssl rand -hex 32"
        problems: list[str] = []
        problems += _secret_problems("SECRET_KEY", self.secret_key, gen)
        problems += _secret_problems("ENCRYPTION_KEY", self.encryption_key, gen)

        problems += _infra_problems(
            "Пароль Postgres в DATABASE_URL",
            _password_from_url(self.database_url),
            "Задайте свой пароль и продублируйте его в POSTGRES_PASSWORD "
            "(если база уже создана — смените его внутри базы через ALTER USER).",
        )
        problems += _infra_problems(
            "S3_ACCESS_KEY",
            self.s3_access_key,
            "Придумайте своё имя ключа для MinIO.",
        )
        problems += _infra_problems(
            "S3_SECRET_KEY",
            self.s3_secret_key,
            "Сгенерируйте пароль: openssl rand -hex 24",
        )

        if self.secret_key.strip() and self.secret_key == self.encryption_key:
            problems.append(
                "SECRET_KEY и ENCRYPTION_KEY одинаковые. Это разные секреты с "
                "разным сроком жизни — задайте два независимых значения."
            )

        if self.demo_mode:
            problems.append(
                "DEMO_MODE=true. В демо-режиме шлюз не подключается к Telegram, "
                "а в базе живут выдуманные клиенты. Поставьте DEMO_MODE=false."
            )

        if not self.cookie_secure:
            problems.append(
                "COOKIE_SECURE=false. Cookie сессии уйдёт по открытому HTTP и "
                "её перехватят. Поставьте COOKIE_SECURE=true — сайт работает "
                "по HTTPS."
            )

        if self.robokassa_enabled and self.robokassa_is_test:
            problems.append(
                "Заполнены боевые данные Робокассы (ROBOKASSA_MERCHANT_LOGIN/"
                "PASSWORD1/PASSWORD2), но ROBOKASSA_IS_TEST=true. Кнопка оплаты "
                "будет работать, но каждая ссылка уйдёт в тестовый контур "
                "Робокассы — деньги по-настоящему проходить не будут. Поставьте "
                "ROBOKASSA_IS_TEST=false, когда магазин переведён в боевой режим."
            )

        if not problems:
            return self

        listing = "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, 1))
        raise ConfigError(
            "\n"
            "===============================================================\n"
            " ЗАПУСК ОСТАНОВЛЕН: настройки продакшена небезопасны\n"
            "===============================================================\n"
            f"APP_ENV=production, но найдено проблем: {len(problems)}\n\n"
            f"{listing}\n\n"
            "Что делать: откройте файл .env на сервере, исправьте перечисленные\n"
            "переменные и запустите заново:\n"
            "  docker compose -f docker-compose.prod.yml up -d\n\n"
            "Заготовка со всеми переменными — в .env.example.\n"
            "==============================================================="
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def robokassa_enabled(self) -> bool:
        """Все три значения нужны сразу: логин без паролей ничего не подпишет."""
        return bool(
            self.robokassa_merchant_login
            and self.robokassa_password1
            and self.robokassa_password2
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
