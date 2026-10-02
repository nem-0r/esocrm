"""Поток управления deploy.sh и apply-resources.sh на подставных docker / curl / sleep.

Настоящий docker здесь не нужен: проверяется логика самих скриптов — порядок шагов, бэкап
перед пересозданием базы, откат блока в .env и кода при сбое, блокировка двойного запуска,
выключатель. Как compose пересоздаёт контейнеры, проверяют отдельно (`check-resource-plan.py`
и репетиция на настоящем docker). Нужны bash и git; для блокировки — ещё flock (на сервере
и в CI они есть).

Запуск из корня репозитория: `python -m pytest deploy/tests -q`
"""

import fcntl
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="нужны bash и git",
)

BASE_ENV = (
    "APP_ENV=production\n"
    "DOMAIN=crm.test\n"
    "POSTGRES_USER=astra\n"
    "POSTGRES_DB=astra\n"
    "POSTGRES_PASSWORD=очень-секретный-пароль\n"
    "ENCRYPTION_KEY=0123456789abcdef0123456789abcdef\n"
)

# Общее для подставных docker и curl: «здоров ли сервер сейчас».
LIB_SH = r"""
healthy_now() {
    case "${FAKE_HEALTH:-ok}" in
        bad) return 1 ;;
        bad_until_rollback) [[ -e "$FAKE_DIR/rolled-back" ]] ;;
        *) return 0 ;;
    esac
}
"""

# Подставной docker: пишет вызов в calls.log и отвечает по сценарию из переменных FAKE_*.
# Второй и последующие `up` считаются откатом: после них «прошлая версия» здорова.
DOCKER_SHIM = r"""#!/usr/bin/env bash
source "$FAKE_DIR/lib.sh"
echo "docker $*" >> "$FAKE_DIR/calls.log"
if [[ "${1:-}" == compose ]]; then
    shift
    [[ "${1:-}" == -f ]] && shift 2
    sub="${1:-}"
    shift || true
    case "$sub" in
        config) exit "${FAKE_CONFIG_RC:-0}" ;;
        exec)   echo "${FAKE_DB_REV:-a1b2c3}"; exit 0 ;;
        ps)     echo "cid-${!#}"; exit 0 ;;
        run)
            case "$*" in
                *"alembic heads"*) echo "${FAKE_HEAD_REV:-a1b2c3} (head)"; exit 0 ;;
                *"alembic show"*)  exit "${FAKE_SHOW_RC:-0}" ;;
                *)                 exit "${FAKE_MIGRATE_RC:-0}" ;;
            esac ;;
        up)
            n="$(cat "$FAKE_DIR/up-count" 2>/dev/null || echo 0)"
            echo $((n + 1)) > "$FAKE_DIR/up-count"
            (( n + 1 >= 2 )) && : > "$FAKE_DIR/rolled-back"
            exit 0 ;;
    esac
    exit 0
fi
if [[ "${1:-}" == inspect ]]; then
    case "$*" in
        *RestartCount*) echo 0 ;;
        *cid-gateway*)  echo running ;;
        *) if healthy_now; then echo healthy; else echo unhealthy; fi ;;
    esac
fi
exit 0
"""

CURL_SHIM = r"""#!/usr/bin/env bash
source "$FAKE_DIR/lib.sh"
echo "curl $*" >> "$FAKE_DIR/calls.log"
healthy_now || exit 22
echo '{"status":"ok"}'
"""

SLEEP_SHIM = """#!/bin/sh
exec /bin/sleep 0.05
"""

# Подставной backup.sh: тот же договор, что у настоящего, — путь к дампу последней строкой.
BACKUP_SHIM = r"""#!/usr/bin/env bash
echo "backup $*" >> "$FAKE_DIR/calls.log"
if [[ "${FAKE_BACKUP_FAIL:-}" == 1 ]]; then
    echo "ОШИБКА: подставной сбой бэкапа" >&2
    exit 1
fi
dir="$(cd "$(dirname "$0")/.." && pwd)/backups"
mkdir -p "$dir"
echo "dump" > "$dir/db-test.sql.gz"
echo "[00:00:00] дамп готов"
printf '%s\n' "$dir/db-test.sql.gz"
"""

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(project_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=project_dir,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def project(tmp_path: Path) -> SimpleNamespace:
    """Каталог «сервера»: скрипты, .env, git-репозиторий с двумя коммитами и подставные команды."""
    proj = tmp_path / "proj"
    (proj / "deploy").mkdir(parents=True)
    for name in ("deploy.sh", "apply-resources.sh", "resource_plan.py"):
        shutil.copy(ROOT / "deploy" / name, proj / "deploy" / name)
    write_executable(proj / "deploy" / "backup.sh", BACKUP_SHIM)
    (proj / "docker-compose.prod.yml").write_text("services: {}\n")
    (proj / ".env").write_text(BASE_ENV, encoding="utf-8")
    (proj / ".gitignore").write_text(
        ".env\nbackups/\ndeploy/.resource-plan.previous\ndeploy/.deploy.lock\ndeploy/.releases\n"
        "__pycache__/\n"
    )

    fake = tmp_path / "fake"
    (fake / "bin").mkdir(parents=True)
    (fake / "lib.sh").write_text(LIB_SH)
    (fake / "calls.log").write_text("")
    write_executable(fake / "bin" / "docker", DOCKER_SHIM)
    write_executable(fake / "bin" / "curl", CURL_SHIM)
    write_executable(fake / "bin" / "sleep", SLEEP_SHIM)

    git(proj, "init", "-q", "-b", "main")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "прошлая версия")
    prev_sha = git(proj, "rev-parse", "HEAD")
    (proj / "marker.txt").write_text("новая версия\n")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "новая версия")
    return SimpleNamespace(dir=proj, fake=fake, prev_sha=prev_sha)


def run_script(project, script: str, *args: str, **extra_env: str):
    env = {
        **os.environ,
        **GIT_ENV,
        "PATH": f"{project.fake / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DIR": str(project.fake),
        "IMAGE_TAG": "sha-new",
        "BACKEND_IMAGE": "reg/backend",
        "WEB_IMAGE": "reg/web",
        "HEALTH_URL": "http://health.test",
        "MIN_FREE_GB": "0",
        "HEALTH_TIMEOUT": "2",
        "PREV_SHA": project.prev_sha,
        **extra_env,
    }
    return subprocess.run(
        ["bash", str(project.dir / "deploy" / script), *args],
        cwd=project.dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def calls(project) -> list[str]:
    return (project.fake / "calls.log").read_text(encoding="utf-8").splitlines()


def clear_calls(project) -> None:
    (project.fake / "calls.log").write_text("")
    for marker in ("up-count", "rolled-back"):
        (project.fake / marker).unlink(missing_ok=True)


def env_text(project) -> str:
    return (project.dir / ".env").read_text(encoding="utf-8")


BEGIN = "# >>> astra-resource-plan"
END = "# <<< astra-resource-plan"


def has_block(text: str) -> bool:
    return BEGIN in text


def block_of(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if line.startswith(BEGIN):
            inside = True
        elif line.startswith(END):
            inside = False
        elif inside and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def outside(text: str) -> list[str]:
    """Непустые строки .env вне блока плана — всё, что принадлежит владельцу сервера."""
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith(BEGIN):
            inside = True
        elif line.startswith(END):
            inside = False
        elif not inside and line.strip():
            lines.append(line)
    return lines


def backup_calls(project) -> list[str]:
    return [c for c in calls(project) if c.startswith("backup ")]


def up_calls(project) -> list[str]:
    return [c for c in calls(project) if " up -d" in c]


DUMP = "backups/db-test.sql.gz"

# ===================================================================== deploy.sh


def test_first_deploy_applies_plan_and_takes_backup(project):
    before = env_text(project)

    result = run_script(project, "deploy.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    after = env_text(project)
    assert has_block(after)
    # Всё, что было в .env, на месте — секреты не тронуты; добавились только имена образов.
    assert set(outside(before)) <= set(outside(after))
    assert "POSTGRES_PASSWORD=очень-секретный-пароль" in after
    assert "IMAGE_TAG=sha-new" in after
    assert "план ресурсов обновлён" in result.stdout
    # Миграций нет, но база пересоздастся — бэкап всё равно сделан (и убран после успеха).
    assert "база пересоздастся с новыми настройками — сначала бэкап" in result.stdout
    assert len(backup_calls(project)) == 1
    assert not (project.dir / DUMP).exists()
    assert any("up -d --no-build --remove-orphans" in c for c in calls(project))


def test_second_deploy_leaves_plan_alone_and_skips_backup(project):
    assert run_script(project, "deploy.sh").returncode == 0
    snapshot = env_text(project)
    clear_calls(project)

    result = run_script(project, "deploy.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "план ресурсов актуален" in result.stdout
    assert env_text(project) == snapshot
    assert backup_calls(project) == []


def test_failed_health_check_returns_env_code_and_containers(project):
    before = env_text(project)

    result = run_script(project, "deploy.sh", FAKE_HEALTH="bad_until_rollback")

    out = result.stdout
    assert result.returncode == 1
    assert "ВЫКАТКА НЕ УДАЛАСЬ" in out
    assert "план ресурсов возвращён к прежнему" in out
    assert "прошлая версия снова работает" in out
    after = env_text(project)
    assert not has_block(after)
    # .env вернулся к тому, каким был: ни блока, ни имён образов неудачной выкатки.
    assert outside(after) == outside(before)
    # Код на сервере — снова прошлый коммит.
    assert git(project.dir, "rev-parse", "HEAD") == project.prev_sha
    assert not (project.dir / "marker.txt").exists()
    # База, redis и файлы пересозданы с прежними значениями.
    assert any("up -d --no-deps --no-build db cache storage" in c for c in calls(project))
    # Бэкап остаётся — он был страховкой на момент перехода.
    assert "бэкап базы до выкатки сохранён" in out
    assert (project.dir / DUMP).exists()


def test_failed_migration_also_returns_plan(project):
    before = env_text(project)

    result = run_script(project, "deploy.sh", FAKE_MIGRATE_RC="1", FAKE_HEALTH="bad_until_rollback")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "миграция не прошла" in result.stdout
    assert not has_block(env_text(project))
    assert outside(env_text(project)) == outside(before)


def test_failed_upgrade_restores_the_previous_block_not_removes_it(project):
    # Сервер уже работает по плану; потом его «увеличили» и выкатка не удалась.
    assert run_script(project, "deploy.sh").returncode == 0
    old = re.sub(r"ASTRA_PLAN_CORES=\d+", "ASTRA_PLAN_CORES=999", env_text(project))
    (project.dir / ".env").write_text(old, encoding="utf-8")
    clear_calls(project)

    result = run_script(
        project, "deploy.sh", IMAGE_TAG="sha-newer", FAKE_HEALTH="bad_until_rollback"
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "план ресурсов возвращён к прежнему" in result.stdout
    after = env_text(project)
    assert block_of(after) == block_of(old)  # прежний блок, а не новый и не пустой
    assert "IMAGE_TAG=sha-new\n" in after  # образы вернулись к тем, что работали
    assert "sha-newer" not in after


def test_backup_is_not_doubled_when_migrations_already_took_one(project):
    result = run_script(project, "deploy.sh", FAKE_DB_REV="ffff00")  # база на другой ревизии

    assert result.returncode == 0, result.stdout + result.stderr
    assert "в новой версии миграции" in result.stdout
    assert len(backup_calls(project)) == 1
    assert "база пересоздастся" not in result.stdout


def test_return_to_previous_version_neither_migrates_nor_backs_up(project):
    # База новее кода (откат на прошлую версию): `alembic show` не знает её ревизию.
    result = run_script(project, "deploy.sh", FAKE_DB_REV="ffff00", FAKE_SHOW_RC="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "новее этой версии" in result.stdout
    assert "новый план ресурсов для базы применится при следующей обычной выкатке" in result.stdout
    assert backup_calls(project) == []  # базу в этой выкатке не пересоздают
    assert not any(" run --rm -T migrate" in c and "alembic" not in c for c in calls(project))
    assert any("up -d --no-build --no-deps --remove-orphans api gateway scheduler web" in c for c in calls(project))


def test_backup_failure_stops_before_any_container_is_touched(project):
    before = env_text(project)

    result = run_script(project, "deploy.sh", FAKE_BACKUP_FAIL="1")

    assert result.returncode == 1
    assert "рабочие контейнеры не трогали" in result.stdout
    assert not has_block(env_text(project))
    assert outside(env_text(project)) == outside(before)
    assert up_calls(project) == []
    assert git(project.dir, "rev-parse", "HEAD") == project.prev_sha


def test_autosize_off_switch_changes_nothing(project):
    (project.dir / ".env").write_text(BASE_ENV + "ASTRA_AUTOSIZE=off\n", encoding="utf-8")

    result = run_script(project, "deploy.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not has_block(env_text(project))
    assert backup_calls(project) == []


def test_crashing_plan_script_does_not_stop_the_deploy(project):
    (project.dir / "deploy" / "resource_plan.py").write_text("import sys\nsys.exit(3)\n")

    result = run_script(project, "deploy.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "план ресурсов не применился (код 3)" in result.stdout
    assert not has_block(env_text(project))
    assert backup_calls(project) == []


def test_compose_rejecting_the_new_plan_returns_the_old_one(project):
    result = run_script(project, "deploy.sh", FAKE_CONFIG_RC="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "не проходит проверку compose — возвращаю прежний" in result.stdout
    assert not has_block(env_text(project))
    assert backup_calls(project) == []  # базу пересоздавать не будут — бэкап ни к чему


@pytest.mark.skipif(shutil.which("flock") is None, reason="нужен flock (на сервере есть)")
def test_deploy_refuses_to_run_twice_at_once(project):
    before = env_text(project)
    lock_path = project.dir / "deploy" / ".deploy.lock"
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_script(project, "deploy.sh")

    assert result.returncode == 75
    assert "уже идёт" in result.stderr
    assert env_text(project) == before
    assert calls(project) == []
    assert git(project.dir, "rev-parse", "HEAD") != project.prev_sha  # код не откатывали


# ============================================================ apply-resources.sh

SMALL = {"RESOURCE_PLAN_ARGS": "--cores 4 --ram-mb 7936"}
BIG = {"RESOURCE_PLAN_ARGS": "--cores 6 --ram-mb 16384"}


def test_apply_writes_plan_restarts_services_and_removes_backup(project):
    result = run_script(project, "apply-resources.sh", "--yes", **SMALL)

    assert result.returncode == 0, result.stdout + result.stderr
    assert block_of(env_text(project))["ASTRA_DB_SHARED_BUFFERS"] == "1984MB"
    assert "POSTGRES_PASSWORD=очень-секретный-пароль" in env_text(project)
    assert len(backup_calls(project)) == 1
    assert any("up -d --no-build --no-deps db cache storage api gateway scheduler web" in c for c in calls(project))
    assert any(c.endswith("restart nginx") for c in calls(project))
    assert not (project.dir / DUMP).exists()  # страховка больше не нужна


def test_apply_after_server_upgrade_grows_the_numbers(project):
    assert run_script(project, "apply-resources.sh", "--yes", **SMALL).returncode == 0
    clear_calls(project)

    result = run_script(project, "apply-resources.sh", "--yes", **BIG)

    assert result.returncode == 0, result.stdout + result.stderr
    block = block_of(env_text(project))
    assert block["ASTRA_DB_SHARED_BUFFERS"] == "4096MB"
    assert block["ASTRA_DB_MEMORY"] == "8192M"
    assert block["ASTRA_PLAN_CORES"] == "6"
    assert block["ASTRA_PLAN_RAM_MB"] == "16384"
    assert len(up_calls(project)) == 1


def test_apply_when_nothing_changed_does_nothing(project):
    assert run_script(project, "apply-resources.sh", "--yes", **SMALL).returncode == 0
    snapshot = env_text(project)
    clear_calls(project)

    result = run_script(project, "apply-resources.sh", "--if-changed", **SMALL)

    assert result.returncode == 0
    assert "план уже актуален" in result.stdout
    assert env_text(project) == snapshot
    assert calls(project) == []


def test_apply_dry_run_writes_nothing(project):
    before = env_text(project)

    result = run_script(project, "apply-resources.sh", "--dry-run", **SMALL)

    assert result.returncode == 0, result.stdout + result.stderr
    assert env_text(project) == before
    assert calls(project) == []


def test_apply_unhealthy_services_return_the_previous_plan(project):
    assert run_script(project, "apply-resources.sh", "--yes", **SMALL).returncode == 0
    small_block = block_of(env_text(project))
    clear_calls(project)

    result = run_script(
        project, "apply-resources.sh", "--yes", FAKE_HEALTH="bad_until_rollback", **BIG
    )

    out = result.stdout
    assert result.returncode == 1
    assert "СЕРВИСЫ НЕ СТАЛИ ЗДОРОВЫМИ" in out
    assert "прежний план восстановлен, сервисы здоровы" in out
    assert block_of(env_text(project)) == small_block  # вернулись прежние числа
    assert "бэкап базы до применения сохранён" in out
    assert (project.dir / DUMP).exists()


def test_apply_backup_failure_changes_nothing(project):
    before = env_text(project)

    result = run_script(project, "apply-resources.sh", "--yes", FAKE_BACKUP_FAIL="1", **SMALL)

    assert result.returncode == 1
    assert "бэкап базы не получился" in result.stdout
    assert not has_block(env_text(project))
    assert outside(env_text(project)) == outside(before)
    assert up_calls(project) == []


def test_apply_compose_rejecting_the_plan_restores_the_old_one(project):
    result = run_script(project, "apply-resources.sh", "--yes", FAKE_CONFIG_RC="1", **SMALL)

    assert result.returncode == 1
    assert "не проходит проверку compose" in result.stdout
    assert not has_block(env_text(project))
    assert up_calls(project) == []
    assert backup_calls(project) == []  # до пересоздания дело не дошло — бэкап не делали


@pytest.mark.skipif(shutil.which("flock") is None, reason="нужен flock (на сервере есть)")
def test_apply_refuses_while_a_deploy_is_running(project):
    lock_path = project.dir / "deploy" / ".deploy.lock"
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_script(project, "apply-resources.sh", "--yes", **SMALL)

    assert result.returncode == 75
    assert not has_block(env_text(project))
    assert calls(project) == []
