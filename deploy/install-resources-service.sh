#!/usr/bin/env bash
#
# Включить пересчёт плана ресурсов при загрузке сервера (юнит systemd).
#   sudo ./deploy/install-resources-service.sh            # включить
#   sudo ./deploy/install-resources-service.sh --remove   # выключить и убрать
#
# Юнит читает путь /opt/astra; если проект лежит в другом месте — поправьте
# WorkingDirectory и ExecStart в deploy/astra-resources.service до установки.

set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
UNIT=/etc/systemd/system/astra-resources.service

if [[ "${1:-}" == "--remove" ]]; then
    systemctl disable --now astra-resources.service 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload
    echo "юнит astra-resources убран"
    exit 0
fi

[[ "$(pwd)" == "/opt/astra" ]] || echo "ВНИМАНИЕ: проект не в /opt/astra — проверьте пути в юните" >&2
install -m 644 deploy/astra-resources.service "$UNIT"
systemctl daemon-reload
systemctl enable astra-resources.service
echo "юнит включён: при следующей загрузке сервера план ресурсов пересчитается сам"
echo "проверить сейчас, не перезагружая:  sudo systemctl start astra-resources.service && journalctl -u astra-resources -n 20"
