"""Распределённый прогон Temporal: воркер — отдельный процесс.

Пропускается без temporalio или если тестовый сервер недоступен в окружении
(SKIP честно отличается от PASS: скрипт печатает исход, тест смотрит на него).
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
    if "SKIP" in out:
        pytest.skip(out.strip().splitlines()[-1] if out.strip() else "недоступно")
    assert "PASS" in out, out[-500:]


def test_temporal_worker_restart_mid_workflow(tmp_path=None):
    root = Path(__file__).parent.parent
    proc = subprocess.run(
        [sys.executable, str(root / "tools" / "temporal_restart.py")],
        capture_output=True, text=True, timeout=240, cwd=root)
    out = proc.stdout + proc.stderr
    if "SKIP" in out:
        pytest.skip(out.strip().splitlines()[-1] if out.strip() else "недоступно")
    assert "PASS" in out, out[-500:]
