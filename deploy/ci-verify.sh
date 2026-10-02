#!/usr/bin/env bash
#
# Полная проверка на тестовом стенде (docker-compose.ci.yml) — то же самое,
# что делает CI в GitHub. Стенд должен быть уже поднят: `make ci-up`.
#
#   ./deploy/ci-verify.sh            # всё
#   ./deploy/ci-verify.sh unit       # только модульные тесты
#
# Порядок не случайный: наборы, которые меняют демо-данные (verify_actions),
# идут последними, перед ними — свежая заливка демо-данных.
# verify_rollback_compat — после наборов, которые пишут данные: проверяет, что
# всё записанное прочтёт и прошлая версия (на неё возвращает автооткат).
# verify_robokassa здесь нет намеренно: он создаёт настоящие счета в боевом
# магазине Робокассы (docs/11-payments-architecture.md) — не для автоматики.

set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

COMPOSE=(docker compose -f docker-compose.ci.yml)
FAILED=()

run() {
    local title="$1"
    shift
    printf '\n\033[1m▶ %s\033[0m\n' "$title"
    if ! "${COMPOSE[@]}" exec -T api "$@"; then
        FAILED+=("$title")
    fi
}

seed() {
    printf '\n\033[1m▶ демо-данные\033[0m\n'
    "${COMPOSE[@]}" exec -T api python -m app.seed.run
}

only="${1:-all}"

run "модульные тесты" python -m pytest tests/unit -q -p no:cacheprovider
if [[ "$only" == "unit" ]]; then
    [[ ${#FAILED[@]} -eq 0 ]] || { echo "Упали: ${FAILED[*]}"; exit 1; }
    exit 0
fi

seed
for suite in smoke_api verify_miro verify_analytics verify_tz verify_gateway verify_payments \
             verify_exports verify_media verify_sync verify_forward verify_services \
             verify_robustness verify_governor verify_outbox verify_rollback_compat; do
    run "$suite" python -m "tests.$suite"
done

seed
run "verify_actions" python -m tests.verify_actions
run "verify_rollback_compat (после verify_actions)" python -m tests.verify_rollback_compat

# Обещание «потеря Redis ничего не ломает» — проверяем, погасив Redis.
printf '\n\033[1m▶ работа без Redis\033[0m\n'
"${COMPOSE[@]}" stop cache >/dev/null
sleep 2
"${COMPOSE[@]}" exec -T api python -m tests.verify_without_redis || FAILED+=("verify_without_redis")
"${COMPOSE[@]}" start cache >/dev/null

if [[ ${#FAILED[@]} -gt 0 ]]; then
    printf '\n\033[31mУпали наборы: %s\033[0m\n' "${FAILED[*]}"
    exit 1
fi
printf '\n\033[32mВсе проверки прошли.\033[0m\n'
