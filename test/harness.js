/* ══════════════════════════════════════════════════════════
   harness.js — «игрушечный браузер» для проверки игры без экрана.
   Он не заменяет настоящий браузер, но позволяет прогнать
   сотни прохождений и убедиться, что ни одна ветка не ломается.
   Запуск: node test/harness.js
   ══════════════════════════════════════════════════════════ */

const fs = require("fs");
const vm = require("vm");
const path = require("path");

const ROOT = path.join(__dirname, "..");

/* ── Минимальная имитация DOM ─────────────────────────── */
function makeEl(id) {
  const el = {
    id: id || "",
    textContent: "",
    _innerHTML: "",
    title: "",
    src: "",
    alt: "",
    style: {},
    children: [],
    disabled: false,
    offsetWidth: 1,
    _listeners: {},
    classList: {
      _set: new Set(),
      add(c) { this._set.add(c); },
      remove(c) { this._set.delete(c); },
      toggle(c, force) {
        if (force === undefined) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); }
        else if (force) this._set.add(c); else this._set.delete(c);
      },
      contains(c) { return this._set.has(c); }
    },
    addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); },
    click() { (this._listeners.click || []).forEach(fn => fn({ target: this })); },
    appendChild(child) { this.children.push(child); return child; },
    insertBefore(child) { this.children.push(child); return child; },
    querySelectorAll(sel) {
      const out = [];
      const walk = (node) => {
        node.children.forEach(c => {
          if (c._tag === "button") out.push(c);
          walk(c);
        });
      };
      walk(this);
      return out;
    },
    setAttribute(k, v) { this[k] = v; },
    getAttribute(k) { return this[k]; },
    parentNode: { insertBefore() {} }
  };
  /* Настоящий innerHTML при записи стирает всё внутри.
     Здесь повторяем это поведение, иначе «старые» кнопки
     остаются в списке и тест нажимает не те. */
  Object.defineProperty(el, "innerHTML", {
    get() { return this._innerHTML; },
    set(v) { this._innerHTML = v; this.children = []; }
  });

  return el;
}

function makeDocument() {
  const nodes = {};
  const doc = {
    _ready: [],
    getElementById(id) {
      if (!nodes[id]) nodes[id] = makeEl(id);
      return nodes[id];
    },
    createElement(tag) {
      const el = makeEl("");
      el._tag = tag;
      return el;
    },
    createTextNode(text) {
      return { _tag: "text", textContent: text, children: [] };
    },
    querySelectorAll(sel) {
      // панели навыков/сумки/дневника
      return [makePanel("skills"), makePanel("bag"), makePanel("log")];
    },
    addEventListener(ev, fn) { if (ev === "DOMContentLoaded") doc._ready.push(fn); }
  };

  function makePanel(name) {
    const el = makeEl("");
    el.setAttribute("data-panel", name);
    return el;
  }

  return doc;
}

/* ── Сборка «браузера» ────────────────────────────────── */
function boot() {
  const store = {};
  const doc = makeDocument();

  const ctx = {
    console: { log: () => {}, warn: () => {}, error: console.error },
    Math, Date, JSON, Object, Array, String, Number, Boolean, parseInt, parseFloat, isNaN,
    /* Все таймеры выполняем сразу: в тесте время условное.
       Покадровую анимацию отключаем флагом __NO_ANIM, иначе её
       бесконечный цикл кадров не даст тесту закончиться. */
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
    requestAnimationFrame: (fn) => { fn(); return 0; },
    matchMedia: () => ({ matches: false }),
    Image: function () { this.src = ""; },
    document: doc,
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; }
    },
    window: {}
  };
  ctx.window = ctx;
  ctx.window.scrollTo = () => {};
  ctx.__NO_ANIM = true;   // см. src/ui.js: отключает покадровую анимацию
  vm.createContext(ctx);

  /* В браузере все файлы живут в одной общей области видимости,
     поэтому здесь мы тоже склеиваем их в один сценарий. */
  const files = ["src/state.js", "src/story/chapter1.js", "src/story/chapter2.js",
                 "src/story/chapter3.js", "src/ui.js", "src/game.js"];
  const code = files.map((f) => fs.readFileSync(path.join(ROOT, f), "utf8")).join("\n;\n")
             + "\n;globalThis.__api = { Game: Game, STORY: STORY, UI: UI };";
  vm.runInContext(code, ctx, { filename: "app.bundle.js" });

  doc._ready.forEach((fn) => fn());
  return { ctx, doc, store };
}

/* ── Прохождение: случайный выбор вариантов ───────────── */
function playthrough(seed, backgroundId) {
  const { ctx, doc, store } = boot();

  // нажимаем «Новая игра», чтобы появился выбор героя
  doc.getElementById("btn-new").click();

  // выбираем героя
  const cards = doc.getElementById("bg-list").children;
  const idx = backgroundId === "tech" ? 0 : backgroundId === "hacker" ? 1 : 2;
  const card = cards[idx];
  if (!card) throw new Error("Нет карточки героя " + backgroundId);
  card.click();

  const visited = [];
  const STORY = ctx.__api.STORY;
  const Game = ctx.__api.Game;

  for (let step = 0; step < 400; step++) {
    const state = Game.state;
    if (!state) throw new Error("Состояние потеряно");

    const scene = STORY[state.scene];
    if (!scene) throw new Error("Сцена не найдена: " + state.scene);
    visited.push(state.scene);

    if (scene.ending) {
      return { ok: true, ending: scene.ending, scenes: visited.length, visited, state, store };
    }

    // собираем доступные кнопки с их вариантами
    const available = [];
    scene.choices.forEach((c) => {
      if (c.showIf && !c.showIf(state)) return;
      if (c.needItem && state.items.indexOf(c.needItem) === -1) return;
      available.push(c);
    });
    if (available.length === 0) {
      throw new Error("Тупик без вариантов: " + state.scene);
    }

    // Предпочитаем варианты, ведущие в ещё не пройденные сцены:
    // так тест обходит больше контента, а не кружит по коридору.
    const fresh = [];
    available.forEach((c, i) => {
      const target = c.next || c.success || c.fail;
      if (target && visited.indexOf(target) === -1) fresh.push(i);
    });
    const pool = fresh.length ? fresh : available.map((c, i) => i);

    seed = (seed * 1103515245 + 12345) % 2147483648;
    const pickIndex = pool[seed % pool.length];

    // кнопки на экране лежат в том же порядке, что и доступные варианты
    const wrappers = doc.getElementById("choices").children;
    const wrapper = wrappers[pickIndex];
    if (!wrapper) throw new Error("Нет кнопки №" + pickIndex + " на сцене " + state.scene);
    const button = wrapper.children.find((c) => c._tag === "button");
    if (!button) throw new Error("Кнопка не отрисована на сцене " + state.scene);
    button.click();
  }
  throw new Error("Слишком длинное прохождение (возможен цикл)");
}

/* ── Запуск серии проверок ────────────────────────────── */
const results = [];
for (const bg of ["tech", "hacker", "talker"]) {
  for (let seed = 1; seed <= 120; seed++) {
    try {
      results.push(playthrough(seed, bg));
    } catch (e) {
      console.error("ПРОВАЛ", bg, seed, e.message);
      process.exitCode = 1;
      break;
    }
  }
}

const endings = {};
results.forEach((r) => { endings[r.ending] = (endings[r.ending] || 0) + 1; });
const lengths = results.map((r) => r.scenes);
console.log("Прохождений:", results.length);
console.log("Финал «Двое»:", endings["Двое"] || 0);
console.log("Финал «Держать»:", endings["Держать"] || 0);
console.log("Финал «Пять всплесков»:", endings["Пять всплесков"] || 0);
console.log("Длина прохождения: от", Math.min(...lengths), "до", Math.max(...lengths), "сцен");
console.log(process.exitCode ? "ЕСТЬ ОШИБКИ" : "ВСЁ ЧИСТО");
