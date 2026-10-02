#!/usr/bin/env bash
#
# Применить план ресурсов к работающему серверу — без выкатки новой версии.
# Нужен после апгрейда сервера (больше ядер/памяти): процессы шлюза и api подстроятся
# сами при перезапуске контейнеров, а потолки памяти и настройки базы — только после
# пересчёта плана (docs/17-resource-autoscaling.md §5.3).
#
#   ./deploy/apply-resources.sh                # показать план, спросить, применить
#   ./deploy/apply-resources.sh --yes          # применить без вопроса
#   ./deploy/apply-resources.sh --if-changed   # применить, только если план изменился
#                                              # (для запуска при загрузке сервера)
#   ./deploy/apply-resources.sh --dry-run      # только показать
#
# Что делает: пересчитывает план → записывает блок в .env → `docker compose up -d`
# пересоздаёт только сервисы, у которых значения изменились (база — 10–30 с простоя) →
# ждёт здоровья. Не вышло — возвращает прежний блок и прежние значения.
#
# Параллельно с выкаткой не работает: обе берут одну блокировку.

set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_DIR"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
PLAN_PREVIOUS="$PROJECT_DIR/deploy/.resource-plan.previous"
# Сервисы, которые пересоздаёт и проверяет скрипт. Меняются только на репетиции
# (там поднимают не весь стек): `RESOURCE_SERVICES="db cache" HEALTH_SERVICES=db
# HEALTH_URL=- RESTART_NGINX=false`.
RESOURCE_SERVICES="${RESOURCE_SERVICES:-db cache storage api gateway scheduler web}"
HEALTH_SERVICES="${HEALTH_SERVICES:-api web nginx}"
RESTART_NGINX="${RESTART_NGINX:-true}"
# Подставить размер сервера руками (репетиция, «а если сервер станет таким?»):
# `RESOURCE_PLAN_ARGS="--cores 6 --ram-mb 16384"`. На боевом сервере не задаётся.
PLAN_ARGS=()
read -r -a PLAN_ARGS <<< "${RESOURCE_PLAN_ARGS:-}"
plan() { python3 deploy/resource_plan.py --env-file "$ENV_FILE" ${PLAN_ARGS[@]+"${PLAN_ARGS[@]}"} "$@"; }

ASSUME_YES=false
DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=true ;;
        --if-changed) ASSUME_YES=true ;;  # без вопроса; «если не изменилось» и так выходит сразу
        --dry-run) DRY_RUN=true ;;
        *) echo "неизвестный параметр: $arg" >&2; exit 2 ;;
    esac
done

log() { printf '[%s] %s\n' "$(date +'%H:%M:%S')" "$*"; }
dc()  { docker compose -f "$COMPOSE_FILE" "$@"; }

LOCK_FILE="${LOCK_FILE:-$PROJECT_DIR/deploy/.deploy.lock}"
if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "Идёт выкатка или другое применение плана — дождитесь конца" >&2
        exit 75
    fi
else
    echo "(flock не найден — защита от двойного запуска не работает; на сервере он есть)" >&2
fi

[[ -f "$ENV_FILE" ]] || { echo "нет $ENV_FILE" >&2; exit 1; }
DOMAIN="$(grep -E '^[[:space:]]*DOMAIN=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '"\r')"
HEALTH_URL="${HEALTH_URL:-https://$DOMAIN}"

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) service cid state ok
    while (( SECONDS < deadline )); do
        ok=true
        for service in $HEALTH_SERVICES; do
            cid="$(dc ps -q "$service" 2>/dev/null | head -n 1)"
            state="absent"
            [[ -z "$cid" ]] || state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid")"
            [[ "$state" == "healthy" ]] || ok=false
        done
        if [[ "$ok" == true ]] && { [[ "$HEALTH_URL" == "-" ]] || curl -fsS -m 10 "$HEALTH_URL/health" | grep -q '"status":"ok"'; }; then
            return 0
        fi
        sleep 3
    done
    return 1
}

log "расчёт плана ресурсов"
# Код возврата читаем явно: после `if ! команда` он уже обнулён отрицанием.
if plan --check; then
    plan_rc=0
else
    plan_rc=$?
fi
case "$plan_rc" in
    0)  CHANGED=false ;;
    10) CHANGED=true ;;
    *)  log "план не посчитался (код $plan_rc)"; exit 1 ;;
esac

if [[ "$CHANGED" != true ]]; then
    log "план уже актуален — менять нечего"
    exit 0
fi
[[ "$DRY_RUN" != true ]] || { log "(режим --dry-run: ничего не меняю)"; exit 0; }

if [[ "$ASSUME_YES" != true ]]; then
    read -r -p "Применить? База перезапустится на 10–30 секунд. [y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" ]] || { log "отменено"; exit 0; }
fi

# Коды скрипта плана: 0 — не изменилось, 10 — изменилось (это успех), остальное — сбой.
if plan --apply --save-previous "$PLAN_PREVIOUS" >/dev/null; then plan_rc=0; else plan_rc=$?; fi
[[ "$plan_rc" == 0 || "$plan_rc" == 10 ]] || { log "план не записался (код $plan_rc)"; exit 1; }
if ! dc config -q >/dev/null 2>&1; then
    log "новый план не проходит проверку compose — возвращаю прежний"
    plan --restore "$PLAN_PREVIOUS" >/dev/null || true
    exit 1
fi

log "применяю: пересоздаются только сервисы с изменившимися значениями"
# Список сервисов нарочно разбивается на слова:
# shellcheck disable=SC2086
dc up -d --no-build --no-deps $RESOURCE_SERVICES
# api/web могли быть пересозданы с новыми адресами — nginx держит старые.
[[ "$RESTART_NGINX" != true ]] || dc restart nginx >/dev/null

if wait_healthy; then
    log "готово: сервисы здоровы, $HEALTH_URL отвечает"
    exit 0
fi

log "СЕРВИСЫ НЕ СТАЛИ ЗДОРОВЫМИ — возвращаю прежний план"
plan --restore "$PLAN_PREVIOUS" >/dev/null || true
# shellcheck disable=SC2086
dc up -d --no-build --no-deps $RESOURCE_SERVICES || true
[[ "$RESTART_NGINX" != true ]] || dc restart nginx >/dev/null || true
if wait_healthy; then
    log "прежний план восстановлен, сервисы здоровы"
else
    log "ВНИМАНИЕ: и с прежним планом сервисы не отвечают — нужен человек"
fi
exit 1
