#!/usr/bin/env bash
#
# Резервное копирование Астра CRM: база + файлы вложений.
#
#   ./deploy/backup.sh                    # обычный запуск (продакшен-контур)
#   COMPOSE_FILE=docker-compose.yml ./deploy/backup.sh   # контур разработки
#   BACKUP_DIR=/mnt/backups ./deploy/backup.sh
#   KEEP_DAYS=90 ./deploy/backup.sh
#
# По cron раз в сутки в 03:20 (crontab -e):
#   20 3 * * * cd /opt/astra && ./deploy/backup.sh >> /var/log/astra-backup.log 2>&1
#
# Скрипт намеренно падает с ненулевым кодом при любой неполадке и проверяет
# результат: молча записанный пустой дамп — худший исход из возможных, потому
# что о нём узнают в день, когда бэкап понадобился.

set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

STAMP="$(date +%Y%m%d-%H%M%S)"
DB_FILE="$BACKUP_DIR/db-$STAMP.sql.gz"
FILES_FILE="$BACKUP_DIR/files-$STAMP.tar.gz"

log()  { printf '[%s] %s\n' "$(date +'%H:%M:%S')" "$*"; }
fail() { printf '[%s] ОШИБКА: %s\n' "$(date +'%H:%M:%S')" "$*" >&2; exit 1; }

trap 'fail "прервано на строке $LINENO"' ERR

cd "$PROJECT_DIR"

[[ -f "$COMPOSE_FILE" ]] || fail "нет файла $COMPOSE_FILE (PROJECT_DIR=$PROJECT_DIR)"
[[ -f "$ENV_FILE" ]]     || fail "нет файла $ENV_FILE — из него берутся имя базы и бакет"

# Читаем .env, не выполняя его: строки вида KEY=value, комментарии пропускаем.
get_env() {
    local key="$1" line
    line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%%$'\r'}"
    # снимаем кавычки, если есть
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

PG_USER="$(get_env POSTGRES_USER)";  PG_USER="${PG_USER:-astra}"
PG_DB="$(get_env POSTGRES_DB)";      PG_DB="${PG_DB:-astra}"
S3_BUCKET="$(get_env S3_BUCKET)";    S3_BUCKET="${S3_BUCKET:-astra-files}"

dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

mkdir -p "$BACKUP_DIR"

# ---------------------------------------------------------------- база ------
log "дамп базы $PG_DB → $(basename "$DB_FILE")"

# --no-owner/--no-privileges: восстанавливаться дамп должен в любую базу под
# любой ролью, а не только под тем пользователем, под которым снимался.
# Формат plain: файл читается глазами и восстанавливается обычным psql,
# без совпадения версий pg_restore.
dc exec -T db pg_dump \
    -U "$PG_USER" -d "$PG_DB" \
    --format=plain --no-owner --no-privileges \
  | gzip -9 > "$DB_FILE"

# Пайп прячет код возврата pg_dump, поэтому результат проверяем по содержимому.
[[ -s "$DB_FILE" ]] || fail "дамп базы пустой: $DB_FILE"

DB_BYTES=$(wc -c < "$DB_FILE" | tr -d ' ')
TABLES=$(gzip -cd "$DB_FILE" | grep -c '^CREATE TABLE' || true)
COPIES=$(gzip -cd "$DB_FILE" | grep -c '^COPY ' || true)

[[ "$TABLES" -gt 0 ]] || fail "в дампе нет ни одной команды CREATE TABLE — база снялась пустой"

log "база: $DB_BYTES байт, таблиц $TABLES, блоков данных $COPIES"

# --------------------------------------------------------------- файлы -----
log "выгрузка бакета $S3_BUCKET → $(basename "$FILES_FILE")"

STORAGE_CID="$(dc ps -q storage)"
[[ -n "$STORAGE_CID" ]] || fail "контейнер storage не запущен"

TMP_IN_CONTAINER="/tmp/astra-backup-$STAMP"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/astra-backup.XXXXXX")"
cleanup() {
    rm -rf "$STAGING"
    docker exec "$STORAGE_CID" rm -rf "$TMP_IN_CONTAINER" >/dev/null 2>&1 || true
}
trap 'cleanup; fail "прервано на строке $LINENO"' ERR
trap cleanup EXIT

# mc уже лежит в образе MinIO. Алиас local в образе есть, но без ключей —
# он нужен только healthcheck-у, поэтому заводим свой.
docker exec "$STORAGE_CID" sh -c "
    set -e
    mc alias set bk http://127.0.0.1:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" > /dev/null
    rm -rf '$TMP_IN_CONTAINER'
    mkdir -p '$TMP_IN_CONTAINER'
    mc mirror --quiet 'bk/$S3_BUCKET' '$TMP_IN_CONTAINER' > /dev/null
"

# В образе MinIO нет tar — забираем каталог наружу через docker cp и жмём тут.
docker cp "$STORAGE_CID:$TMP_IN_CONTAINER" "$STAGING/files" > /dev/null
tar -C "$STAGING" -czf "$FILES_FILE" files

[[ -s "$FILES_FILE" ]] || fail "архив файлов пустой: $FILES_FILE"

FILES_COUNT=$(find "$STAGING/files" -type f | wc -l | tr -d ' ')
FILES_BYTES=$(wc -c < "$FILES_FILE" | tr -d ' ')
log "файлы: $FILES_COUNT шт., архив $FILES_BYTES байт"

# ------------------------------------------------------------- ротация -----
# Удаляем только свои файлы и только в каталоге бэкапов, без рекурсии:
# случайно снести что-то ещё этот find не должен уметь.
log "чистка старше $KEEP_DAYS дней"
DELETED=0
while IFS= read -r old; do
    [[ -n "$old" ]] || continue
    rm -f "$old"
    DELETED=$((DELETED + 1))
    log "  удалён $(basename "$old")"
done < <(find "$BACKUP_DIR" -maxdepth 1 -type f \
              \( -name 'db-*.sql.gz' -o -name 'files-*.tar.gz' \) \
              -mtime "+$KEEP_DAYS" 2>/dev/null || true)

REMAIN=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'db-*.sql.gz' | wc -l | tr -d ' ')
log "удалено старых файлов: $DELETED, дампов базы в хранилище: $REMAIN"

log "готово:"
log "  $DB_FILE"
log "  $FILES_FILE"
