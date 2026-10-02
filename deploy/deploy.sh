#!/usr/bin/env bash
#
# Выкатка версии на сервер. Запускает CI по SSH (.github/workflows/deploy.yml)
# из каталога проекта, когда рабочая копия уже переведена на нужный коммит.
# Можно запустить и руками — ровно тем же способом.
#
#   IMAGE_TAG=sha-<коммит> BACKEND_IMAGE=ghcr.io/<владелец>/<репо>-backend \
#   WEB_IMAGE=ghcr.io/<владелец>/<репо>-web PREV_SHA=<прошлый коммит> \
#   ./deploy/deploy.sh
#
# Необязательные:
#   GHCR_USER / GHCR_TOKEN — вход в реестр образов (CI передаёт свой токен);
#   COMPOSE_FILE           — по умолчанию docker-compose.prod.yml;
#   HEALTH_URL             — адрес проверки здоровья (по умолчанию https://$DOMAIN);
#   MIN_FREE_GB            — сколько места на диске нужно для выкатки (5).
#
# Шаги (docs/16-ci-cd.md):
#   1. место на диске и файл .env;
#   2. скачать образы, прошедшие проверки (не вышло — собрать здесь же);
#   3. есть новые миграции — сначала бэкап базы; база новее кода (возврат на
#      прошлую версию) — миграции не трогаются вовсе;
#   3б. план ресурсов под размер сервера (deploy/resource_plan.py): потолки памяти и
#      настройки базы пересчитываются и пишутся в .env; изменилось — compose
#      пересоздаст затронутые сервисы (docs/17-resource-autoscaling.md §5.3);
#   4. миграции, затем api/шлюз/планировщик/веб; перезапуск nginx;
#   5. проверка здоровья снаружи;
#   6. успех — убрать бэкап этой выкатки и старые образы;
#      неуспех — откат на прошлую версию (и прежний план ресурсов), бэкап остаётся.
#
# Миграции назад не откатываются: они только расширяющие (docs/05, D-28),
# и прошлая версия кода работает с новой схемой.
#
# PROJECT_DIR — каталог проекта, если сценарий запущен не из него (CI так
# выкатывает версию, в которой своего deploy.sh ещё нет, — см. deploy.yml).

set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"

# Две выкатки (или выкатка и deploy/apply-resources.sh) одновременно на один сервер —
# никогда: обе пересоздают контейнеры. Вторая сразу сообщает и не трогает ничего,
# в том числе код на сервере (поэтому проверка стоит раньше отката).
LOCK_FILE="${LOCK_FILE:-$PROJECT_DIR/deploy/.deploy.lock}"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "Другая выкатка или применение плана ресурсов уже идёт — дождитесь её конца" >&2
        exit 75
    fi
else
    echo "(flock не найден — защита от двойного запуска не работает; на сервере он есть)" >&2
fi
MIN_FREE_GB="${MIN_FREE_GB:-5}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
KEEP_IMAGES="${KEEP_IMAGES:-3}"
PREV_SHA="${PREV_SHA:-}"
IMAGE_TAG="${IMAGE_TAG:?укажите IMAGE_TAG (например sha-<коммит>)}"
BACKEND_IMAGE="${BACKEND_IMAGE:-astra-backend}"
WEB_IMAGE="${WEB_IMAGE:-astra-web}"
export IMAGE_TAG BACKEND_IMAGE WEB_IMAGE

log()  { printf '[%s] %s\n' "$(date +'%H:%M:%S')" "$*"; }
# Вернуть блок плана ресурсов к сохранённому. Код 10 — «изменился», это успех.
restore_plan() {
    local rc=0
    python3 deploy/resource_plan.py --env-file "$ENV_FILE" --restore "$PLAN_PREVIOUS" >/dev/null 2>&1 || rc=$?
    [[ "$rc" == 0 || "$rc" == 10 ]]
}
# Любая остановка выкатки идёт через откат: код на сервере возвращается на
# прошлый коммит, чтобы рабочая копия совпадала с тем, что реально запущено.
fail() { rollback "$*"; }
dc()   { docker compose -f "$COMPOSE_FILE" "$@"; }

get_env() {
    local line
    line="$(grep -E "^[[:space:]]*$1=" "$ENV_FILE" | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%%$'\r'}"
    line="${line%\"}"; line="${line#\"}"
    printf '%s' "$line"
}

# Записать KEY=value в .env: заменить строку или дописать. Нужно, чтобы ручной
# `docker compose up -d` после выкатки поднимал ту же версию, а не собирал
# что-то своё из исходников.
set_env() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp "$ENV_FILE.XXXXXX")"
    if grep -qE "^[[:space:]]*$key=" "$ENV_FILE"; then
        awk -v k="$key" -v v="$value" \
            'BEGIN{done=0} $0 ~ "^[[:space:]]*"k"=" {if(!done){print k"="v; done=1}; next} {print}' \
            "$ENV_FILE" > "$tmp"
    else
        cp "$ENV_FILE" "$tmp"
        printf '%s=%s\n' "$key" "$value" >> "$tmp"
    fi
    cat "$tmp" > "$ENV_FILE"   # сохраняем права и владельца исходного файла
    rm -f "$tmp"
}

# Убрать KEY из .env. Нужно при откате на версию до CI: в .env должно остаться
# то, что реально запущено, а не имена образов неудачной выкатки.
unset_env() {
    local key="$1" tmp
    grep -qE "^[[:space:]]*$key=" "$ENV_FILE" || return 0
    tmp="$(mktemp "$ENV_FILE.XXXXXX")"
    grep -vE "^[[:space:]]*$key=" "$ENV_FILE" > "$tmp" || true
    cat "$tmp" > "$ENV_FILE"   # сохраняем права и владельца исходного файла
    rm -f "$tmp"
}

BACKUP_FILE=""
PLAN_PREVIOUS="$PROJECT_DIR/deploy/.resource-plan.previous"
PLAN_CHANGED=false
BUILT_LOCALLY=false
STARTED=false
SCHEMA_CHANGED=false
PREV_IMAGE_TAG=""
PREV_BACKEND_IMAGE=""
PREV_WEB_IMAGE=""

# ------------------------------------------------------------ откат
rollback() {
    local reason="$1"
    trap - ERR
    log "ВЫКАТКА НЕ УДАЛАСЬ: $reason"
    if [[ "$PLAN_CHANGED" == true ]]; then
        # План ресурсов — часть этой выкатки: .env должен остаться таким, каким был.
        if restore_plan; then
            log "план ресурсов возвращён к прежнему"
        else
            log "не удалось вернуть прежний план ресурсов — проверьте блок в .env"
        fi
    fi
    if [[ -n "$PREV_SHA" ]]; then
        git checkout -q -B main "$PREV_SHA" || log "не удалось вернуть код на $PREV_SHA"
        log "код на сервере возвращён на $PREV_SHA"
    fi
    if [[ "$STARTED" != true ]]; then
        log "рабочие контейнеры не трогали — сервис работает на прошлой версии"
    else
        log "откат на прошлую версию (${PREV_SHA:-неизвестна}, образы ${PREV_IMAGE_TAG:-собранные локально})"
        if [[ -n "$PREV_IMAGE_TAG" ]]; then
            set_env IMAGE_TAG "$PREV_IMAGE_TAG"
            set_env BACKEND_IMAGE "${PREV_BACKEND_IMAGE:-astra-backend}"
            set_env WEB_IMAGE "${PREV_WEB_IMAGE:-astra-web}"
            export IMAGE_TAG="$PREV_IMAGE_TAG" BACKEND_IMAGE="${PREV_BACKEND_IMAGE:-astra-backend}"
            export WEB_IMAGE="${PREV_WEB_IMAGE:-astra-web}"
        else
            # До первой выкатки через CI образы собирались на сервере со старыми
            # именами — прошлый docker-compose.prod.yml их и поднимет. Имена
            # образов неудачной версии из .env убираем: иначе следующая выкатка
            # приняла бы их за «прошлую версию» и при сбое откатилась бы не туда.
            unset_env IMAGE_TAG
            unset_env BACKEND_IMAGE
            unset_env WEB_IMAGE
            unset IMAGE_TAG BACKEND_IMAGE WEB_IMAGE
        fi
        # Миграции назад не крутим: они расширяющие, прошлый код с ними работает.
        # --no-deps обязателен: иначе compose запустил бы migrate прошлой версии,
        # а та не знает новую ревизию базы, падает — и api за ней не стартует.
        if [[ "$PLAN_CHANGED" == true ]]; then
            # База, redis и хранилище файлов могли быть пересозданы по новому плану —
            # возвращаем им прежние значения (compose пересоздаст только изменившиеся).
            dc up -d --no-deps --no-build db cache storage || log "откат: база/redis/хранилище не поднялись"
        fi
        dc up -d --no-deps --no-build api gateway scheduler web || log "откат: контейнеры не поднялись"
        dc restart nginx || true
        if wait_healthy; then
            log "прошлая версия снова работает"
        else
            log "ВНИМАНИЕ: и прошлая версия не отвечает — нужен человек"
        fi
        if [[ "$SCHEMA_CHANGED" == true ]]; then
            log "схема базы теперь новее кода: перезапускать только без migrate —"
            log "  docker compose -f $COMPOSE_FILE up -d --no-deps api gateway scheduler web"
            log "  (обычный «up -d» запустит migrate прошлой версии, и она упадёт)"
        fi
    fi
    if [[ -n "$BACKUP_FILE" ]]; then
        log "бэкап базы до выкатки сохранён: $BACKUP_FILE"
    fi
    exit 1
}
trap 'rollback "ошибка на строке $LINENO"' ERR

[[ -f "$COMPOSE_FILE" ]] || fail "нет $COMPOSE_FILE в $PROJECT_DIR"
[[ -f "$ENV_FILE" ]] || fail "нет $ENV_FILE — без него сервисы не поднять"

DOMAIN="$(get_env DOMAIN)"
HEALTH_URL="${HEALTH_URL:-https://$DOMAIN}"
[[ "$HEALTH_URL" != "https://" ]] || fail "в .env нет DOMAIN — не по чему проверить здоровье"

PREV_IMAGE_TAG="$(get_env IMAGE_TAG)"
PREV_BACKEND_IMAGE="$(get_env BACKEND_IMAGE)"
PREV_WEB_IMAGE="$(get_env WEB_IMAGE)"

# ------------------------------------------------------------ проверки
container_health() {
    local cid
    cid="$(dc ps -q "$1" 2>/dev/null | head -n 1)"
    [[ -n "$cid" ]] || { echo "absent"; return; }
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid"
}

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) service state all_ok
    while (( SECONDS < deadline )); do
        all_ok=true
        for service in api web nginx; do
            state="$(container_health "$service")"
            [[ "$state" == "healthy" ]] || all_ok=false
        done
        state="$(container_health gateway)"
        [[ "$state" == "running" ]] || all_ok=false
        if [[ "$all_ok" == true ]] \
            && curl -fsS -m 10 "$HEALTH_URL/health" | grep -q '"status":"ok"' \
            && curl -fsS -m 10 -o /dev/null "$HEALTH_URL/"; then
            return 0
        fi
        sleep 3
    done
    return 1
}

gateway_restarts() {
    local cid
    cid="$(dc ps -q gateway | head -n 1)"
    [[ -n "$cid" ]] && docker inspect -f '{{.RestartCount}}' "$cid" || echo 0
}

# ------------------------------------------------------------ 1. место
free_kb="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
(( free_kb > MIN_FREE_GB * 1024 * 1024 )) \
    || fail "на диске меньше ${MIN_FREE_GB} ГБ свободно ($((free_kb / 1024)) МБ) — сначала освободите место"
log "свободно на диске: $((free_kb / 1024 / 1024)) ГБ"

# ------------------------------------------------------------ 2. образы
if [[ -n "${GHCR_TOKEN:-}" ]]; then
    printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USER:-ci}" --password-stdin >/dev/null
fi
log "образы $BACKEND_IMAGE:$IMAGE_TAG и $WEB_IMAGE:$IMAGE_TAG"
# Проверяем, что образ действительно появился: у версий до CI в compose нет
# имён из реестра, и pull «успешно» пропускает их, ничего не скачав.
if dc pull --quiet api web \
    && docker image inspect "$BACKEND_IMAGE:$IMAGE_TAG" "$WEB_IMAGE:$IMAGE_TAG" >/dev/null 2>&1; then
    log "образы скачаны — ровно те, что прошли проверки"
else
    # Реестр недоступен или образа нет (коммит до CI): собираем здесь же из
    # рабочей копии — так выкатывали и раньше. Все службы: в compose до CI у
    # каждой свой образ, и старые образы шлюза не должны остаться от другой версии.
    log "скачать не вышло — собираю на сервере из коммита $(git rev-parse --short HEAD)"
    dc build
    BUILT_LOCALLY=true
fi
if [[ -n "${GHCR_TOKEN:-}" ]]; then
    docker logout ghcr.io >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------ 3. схема и бэкап
# Ревизию базы читаем прямо из неё, а не через alembic образа: alembic прошлой
# версии новую ревизию не знает и вместо ответа падает.
PG_USER="$(get_env POSTGRES_USER)"; PG_USER="${PG_USER:-astra}"
PG_DB="$(get_env POSTGRES_DB)";     PG_DB="${PG_DB:-astra}"
db_rev="$(dc exec -T db psql -U "$PG_USER" -d "$PG_DB" -tAc 'select version_num from alembic_version' 2>/dev/null \
    | tr -d '[:space:]' || true)"
head_rev="$(dc run --rm --no-deps -T migrate alembic heads 2>/dev/null | awk '/^[0-9a-f]+/ {print $1}' | tail -n 1 || true)"
[[ -n "$head_rev" ]] || fail "не удалось узнать версию схемы в новом образе"
SCHEMA_AHEAD=false
if [[ -n "$db_rev" && "$db_rev" != "$head_rev" ]] \
    && ! dc run --rm --no-deps -T migrate alembic show "$db_rev" >/dev/null 2>&1; then
    # Ревизии базы нет среди миграций этой версии: база новее кода — это возврат
    # на прошлую версию. Миграции назад не крутим (D-28): прошлый код работает с
    # новой схемой, а его migrate новую ревизию не знает и упал бы.
    SCHEMA_AHEAD=true
    log "схема базы ($db_rev) новее этой версии ($head_rev) — возврат на прошлую версию, миграции не трогаю"
elif [[ "$db_rev" != "$head_rev" ]]; then
    log "в новой версии миграции (${db_rev:-нет} → $head_rev) — сначала бэкап базы"
    BACKUP_FILE="$(./deploy/backup.sh --db-only | tail -n 1)"
    [[ -s "$BACKUP_FILE" ]] || fail "бэкап базы не получился — выкатку не начинаю"
    log "бэкап: $BACKUP_FILE"
else
    log "миграций нет (схема $db_rev) — бэкап не нужен"
fi

# ------------------------------------------------------------ 3б. план ресурсов
# Потолки памяти и настройки базы по размеру сервера. Необязательный шаг: нет скрипта,
# нет python3 или план не посчитался — выкатка идёт с прежними значениями (compose берёт
# значения по умолчанию, равные нынешним). Если значения изменились, `up -d` ниже сам
# пересоздаст затронутые сервисы — например, базу с новым shared_buffers (10–30 с).
if [[ -f deploy/resource_plan.py ]] && command -v python3 >/dev/null 2>&1; then
    if python3 deploy/resource_plan.py --env-file "$ENV_FILE" --apply --save-previous "$PLAN_PREVIOUS"; then
        plan_rc=0
    else
        plan_rc=$?
    fi
    case "$plan_rc" in
        0)  log "план ресурсов актуален" ;;
        10)
            PLAN_CHANGED=true
            if dc config -q >/dev/null 2>&1; then
                log "план ресурсов обновлён под размер сервера"
            else
                log "новый план не проходит проверку compose — возвращаю прежний"
                restore_plan || true
                PLAN_CHANGED=false
            fi
            ;;
        *)  log "план ресурсов не применился (код $plan_rc) — иду с прежними значениями" ;;
    esac
else
    log "план ресурсов пропущен (нет deploy/resource_plan.py или python3)"
fi

# ------------------------------------------------------------ 4. запуск
set_env IMAGE_TAG "$IMAGE_TAG"
set_env BACKEND_IMAGE "$BACKEND_IMAGE"
set_env WEB_IMAGE "$WEB_IMAGE"
STARTED=true

if [[ "$SCHEMA_AHEAD" == true ]]; then
    log "перезапуск сервисов (без migrate — схема базы новее кода)"
    [[ "$PLAN_CHANGED" != true ]] || log "новый план ресурсов для базы применится при следующей обычной выкатке"
    dc up -d --no-build --no-deps --remove-orphans api gateway scheduler web
else
    log "миграции"
    dc run --rm -T migrate || rollback "миграция не прошла"
    [[ "$db_rev" == "$head_rev" ]] || SCHEMA_CHANGED=true
    log "перезапуск сервисов"
    dc up -d --no-build --remove-orphans
fi
# api/web пересозданы с новыми адресами — nginx держит старые, пока его не
# перезапустить (README, «Повторный выкат»).
dc restart nginx

# ------------------------------------------------------------ 5. здоровье
restarts_before="$(gateway_restarts)"
wait_healthy || rollback "сервисы не стали здоровыми за ${HEALTH_TIMEOUT} с"
sleep 15
restarts_after="$(gateway_restarts)"
[[ "$restarts_after" == "$restarts_before" ]] || rollback "шлюз Telegram перезапускается после старта"
log "здоровье: api, web, nginx и шлюз в порядке, $HEALTH_URL отвечает"

# ------------------------------------------------------------ 6. уборка
trap - ERR
if [[ -n "$BACKUP_FILE" ]]; then
    rm -f "$BACKUP_FILE"
    log "бэкап этой выкатки удалён — версия работает (он был страховкой на момент перехода)"
fi
printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$(git rev-parse HEAD)" "$IMAGE_TAG" >> deploy/.releases
# Последние $KEEP_IMAGES версий оставляем — на них откатываются без сборки.
for repo in "$BACKEND_IMAGE" "$WEB_IMAGE"; do
    docker image ls "$repo" --format '{{.CreatedAt}}\t{{.Tag}}' | sort -r | tail -n +$((KEEP_IMAGES + 1)) \
        | cut -f2 | while read -r old; do
            [[ -n "$old" && "$old" != "$IMAGE_TAG" && "$old" != "$PREV_IMAGE_TAG" ]] || continue
            docker image rm "$repo:$old" >/dev/null 2>&1 && log "удалён старый образ $repo:$old" || true
        done
done
docker image prune -f >/dev/null 2>&1 || true
if [[ "$BUILT_LOCALLY" == true ]]; then
    docker builder prune -af >/dev/null 2>&1 || true
fi
log "готово: выкачена $(git rev-parse --short HEAD) ($IMAGE_TAG)"
