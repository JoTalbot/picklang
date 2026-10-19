"""Распределённый прогон Temporal: воркер — отдельный процесс.

Пропускается только если temporalio не установлен. Ошибки запуска/сервера
не маскируются под SKIP: интеграционный тест должен завершиться PASS.
"""
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("temporalio")


def test_temporal_worker_in_separate_process():
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "temporal_distributed.py")],
        capture_output=True, text=True, timeout=240, cwd=root)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-1000:]
    assert any(line.startswith("PASS:") for line in out.splitlines()), out[-1000:]


def test_temporal_worker_restart_mid_workflow(tmp_path=None):
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "temporal_restart.py")],
        capture_output=True, text=True, timeout=240, cwd=root)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-1000:]
    assert any(line.startswith("PASS:") for line in out.splitlines()), out[-1000:]
