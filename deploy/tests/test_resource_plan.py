"""План ресурсов (deploy/resource_plan.py): формулы, правила безопасности, запись в .env.

Запуск из корня репозитория: `python -m pytest deploy/tests -q`
"""

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "resource_plan.py"

spec = importlib.util.spec_from_file_location("resource_plan", SCRIPT)
rp = importlib.util.module_from_spec(spec)
sys.modules["resource_plan"] = rp
spec.loader.exec_module(rp)

SECRETS = (
    "APP_ENV=production\n"
    "DOMAIN=esocrm.com\n"
    "POSTGRES_PASSWORD=очень-секретный-пароль\n"
    "ENCRYPTION_KEY=0123456789abcdef0123456789abcdef\n"
    "IMAGE_TAG=sha-abc\n"
)

PROFILES = [(4, 7934), (6, 16384), (8, 32768), (16, 65536)]


# ------------------------------------------------------------------ формулы


def test_current_server_matches_documented_numbers():
    plan = rp.compute(4, 7934, 30)
    v = plan.values
    assert (plan.api_workers, plan.gateway_processes) == (2, 3)
    assert v["ASTRA_API_MEMORY"] == "1536M"  # как вписано сейчас
    assert v["ASTRA_GATEWAY_MEMORY"] == "2961M"  # под 30 аккаунтов
    assert v["ASTRA_DB_SHARED_BUFFERS"] == "1984MB"
    assert v["ASTRA_DB_MEMORY"] == "3968M"  # вдвое больше shared_buffers
    assert v["ASTRA_DB_MAX_CONNECTIONS"] == "150"


def test_upgrade_to_6_cores_16_gb_grows_what_it_should():
    plan = rp.compute(6, 16384, 30)
    v = plan.values
    assert (plan.api_workers, plan.gateway_processes) == (3, 5)
    assert v["ASTRA_DB_SHARED_BUFFERS"] == "4096MB"
    assert plan.mb("ASTRA_DB_MEMORY") == 8192
    assert plan.mb("ASTRA_GATEWAY_MEMORY") == 3294


@pytest.mark.parametrize(("cores", "ram"), PROFILES)
def test_plan_never_goes_below_current_values(cores, ram):
    plan = rp.compute(cores, ram, 30)
    for key, floor in (
        ("ASTRA_API_MEMORY", 1536),
        ("ASTRA_GATEWAY_MEMORY", 1536),
        ("ASTRA_DB_MEMORY", 2048),
        ("ASTRA_STORAGE_MEMORY", 1024),
        ("ASTRA_CACHE_MEMORY", 512),
    ):
        assert plan.mb(key) >= floor, key
    v = plan.values
    assert int(v["ASTRA_DB_SHARED_BUFFERS"].removesuffix("MB")) >= 512
    assert int(v["ASTRA_DB_EFFECTIVE_CACHE_SIZE"].removesuffix("MB")) >= 1536
    assert int(v["ASTRA_DB_WORK_MEM"].removesuffix("MB")) >= 16
    assert int(v["ASTRA_DB_MAX_CONNECTIONS"]) >= 100


def test_plan_is_monotonic_with_server_size():
    previous = None
    for cores, ram in PROFILES:
        plan = rp.compute(cores, ram, 30)
        sizes = (
            plan.mb("ASTRA_DB_MEMORY"),
            int(plan.values["ASTRA_DB_SHARED_BUFFERS"].removesuffix("MB")),
            plan.api_workers,
            plan.gateway_processes,
        )
        if previous:
            assert all(now >= before for now, before in zip(sizes, previous, strict=True))
        previous = sizes


def test_invariants_hold_across_many_servers():
    for cores in (1, 2, 3, 4, 6, 8, 12, 16, 32, 64):
        for ram in (4096, 6144, 7934, 12288, 16384, 32768, 65536, 131072):
            for accounts in (1, 10, 30, 100):
                plan = rp.compute(cores, ram, accounts)  # _check сам бросит при нелепице
                shared = int(plan.values["ASTRA_DB_SHARED_BUFFERS"].removesuffix("MB"))
                assert shared * 2 <= plan.mb("ASTRA_DB_MEMORY")
                assert 1 <= plan.gateway_processes <= 12 and 2 <= plan.api_workers <= 8


def test_small_server_and_many_accounts_produce_warning_not_failure():
    plan = rp.compute(2, 4096, 100)
    assert plan.warnings, "сервер на 4 ГБ под 100 аккаунтов должен получить предупреждение"
    assert plan.mb("ASTRA_GATEWAY_MEMORY") > 1536


@pytest.mark.parametrize("bad", [(0, 8192, 30), (4, 100, 30), (4, 8192, 0)])
def test_nonsense_server_size_is_rejected(bad):
    with pytest.raises(ValueError):
        rp.compute(*bad)


# --------------------------------------------------------------- файл .env


def run(*args, env_file: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file), *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(SECRETS)
    path.chmod(0o600)
    return path


def test_print_mode_changes_nothing(env_file):
    result = run("--cores", "4", "--ram-mb", "7934", env_file=env_file)
    assert result.returncode == rp.EXIT_OK and "ничего не записано" in result.stdout
    assert env_file.read_text() == SECRETS


def test_apply_adds_block_and_keeps_secrets_byte_for_byte(env_file):
    result = run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    assert result.returncode == rp.EXIT_CHANGED, result.stderr
    text = env_file.read_text()
    assert text.startswith(SECRETS.rstrip("\n")), "секреты и порядок строк не тронуты"
    assert "ASTRA_GATEWAY_MEMORY=2961M" in text and rp.END in text
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600, "права файла сохранены"


def test_apply_is_idempotent(env_file):
    run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    first = env_file.read_text()
    result = run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    assert result.returncode == rp.EXIT_OK and "уже актуален" in result.stdout
    assert env_file.read_text() == first, "повторное применение не должно менять файл"


def test_apply_updates_block_when_server_grows_and_keeps_one_block(env_file):
    run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    result = run("--apply", "--cores", "6", "--ram-mb", "16384", env_file=env_file)
    assert result.returncode == rp.EXIT_CHANGED
    text = env_file.read_text()
    assert text.count(rp.BEGIN_PREFIX) == 1 and text.count(rp.END_PREFIX) == 1
    assert "ASTRA_DB_SHARED_BUFFERS=4096MB" in text and "ASTRA_PLAN_CORES=6" in text
    assert "ASTRA_DB_SHARED_BUFFERS=1984MB" not in text
    assert text.startswith(SECRETS.rstrip("\n"))


def test_check_mode_exit_codes(env_file):
    assert run("--check", "--cores", "4", "--ram-mb", "7934", env_file=env_file).returncode == rp.EXIT_CHANGED
    run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    assert run("--check", "--cores", "4", "--ram-mb", "7934", env_file=env_file).returncode == rp.EXIT_OK
    assert run("--check", "--cores", "8", "--ram-mb", "32768", env_file=env_file).returncode == rp.EXIT_CHANGED


def test_restore_returns_previous_block(env_file, tmp_path):
    previous = tmp_path / "previous"
    run("--apply", "--cores", "4", "--ram-mb", "7934", "--save-previous", str(previous), env_file=env_file)
    assert previous.read_text() == "", "блока раньше не было — сохранена пустота"
    before_second = env_file.read_text()
    run("--apply", "--cores", "6", "--ram-mb", "16384", "--save-previous", str(previous), env_file=env_file)
    assert "ASTRA_PLAN_CORES=4" in previous.read_text()
    result = run("--restore", str(previous), env_file=env_file)
    assert result.returncode == rp.EXIT_CHANGED
    assert env_file.read_text() == before_second, "после отката файл такой же, как до второго плана"


def test_restore_with_empty_previous_removes_the_block(env_file, tmp_path):
    previous = tmp_path / "previous"
    run("--apply", "--cores", "4", "--ram-mb", "7934", "--save-previous", str(previous), env_file=env_file)
    run("--restore", str(previous), env_file=env_file)
    assert env_file.read_text().rstrip("\n") == SECRETS.rstrip("\n")


def test_kill_switch(env_file):
    env_file.write_text(SECRETS + "ASTRA_AUTOSIZE=off\n")
    result = run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    assert result.returncode == rp.EXIT_OK and "выключен" in result.stdout
    assert rp.BEGIN_PREFIX not in env_file.read_text()


def test_expected_accounts_can_be_set_in_env_outside_block(env_file):
    env_file.write_text(SECRETS + "ASTRA_EXPECTED_ACCOUNTS=50\n")
    run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    text = env_file.read_text()
    assert "ASTRA_PLAN_ACCOUNTS=50" in text
    assert "ASTRA_EXPECTED_ACCOUNTS=50" in text.split(rp.BEGIN_PREFIX)[0]


def test_file_without_trailing_newline_and_empty_file(tmp_path):
    for content in ("A=1", ""):
        path = tmp_path / f".env{len(content)}"
        path.write_text(content)
        assert run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=path).returncode == rp.EXIT_CHANGED
        text = path.read_text()
        assert text.count(rp.BEGIN_PREFIX) == 1 and (not content or text.startswith("A=1\n"))
        assert run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=path).returncode == rp.EXIT_OK


def test_missing_env_file_is_created_private(tmp_path):
    path = tmp_path / "new.env"
    assert run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=path).returncode == rp.EXIT_CHANGED
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_real_hardware_detection_works_on_linux():
    if not os.path.exists("/proc/meminfo"):
        pytest.skip("не Linux")
    cores, ram = rp.detect()
    assert cores >= 1 and ram >= 512


def test_deploy_script_edits_do_not_break_block(env_file):
    """deploy.sh дописывает IMAGE_TAG и т. п. в конец файла — блок это переживает."""
    run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    env_file.write_text(env_file.read_text() + "BACKEND_IMAGE=ghcr.io/x/y-backend\n")
    result = run("--apply", "--cores", "4", "--ram-mb", "7934", env_file=env_file)
    text = env_file.read_text()
    assert text.count(rp.BEGIN_PREFIX) == 1 and "BACKEND_IMAGE=ghcr.io/x/y-backend" in text
    assert result.returncode == rp.EXIT_OK
