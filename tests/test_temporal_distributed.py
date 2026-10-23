"""Распределённый прогон Temporal: воркер — отдельный процесс.

Пропускается только если temporalio не установлен. Ошибки запуска/сервера
не маскируются под SKIP: интеграционный тест должен завершиться PASS.
"""
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.temporal

pytest.importorskip("temporalio")


def test_temporal_worker_in_separate_process():
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "temporal_distributed.py")],
        capture_output=True, text=True, timeout=240, cwd=root)
    out = proc.stdout + proc.stderr
    passed = any(line.startswith("PASS:") for line in out.splitlines())

    # The bundled Temporal test server can abort during native shutdown on
    # ARM64 after the workflow itself has passed. Retry only this teardown-only
    # signature; a missing PASS or ordinary nonzero exit must fail immediately.
    if proc.returncode < 0 and passed:
        retry = subprocess.run(
            [sys.executable, str(root / "tools" / "temporal_distributed.py")],
            capture_output=True, text=True, timeout=240, cwd=root)
        retry_out = retry.stdout + retry.stderr
        assert retry.returncode == 0, retry_out[-1500:]
        assert any(line.startswith("PASS:") for line in retry_out.splitlines()), retry_out[-1500:]
        return

    assert proc.returncode == 0, out[-1000:]
    assert passed, out[-1000:]


def test_temporal_worker_restart_mid_workflow():
    root = Path(__file__).parent.parent
    command = [sys.executable, str(root / "tools" / "temporal_restart.py")]
    proc = subprocess.run(
        command, capture_output=True, text=True, timeout=240, cwd=root)
    out = proc.stdout + proc.stderr
    passed = any(line.startswith("PASS:") for line in out.splitlines())

    # Temporal's bundled local server can occasionally abort during its own
    # shutdown after the workflow has already completed successfully. Retry
    # that teardown-only failure once; never retry a failed workflow assertion.
    if proc.returncode != 0 and passed and proc.returncode < 0:
        retry = subprocess.run(
            command, capture_output=True, text=True, timeout=240, cwd=root)
        retry_out = retry.stdout + retry.stderr
        assert retry.returncode == 0, retry_out[-1500:]
        assert any(line.startswith("PASS:") for line in retry_out.splitlines()), retry_out[-1500:]
        return

    assert proc.returncode == 0, out[-1500:]
    assert passed, out[-1500:]
