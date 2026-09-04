# Астра CRM

CRM для продажи эзотерических услуг с перепиской в личных Telegram-аккаунтах.
Веб-приложение: один код на телефон и компьютер.

Документы проекта — в `docs/`. Читать в порядке: `00-context.md` → `07-architecture.md`
→ `02-data-model.md` → `03-business-rules.md`.

## Запуск на своей машине

Нужны Docker Desktop и Node 20+.

```bash
cp .env.example .env      # значения по умолчанию годятся для локальной работы
make up                   # поднимает ВСЁ, включая фронтенд
make seed                 # демо-данные
```

Всё работает в Docker, ничего ставить на машину не нужно.

| Что | Адрес | Контейнер |
|---|---|---|
| Интерфейс | http://localhost:5173 | `web` |
| API и его документация | http://localhost:8000/docs | `api` |
| Хранилище файлов | http://localhost:9001 | `storage` |
| PostgreSQL | localhost:5432 | `db` |
| Redis | localhost:6380 | `cache` |

Порт Redis снаружи 6380, а не 6379: 6379 часто занят локально установленным Redis.
Внутри сети контейнеров он остаётся 6379.

Команда `make web` запускает фронтенд напрямую на машине, минуя Docker — она нужна
только когда удобнее отлаживать вёрстку без контейнера.

### Демо-доступы

| Логин | Пароль | Роль |
|---|---|---|
| `elena@astra.ru` | `demo1234` | Руководитель |
| `marina@astra.ru` | `demo1234` | Менеджер |
| `anna@astra.ru` | `demo1234` | Менеджер |

## Демо-режим

При `DEMO_MODE=true` шлюз **не подключается к Telegram**. Подключение аккаунта проходит
через демо-провайдер: код подтверждения — любые 5 цифр. Исходящие сообщения проходят
очередь и меняют статусы так же, как в бою, но никуда не уходят.

Демо-данные и живой Telegram не смешиваются намеренно: в прошлой версии продукта смешивание
дало лавину ложных срабатываний.

## Состав

| Сервис | Роль |
|---|---|
| `api` | REST и WebSocket, права, аналитика |
| `gateway` | сессии Telegram, приём и отправка сообщений |
| `scheduler` | сроки сделок, предагрегаты, чистка |
| `db` | PostgreSQL 16 — единственный источник правды |
| `cache` | Redis — живые события, онлайн-статус |
| `storage` | S3-совместимое хранилище файлов (локально MinIO) |

Ни один процесс ничего не хранит на своём диске: сессии — в базе (зашифрованы),
файлы — в объектном хранилище. Поэтому масштабирование шлюза — одна команда:

```bash
docker compose up -d --scale gateway=3
```

## Полезные команды

```bash
make logs      # логи api, gateway, scheduler
make test      # pytest + vitest
make lint      # ruff, eslint, tsc
make reset     # снести данные и залить демо заново
make db-shell  # psql
```

Сквозная проверка на демо-данных — `make verify`. Четыре набора, каждый бьёт
по работающей системе, а не по заглушкам:

```bash
docker compose exec -T api python -m tests.smoke_api        # доступ и права
docker compose exec -T api python -m tests.verify_miro      # требования доски Miro
docker compose exec -T api python -m tests.verify_analytics # цифры против пересчёта по базе
docker compose exec -T api python -m tests.verify_actions   # действия меняют состояние
```

`verify_actions` намеренно портит демо-данные — после него нужен `make seed`
(`make verify` делает это сам). Результаты последней сверки с доской и
документом модели данных — в `docs/08-conformance.md`.

## Развёртывание в продакшене

Продакшен живёт в отдельном файле `docker-compose.prod.yml`. Отличия от контура
разработки: исходники внутри образов (без монтирования и `--reload`), миграции
отдельным одноразовым сервисом, наружу открыты только 80 и 443, у всех сервисов
`restart: unless-stopped` и ограничения по памяти и CPU.

Всё ниже выполняется **на сервере**, из каталога проекта.

### 1. Сервер

Минимум: 2 vCPU, 8 ГБ ОЗУ, 60 ГБ диска, Ubuntu 22.04 или 24.04. Меньше 4 ГБ брать
нельзя: Postgres, MinIO, шлюз Telegram и сборка фронтенда в 4 ГБ не помещаются.

```bash
# Docker с плагином compose
curl -fsSL https://get.docker.com | sh

# Файрвол: снаружи доступны только ssh и веб
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable

git clone <адрес репозитория> /opt/astra
cd /opt/astra
```

Порты базы, Redis и MinIO наружу не публикуются — до них дотягиваются только
контейнеры. Администратору они доступны через `docker compose exec` или ssh-туннель.

### 2. Домен и DNS

Заведите поддомен, например `crm.example.ru`, и укажите A-запись на IP сервера.
До выпуска сертификата дождитесь, пока имя начнёт разрешаться:

```bash
dig +short crm.example.ru      # должен вывести IP сервера
```

Если A-запись ещё не разошлась, Let's Encrypt выдаст отказ и потратит попытку:
у него 5 неудач в час на домен.

### 3. Сертификат Let's Encrypt

Первый выпуск делается до старта контура, пока порт 80 свободен:

```bash
mkdir -p deploy/certbot/conf deploy/certbot/www

docker run --rm -p 80:80 \
  -v /opt/astra/deploy/certbot/conf:/etc/letsencrypt \
  -v /opt/astra/deploy/certbot/www:/var/www/certbot \
  certbot/certbot certonly --standalone \
  -d crm.example.ru \
  --email вы@почта.ру --agree-tos --no-eff-email
```

Проверка, что сертификат лёг на место:

```bash
ls deploy/certbot/conf/live/crm.example.ru/   # ждём fullchain.pem и privkey.pem
```

**Продление.** Сертификат живёт 90 дней. Продлевать через тот же webroot, который
раздаёт nginx, — контур при этом не останавливается. В `crontab -e`:

```
30 4 * * 1 cd /opt/astra && docker run --rm \
  -v /opt/astra/deploy/certbot/conf:/etc/letsencrypt \
  -v /opt/astra/deploy/certbot/www:/var/www/certbot \
  certbot/certbot renew --webroot -w /var/www/certbot --quiet \
  && docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload
```

Сначала прогоните вручную с `--dry-run` — так проверяется весь путь продления
без расхода лимитов.

### 4. Файл .env

```bash
cp .env.example .env
openssl rand -hex 32        # для SECRET_KEY
openssl rand -hex 32        # для ENCRYPTION_KEY — ДРУГОЕ значение
nano .env
```

Обязательно к замене:

| Переменная | Значение в продакшене |
|---|---|
| `APP_ENV` | `production` |
| `DEMO_MODE` | `false` |
| `DOMAIN` | `crm.example.ru` — то же имя, что в сертификате |
| `SECRET_KEY` | результат `openssl rand -hex 32` |
| `ENCRYPTION_KEY` | результат `openssl rand -hex 32`, **другой** |
| `COOKIE_SECURE` | `true` |
| `POSTGRES_PASSWORD` | свой пароль; тот же — внутри `DATABASE_URL` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | свои значения |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | ключи с my.telegram.org |
| `CORS_ORIGINS` | `https://crm.example.ru` |
| `TELEGRAM_PROXY` | адрес прокси или пусто |

Стартовать на значениях по умолчанию система не даст: при `APP_ENV=production`
проверка в `backend/app/core/config.py` останавливает запуск и печатает
по-русски, какие именно переменные не заполнены. Это не придирка — с известным
`SECRET_KEY` подделывается cookie администратора, с известным `ENCRYPTION_KEY`
из базы достаются рабочие сессии Telegram живых менеджеров.

Сам `.env` в репозиторий не попадает (он в `.gitignore`) — сделайте отдельную
копию в менеджере паролей. Потерянный `ENCRYPTION_KEY` означает переподключение
всех аккаунтов Telegram заново.

### 5. Запуск и миграции

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Миграции выполняет отдельный сервис `migrate`: он отрабатывает `alembic upgrade
head` и завершается, и только после его успеха стартует `api`. Отдельно запускать
ничего не нужно. Если миграция упала — `api` не поднимется, и это правильно:
приложение на несовпадающей схеме хуже, чем отсутствующее приложение.

```bash
docker compose -f docker-compose.prod.yml logs migrate     # как прошла миграция
docker compose -f docker-compose.prod.yml ps               # все должны быть Up / healthy
```

Повторный выкат новой версии:

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

`migrate` отработает заново перед стартом `api`.

### 6. Первый вход

Демо-данных в продакшене нет и `make seed` запускать **нельзя** — он стирает базу
(при `APP_ENV=production` он и сам откажется работать).

Первого руководителя заводит отдельная команда — в чистой базе войти иначе
некому, а всех остальных сотрудников создаёт уже вошедший руководитель:

```bash
docker compose -f docker-compose.prod.yml exec api \
    python -m app.cli create-admin --email director@example.ru --name "Имя Фамилия"
```

Пароль команда спросит в диалоге и дважды: он не попадёт ни в историю команд,
ни в логи. Для автоматической установки его можно передать переменной
`ADMIN_PASSWORD`.

Команда одноразовая по смыслу: если руководитель уже есть, она откажется
работать и подскажет завести сотрудника через интерфейс. Ключ `--force`
оставлен на случай полной потери доступа.

Дальше — открыть `https://crm.example.ru`, войти, в разделе «Аккаунты» подключить
номера Telegram, в «Сотрудниках» завести менеджеров.

### 7. Резервные копии

```bash
./deploy/backup.sh                 # база + вложения в ./backups, хранение 30 дней
```

Кладёт два файла: `backups/db-ГГГГММДД-ЧЧММСС.sql.gz` (полный дамп Postgres)
и `backups/files-ГГГГММДД-ЧЧММСС.tar.gz` (содержимое бакета вложений). Скрипт
проверяет результат: пустой дамп или дамп без `CREATE TABLE` считается ошибкой
и завершает работу ненулевым кодом.

Ежедневно в 03:20, `crontab -e`:

```
20 3 * * * cd /opt/astra && ./deploy/backup.sh >> /var/log/astra-backup.log 2>&1
```

Настройки через переменные окружения: `BACKUP_DIR` (куда класть), `KEEP_DAYS`
(сколько хранить, по умолчанию 30).

**Копии должны лежать не только на этом сервере.** Диск умирает вместе с базой
и бэкапами одновременно. Настройте выгрузку каталога `backups/` наружу — rsync
на другую машину или `rclone` в облако.

Восстановление:

```bash
# проверка копии, рабочую базу не трогает
./deploy/restore.sh --into astra_check backups/db-20260902-032000.sql.gz

# настоящее восстановление: спросит подтверждение словом ВОССТАНОВИТЬ,
# остановит api/gateway/scheduler, зальёт базу и вложения, поднимет обратно
./deploy/restore.sh backups/db-20260902-032000.sql.gz \
                    backups/files-20260902-032000.tar.gz
```

Проверять восстановление раз в квартал на отдельной базе через `--into` —
единственный способ узнать, что копии рабочие, до того, как они понадобятся.

### 8. Что проверить после выкатки

```bash
# 1. Все контейнеры подняты, api и db — healthy
docker compose -f docker-compose.prod.yml ps

# 2. Миграции прошли
docker compose -f docker-compose.prod.yml logs migrate | tail -5

# 3. Сайт отвечает по HTTPS, а HTTP редиректит
curl -sI http://crm.example.ru/ | head -2        # ждём 301 и Location: https://
curl -s  https://crm.example.ru/healthz          # ждём ok — это проба самого nginx
curl -sI https://crm.example.ru/ | head -1       # ждём 200 — отдалась страница входа
# /health самого API наружу не выведен намеренно; смотрим изнутри контура:
docker compose -f docker-compose.prod.yml exec -T api curl -s localhost:8000/health
# ждём: {"status":"ok","demo_mode":false}

# 4. Демо-режим выключен
docker compose -f docker-compose.prod.yml exec -T api \
  python -c "from app.core.config import settings; print(settings.demo_mode, settings.app_env)"
# ждём: False production

# 5. Сертификат валиден и не самоподписанный
echo | openssl s_client -connect crm.example.ru:443 -servername crm.example.ru 2>/dev/null \
  | openssl x509 -noout -issuer -dates

# 6. Порты базы, Redis и MinIO снаружи закрыты
nmap -Pn -p 5432,6379,9000,9001 crm.example.ru   # ждём closed/filtered на всех

# 7. Живые обновления работают: открыть чат в двух браузерах,
#    отправить сообщение — во втором оно должно появиться без перезагрузки

# 8. Бэкап снимается
./deploy/backup.sh && ls -lh backups/
```

Отдельно, руками в интерфейсе: вход под руководителем, подключение первого
аккаунта Telegram (приходит код на телефон — значит, `DEMO_MODE=false` и ключи
`TELEGRAM_API_*` верные), отправка сообщения клиенту, загрузка вложения.

## Правила, которые не обсуждаются

- **Ни одной кнопки-обманки.** Элемент интерфейса либо работает, либо его нет.
- **Ошибка обязана быть видимой.** Интерфейс никогда не подменяет ответ сервера
  демо-данными — в прошлой версии это был самый дорогой класс багов.
- **Чужая запись для менеджера — 404, а не 403.** 403 подтверждает, что запись существует.
- **Деньги — целые копейки.** Дробных типов под деньги нет.
- **Сессии Telegram — главный секрет системы.** Шифруются в базе, ключ в переменной
  окружения, ни в репозиторий, ни в образ не попадают.

## Замечание про npm

В системном кэше npm (`~/.npm/_cacache`) часть файлов принадлежит root — следствие
когда-то выполненного `sudo npm`. Из-за этого установка падала с `EEXIST`. Обходится
локальным кэшем, он зафиксирован в `frontend/.npmrc`. Чинится глобально так:

```bash
sudo chown -R $(id -u):$(id -g) ~/.npm
```
