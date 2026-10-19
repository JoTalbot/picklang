/* ══════════════════════════════════════════════════════════
   game.js — «мозг» игры.
   Здесь: правила проверок, переходы между сценами,
   запись в дневник, сохранение после каждого шага.
   ══════════════════════════════════════════════════════════ */

const Game = (function () {

  let state = null;      // текущее состояние (одна большая коробка с данными)

  /* ── Правило проверки навыка ────────────────────────────
     50% — базовый шанс.
     +18% за каждую единицу навыка сверх сложности.
     +4% за каждую единицу удачи.
     Всегда остаётся шанс провала и шанс успеха: 5–95%.  */
  function chance(skillValue, dc, luck) {
    let p = 0.5 + 0.18 * (skillValue - dc) + 0.04 * (luck || 0);
    return Math.max(0.05, Math.min(0.95, p));
  }

  /* ── Первый запуск ────────────────────────────────────── */
  function init() {
    UI.$("btn-new").addEventListener("click", newGame);
    UI.$("btn-continue").addEventListener("click", continueGame);
    UI.$("btn-restart").addEventListener("click", function () {
      Save.clear();
      newGame();
    });

    UI.$("sheet-close").addEventListener("click", UI.closeSheet);
    UI.$("sheet").addEventListener("click", function (e) {
      if (e.target.id === "sheet") UI.closeSheet();
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-panel]"), function (btn) {
      btn.addEventListener("click", function () {
        if (!state) return;
        const panel = btn.getAttribute("data-panel");
        if (panel === "skills") UI.openSheet("Навыки", UI.skillsHtml(state));
        if (panel === "bag") UI.openSheet("Сумка", UI.bagHtml(state));
        if (panel === "log") UI.openSheet("Дневник", UI.logHtml(state));
      });
    });

    const saved = Save.read();
    UI.$("btn-continue").classList.toggle("hidden", !saved);
    UI.showScreen("start");
  }

  /* ── Новая игра ───────────────────────────────────────── */
  function newGame() {
    const list = UI.$("bg-list");
    list.innerHTML = "";

    BACKGROUNDS.forEach(function (bg) {
      const card = document.createElement("div");
      card.className = "bg-card";

      let skillsHtml = "";
      Object.keys(bg.skills).forEach(function (key) {
        skillsHtml += '<span class="chip">' + SKILLS[key].icon + " " + SKILLS[key].name + " " + bg.skills[key] + "</span>";
      });

      card.innerHTML =
        '<div class="bg-name">' + bg.name + "</div>" +
        '<div class="bg-desc">' + bg.desc + "</div>" +
        '<div class="bg-chips chips">' + skillsHtml + '<span class="chip">🎒 ' + bg.item + "</span></div>";

      card.addEventListener("click", function () { startWith(bg.id); });
      list.appendChild(card);
    });

    UI.showScreen("create");
  }

  function startWith(backgroundId) {
    state = createState(backgroundId);
    Save.clear();
    UI.showScreen("game");
    goto("wake");
  }

  /* ── Продолжить ───────────────────────────────────────── */
  function continueGame() {
    const saved = Save.read();
    if (!saved) { newGame(); return; }

    state = saved;
    UI.showScreen("game");

    const scene = STORY[state.scene];
    if (scene && scene.ending) {
      showEnding(scene);
    } else {
      goto(state.scene);
    }
  }

  /* ── Переход в сцену ──────────────────────────────────── */
  function goto(sceneId) {
    const scene = STORY[sceneId];

    /* Если сцена не найдена — это наша ошибка в данных, а не ошибка игрока.
       Показываем понятное сообщение, чтобы не пугать белым экраном. */
    if (!scene) {
      UI.renderText({
        title: "Тупик",
        text: ["Здесь заканчиваются данные: сцены «" + sceneId + "» не существует.",
               "Это не твоя вина — это недописанная ветка. Вернись назад или начни заново."]
      }, state);
      UI.renderArt(null);
      UI.renderChoices({
        choices: [
          { label: "← Вернуться в коридор", next: "corridor" },
          { label: "Начать заново", next: "__restart" }
        ]
      }, state, function (c) {
        if (c.next === "__restart") { Save.clear(); newGame(); } else { goto(c.next); }
      });
      return;
    }

    state.scene = sceneId;
    state.chapter = Math.max(state.chapter || 1, scene.chapter || 1);

    if (typeof scene.onEnter === "function") {
      scene.onEnter(state);
    }

    Save.write(state);

    if (scene.ending) {
      showEnding(scene);
      return;
    }

    UI.hideCheck();
    UI.renderTopbar(state);
    UI.renderArt(scene.art);
    UI.renderText(scene, state);
    UI.renderChoices(scene, state, handleChoice);

    /* Плашка на картинке: состояние + доступные ходы.
       Показываем только те варианты, что реально доступны игроку. */
    if (scene.art) {
      const visible = scene.choices.filter(function (c) {
        if (c.showIf && !c.showIf(state)) return false;
        if (c.needItem && !hasItem(state, c.needItem)) return false;
        return true;
      });
      UI.renderOverlay(scene, state, visible, handleChoice);
    }
  }

  /* ── Обработка выбора игрока ──────────────────────────── */
  function handleChoice(choice) {
    // 1. Запоминаем, что игрок сделал
    if (choice.label) {
      state.log.push(choice.label);
    }

    // 2. Применяем изменения, которые действуют всегда
    applyEffects(choice.set);
    if (choice.give) addItem(state, choice.give);
    if (choice.take) removeItem(state, choice.take);

    // 3. Если это проверка навыка — бросаем «кубик»
    if (choice.check) {
      const sk = SKILLS[choice.check.skill];
      const value = state.skills[choice.check.skill] || 0;
      const p = chance(value, choice.check.dc, state.skills.luck);
      const ok = Math.random() < p;

      if (ok) {
        applyEffects(choice.setSuccess);
        UI.showCheck("✓ " + sk.name + " " + value + " против сложности " + choice.check.dc + ". Получилось." +
                     (choice.successNote ? " " + choice.successNote + "." : ""), true);
      } else {
        applyEffects(choice.setFail);
        UI.showCheck("✗ " + sk.name + " " + value + " против сложности " + choice.check.dc + ". Не хватило." +
                     (choice.failNote ? " " + choice.failNote + "." : ""), false);
      }

      Save.write(state);

      const target = ok ? (choice.success || choice.next) : (choice.fail || choice.next);
      setTimeout(function () {
        if (target === "__restart") { Save.clear(); newGame(); return; }
        goto(target);
      }, 1700);
      return;
    }

    // 4. Обычный переход
    Save.write(state);
    if (choice.next === "__restart") { Save.clear(); newGame(); return; }
    goto(choice.next);
  }

  function applyEffects(obj) {
    if (!obj) return;
    Object.keys(obj).forEach(function (key) {
      state.flags[key] = obj[key];
    });
  }

  /* ── Финальный экран ──────────────────────────────────── */
  function showEnding(scene) {
    state.ending = scene.ending || scene.title;
    Save.write(state);

    UI.$("end-title").textContent = scene.ending || "Финал";
    UI.$("end-text").innerHTML = "";

    const paragraphs = (typeof scene.text === "function") ? scene.text(state) : scene.text;
    paragraphs.forEach(function (line) {
      const p = document.createElement("p");
      p.textContent = line;
      UI.$("end-text").appendChild(p);
    });

    // Если у финала есть картинка — показываем её сверху
    let artImg = UI.$("end-art");
    if (!artImg) {
      artImg = document.createElement("img");
      artImg.id = "end-art";
      artImg.className = "art";
      artImg.style.borderRadius = "14px";
      artImg.style.marginBottom = "20px";
      artImg.style.border = "1px solid var(--line)";
      UI.$("end-text").parentNode.insertBefore(artImg, UI.$("end-title").nextSibling);
    }
    artImg.style.display = scene.art ? "block" : "none";
    if (scene.art) artImg.src = scene.art;

    const list = UI.$("end-log");
    list.innerHTML = "";
    state.log.forEach(function (entry) {
      const li = document.createElement("li");
      li.textContent = entry;
      list.appendChild(li);
    });

    UI.showScreen("end");
    window.scrollTo(0, 0);
  }

  /* ── Публичный интерфейс ──────────────────────────────── */
  return {
    init: init,
    chance: chance,
    newGame: newGame,
    goto: goto,
    get state() { return state; }
  };
})();

document.addEventListener("DOMContentLoaded", Game.init);
