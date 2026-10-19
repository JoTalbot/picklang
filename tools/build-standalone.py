#!/usr/bin/env python3
"""
build-standalone.py — собирает игру в ОДИН файл.

Зачем: обычная версия состоит из index.html + 8 файлов кода + 10 картинок.
Это правильно для разработки, но неудобно, если хочется отправить игру
себе в мессенджер и открыть на телефоне без сервера.

Что делает: берёт index.html, вставляет внутрь стили, скрипты и картинки
(картинки — в виде base64, то есть «текстом»), и сохраняет orbita9-standalone.html.

Запуск:  python3 tools/build-standalone.py
"""

import base64
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "index.html")
OUT = os.path.join(ROOT, "orbita9-standalone.html")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def data_uri(path):
    """Превращает картинку в строку, которую браузер понимает без файла."""
    with open(path, "rb") as f:
        raw = f.read()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


html = read(SRC)

# ── 1. Стили: <link> -> <style> ──────────────────────────────
css_path = os.path.join(ROOT, "src/styles.css")
html = html.replace(
    '<link rel="stylesheet" href="src/styles.css">',
    "<style>\n" + read(css_path) + "\n</style>",
)

# ── 2. Скрипты: <script src> -> <script> с кодом ─────────────
def inline_script(match):
    rel = match.group(1)
    code = read(os.path.join(ROOT, rel))
    return "<script>\n/* " + rel + " */\n" + code + "\n</script>"


html = re.sub(r'<script src="([^"]+)"></script>', inline_script, html)

# ── 3. Картинки: пути -> base64 внутри данных истории ────────
# Для одного файла берём облегчённый набор (880 px), если он есть:
# 11 МБ в мессенджере открываются плохо, 3 МБ — нормально.
LIGHT = os.path.join(ROOT, "assets/art/web-light")
art_dir = LIGHT if os.path.isdir(LIGHT) else os.path.join(ROOT, "assets/art/web")
uris = {}
for name in os.listdir(art_dir):
    if name.endswith(".jpg"):
        uris["assets/art/web/" + name] = data_uri(os.path.join(art_dir, name))

for path, uri in uris.items():
    html = html.replace('"' + path + '"', '"' + uri + '"')

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

size_mb = os.path.getsize(OUT) / 1024 / 1024
print("Готово:", OUT)
print("Размер: %.1f МБ" % size_mb)
print("Картинок внутри:", len(uris))
