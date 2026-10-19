#!/usr/bin/env python3
"""Отдельный процесс-агент: получает контекст по handoff-digest и печатает снимок.

Использование: context_receiver.py <inbox> <digest> <workdir>
Доказывает, что передача работает между процессами, а не только внутри одного.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from durable_weave.context_transport import ContextTransport  # noqa: E402
from durable_weave.slots import SlotStore  # noqa: E402

inbox, digest, workdir = sys.argv[1], sys.argv[2], sys.argv[3]
snapshot = ContextTransport(inbox).receive(digest, SlotStore(workdir))
print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
