/* ══════════════════════════════════════════════════════════
   state.js — «память» игры
   Здесь описано: из чего состоит состояние, как его
   создать с нуля, как сохранить в браузер и прочитать обратно.
   ══════════════════════════════════════════════════════════ */

/* Имя, под которым сохранение лежит в памяти браузера.
   Если когда-нибудь поменяем формат — поменяем и версию (v1 -> v2),
   чтобы старые сохранения не ломали игру. */
const SAVE_KEY = "orbita9.save.v1";

/* Что мы называем навыками и как они выглядят на экране. */
const SKILLS = {
  repair:   { name: "Ремонт",  icon: "🔧" },
  hack:     { name: "Взлом",   icon: "💻" },
  charisma: { name: "Харизма", icon: "🗣" },
  luck:     { name: "Удача",   icon: "🍀" }
};

/* Кем ты была до пробуждения. Три варианта — три разных «ключа»
   к одним и тем же дверям. Плохого выбора тут нет. */
const BACKGROUNDS = [
  {
    id: "tech",
    name: "Техник",
    desc: "Ты чинила станцию руками. Знаешь, как пахнет горелая проводка.",
    skills: { repair: 3, hack: 2, charisma: 1, luck: 1 },
    item: "Мультитул"
  },
  {
    id: "hacker",
    name: "Связист",
    desc: "Ты говорила со станциями на их языке. ВЕРА тебя помнит.",
    skills: { repair: 1, hack: 3, charisma: 2, luck: 1 },
    item: "Взломщик"
  },
  {
    id: "talker",
    name: "Психолог",
    desc: "Ты умела слушать людей. Экипаж ходил к тебе, когда молчали машины.",
    skills: { repair: 1, hack: 1, charisma: 3, luck: 2 },
    item: "Удостоверение"
  }
];

/* ── Создаём состояние с нуля ─────────────────────────────── */
function createState(backgroundId) {
  const bg = BACKGROUNDS.find(function (b) { return b.id === backgroundId; }) || BACKGROUNDS[0];

  return {
    version: 1,
    scene: "wake",          // на какой сцене мы сейчас
    chapter: 1,             // глава (для верхней панели)
    background: bg.id,
    skills: Object.assign({}, bg.skills),
    items: [bg.item],       // сумка
    flags: {},              // все «запомненные решения» — здесь
    suit: 4,                // целостность скафандра (4 точки)
    log: [],                // дневник: что ты выбирала
    ending: null,           // каким финалом закончилось
    startedAt: Date.now()
  };
}

/* ── Сохранение и загрузка ─────────────────────────────────
   Браузер умеет хранить ТОЛЬКО текст. Поэтому состояние
   сначала превращаем в текст (JSON.stringify), а при чтении —
   собираем обратно (JSON.parse). Как разобрать мебель: сначала
   расписать по деталям, потом собрать. */
const Save = {
  write: function (state) {
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify(state));
    } catch (e) {
      console.warn("Не удалось сохранить игру:", e);
    }
  },

  read: function () {
    try {
      const raw = localStorage.getItem(SAVE_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      // страховка: если сохранение старое/битое — начинаем заново
      if (!data || typeof data !== "object" || !data.scene) return null;
      if (!data.skills || !Array.isArray(data.items)) return null;
      return data;
    } catch (e) {
      console.warn("Сохранение повреждено:", e);
      return null;
    }
  },

  clear: function () {
    try { localStorage.removeItem(SAVE_KEY); } catch (e) { /* ничего */ }
  }
};

/* ── Мелкие помощники для сцен ───────────────────────────── */

/* Есть ли у героя предмет. */
function hasItem(state, name) {
  return state.items.indexOf(name) !== -1;
}

/* Положить предмет в сумку (без дублей). */
function addItem(state, name) {
  if (!hasItem(state, name)) state.items.push(name);
}

/* Убрать предмет из сумки. */
function removeItem(state, name) {
  const i = state.items.indexOf(name);
  if (i !== -1) state.items.splice(i, 1);
}
