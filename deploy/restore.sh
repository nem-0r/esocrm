#!/usr/bin/env bash
#
# Восстановление Астра CRM из резервной копии, снятой deploy/backup.sh.
#
#   ./deploy/restore.sh backups/db-20260902-032000.sql.gz \
#                       backups/files-20260902-032000.tar.gz
#
#   # проверка копии на отдельной базе, рабочую не трогает:
#   ./deploy/restore.sh --into astra_check backups/db-20260902-032000.sql.gz
#
#   # только файлы:
#   ./deploy/restore.sh --files-only backups/files-20260902-032000.tar.gz
#
# Восстановление в рабочую базу необратимо, поэтому скрипт останавливает
# api/gateway/scheduler, спрашивает подтверждение словом ВОССТАНОВИТЬ и
# только потом пересоздаёт базу. С флагом --into ничего не останавливается
# и ничего не спрашивается: там работа идёт с отдельной базой.

set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

TARGET_DB=""
FILES_ONLY=0
ASSUME_YES=0
DB_DUMP=""
FILES_ARCHIVE=""

log()  { printf '[%s] %s\n' "$(date +'%H:%M:%S')" "$*"; }
fail() { printf '[%s] ОШИБКА: %s\n' "$(date +'%H:%M:%S')" "$*" >&2; exit 1; }

usage() {
    cat <<'TXT'
Использование:
  restore.sh [ключи] <db-*.sql.gz> [files-*.tar.gz]

Ключи:
  --into <база>   восстановить в отдельную базу (рабочая не трогается)
  --files-only    восстановить только вложения
  --yes           не спрашивать подтверждение (для скриптов)
  -h, --help      эта справка
TXT
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --into)       TARGET_DB="${2:-}"; [[ -n "$TARGET_DB" ]] || fail "--into без имени базы"; shift 2 ;;
        --files-only) FILES_ONLY=1; shift ;;
        --yes|-y)     ASSUME_YES=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        -*)           fail "неизвестный ключ: $1" ;;
        *)
            case "$(basename "$1")" in
                *.sql.gz)  DB_DUMP="$1" ;;
                *.tar.gz)  FILES_ARCHIVE="$1" ;;
                *)         fail "не понимаю файл: $1 (ожидается db-*.sql.gz или files-*.tar.gz)" ;;
            esac
            shift ;;
    esac
done

cd "$PROJECT_DIR"
[[ -f "$COMPOSE_FILE" ]] || fail "нет файла $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]]     || fail "нет файла $ENV_FILE"

get_env() {
    local key="$1" line
    line="$(grep -E "^[[:space:]]*${key}=" "$ENV_FILE" | tail -n 1 || true)"
    line="${line#*=}"; line="${line%%$'\r'}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

PG_USER="$(get_env POSTGRES_USER)";  PG_USER="${PG_USER:-astra}"
PG_DB="$(get_env POSTGRES_DB)";      PG_DB="${PG_DB:-astra}"
S3_BUCKET="$(get_env S3_BUCKET)";    S3_BUCKET="${S3_BUCKET:-astra-files}"

RESTORE_DB="${TARGET_DB:-$PG_DB}"
IS_LIVE=0
[[ "$RESTORE_DB" == "$PG_DB" ]] && IS_LIVE=1

dc()   { docker compose -f "$COMPOSE_FILE" "$@"; }
psql_() { dc exec -T db psql -v ON_ERROR_STOP=1 -U "$PG_USER" "$@"; }

# ---------------------------------------------------------------- база ------
if [[ "$FILES_ONLY" -eq 0 ]]; then
    [[ -n "$DB_DUMP" ]] || { usage; fail "не указан дамп базы"; }
    [[ -s "$DB_DUMP" ]] || fail "файл $DB_DUMP пустой или не существует"
    gzip -t "$DB_DUMP" 2>/dev/null || fail "$DB_DUMP не открывается как gzip — копия повреждена"

    if [[ "$IS_LIVE" -eq 1 && "$ASSUME_YES" -eq 0 ]]; then
        echo
        echo "  Восстановление в РАБОЧУЮ базу «$RESTORE_DB»."
        echo "  Всё, что сейчас в ней есть, будет стёрто и заменено копией"
        echo "  из файла $DB_DUMP."
        echo
        printf '  Наберите ВОССТАНОВИТЬ для продолжения: '
        read -r answer
        [[ "$answer" == "ВОССТАНОВИТЬ" ]] || fail "отменено пользователем"
    fi

    if [[ "$IS_LIVE" -eq 1 ]]; then
        log "останавливаю api, gateway, scheduler — они держат соединения с базой"
        dc stop api gateway scheduler
    fi

    log "пересоздаю базу $RESTORE_DB"
    # Отцепляем оставшиеся соединения, иначе DROP DATABASE не пройдёт.
    psql_ -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
          WHERE datname = '$RESTORE_DB' AND pid <> pg_backend_pid();" > /dev/null
    psql_ -d postgres -c "DROP DATABASE IF EXISTS \"$RESTORE_DB\";" > /dev/null
    psql_ -d postgres -c "CREATE DATABASE \"$RESTORE_DB\" OWNER \"$PG_USER\";" > /dev/null

    log "заливаю дамп $(basename "$DB_DUMP")"
    gzip -cd "$DB_DUMP" | dc exec -T db psql -v ON_ERROR_STOP=1 -q -U "$PG_USER" -d "$RESTORE_DB" > /dev/null

    RESTORED_TABLES=$(psql_ -d "$RESTORE_DB" -tAc \
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d ' \r')
    log "в базе $RESTORE_DB таблиц: $RESTORED_TABLES"
    [[ "$RESTORED_TABLES" -gt 0 ]] || fail "после восстановления в базе нет таблиц"

    if [[ "$IS_LIVE" -eq 1 ]]; then
        log "поднимаю api, gateway, scheduler"
        dc start api gateway scheduler
    fi
fi

# --------------------------------------------------------------- файлы -----
if [[ -n "$FILES_ARCHIVE" ]]; then
    [[ -s "$FILES_ARCHIVE" ]] || fail "файл $FILES_ARCHIVE пустой или не существует"
    gzip -t "$FILES_ARCHIVE" 2>/dev/null || fail "$FILES_ARCHIVE не открывается как gzip — копия повреждена"

    STORAGE_CID="$(dc ps -q storage)"
    [[ -n "$STORAGE_CID" ]] || fail "контейнер storage не запущен"

    STAGING="$(mktemp -d "${TMPDIR:-/tmp}/astra-restore.XXXXXX")"
    TMP_IN_CONTAINER="/tmp/astra-restore-$$"
    cleanup() {
        rm -rf "$STAGING"
        docker exec "$STORAGE_CID" rm -rf "$TMP_IN_CONTAINER" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    log "распаковываю $(basename "$FILES_ARCHIVE")"
    tar -C "$STAGING" -xzf "$FILES_ARCHIVE"
    [[ -d "$STAGING/files" ]] || fail "в архиве нет каталога files — это не копия вложений"

    COUNT=$(find "$STAGING/files" -type f | wc -l | tr -d ' ')
    log "файлов в копии: $COUNT"

    docker cp "$STAGING/files" "$STORAGE_CID:$TMP_IN_CONTAINER" > /dev/null

    # --overwrite: файлы неизменяемые, но повторный прогон должен быть безопасным.
    # Без --remove: лишнее в хранилище не удаляем, восстановление не должно
    # уносить то, что появилось после снятия копии.
    docker exec "$STORAGE_CID" sh -c "
        set -e
        mc alias set bk http://127.0.0.1:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" > /dev/null
        mc mb --ignore-existing 'bk/$S3_BUCKET' > /dev/null
        mc mirror --quiet --overwrite '$TMP_IN_CONTAINER' 'bk/$S3_BUCKET' > /dev/null
    "
    log "вложения восстановлены в бакет $S3_BUCKET"
fi

log "готово"
if [[ "$IS_LIVE" -eq 0 && "$FILES_ONLY" -eq 0 ]]; then
    log "проверочная база называется $RESTORE_DB; удалить её:"
    log "  docker compose -f $COMPOSE_FILE exec -T db dropdb -U $PG_USER $RESTORE_DB"
fi
