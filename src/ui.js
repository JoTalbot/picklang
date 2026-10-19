/* ══════════════════════════════════════════════════════════
   ui.js — «руки» игры.
   Берёт данные сцены и рисует их на экране.
   Ничего не решает: только показывает. Решает game.js.
   ══════════════════════════════════════════════════════════ */

const UI = (function () {

  /* Короткие имена элементов, чтобы не писать document.getElementById сто раз. */
  const $ = function (id) { return document.getElementById(id); };

  /* ── Переключение экранов ─────────────────────────────── */
  function showScreen(name) {
    ["start", "create", "game", "end"].forEach(function (n) {
      $("screen-" + n).classList.toggle("hidden", n !== name);
    });
  }

  /* ── Верхняя панель ───────────────────────────────────── */
  function renderTopbar(state) {
    const romans = ["", "Глава I", "Глава II", "Глава III"];
    $("chapter-label").textContent = romans[state.chapter] || "Глава";

    const dots = "●".repeat(Math.max(0, state.suit)) + "○".repeat(Math.max(0, 4 - state.suit));
    const suit = $("suit");
    suit.textContent = dots;
    suit.classList.toggle("danger", state.suit <= 2);
    suit.title = "Целостность скафандра: " + state.suit + " из 4";
  }

  /* ── Картинка сцены ───────────────────────────────────── */
  function renderArt(artPath) {
    const wrap = $("art-wrap");
    const img = $("art");
    if (!artPath) {
      wrap.classList.add("hidden");
      return;
    }
    wrap.classList.remove("hidden");
    img.onerror = function () { wrap.classList.add("hidden"); };
    img.src = artPath;
    img.alt = "Иллюстрация сцены";
  }

  /* ── Текст сцены ──────────────────────────────────────── */
  function renderText(scene, state) {
    $("scene-title").textContent = scene.title || "";

    const paragraphs = (typeof scene.text === "function") ? scene.text(state) : scene.text;
    const box = $("scene-text");
    box.innerHTML = "";

    paragraphs.forEach(function (line) {
      const p = document.createElement("p");
      p.textContent = line;
      box.appendChild(p);
    });

    // перезапускаем анимации появления
    void box.offsetWidth;
    window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
  }

  /* ── Варианты ответа ──────────────────────────────────── */
  function renderChoices(scene, state, onChoice) {
    const box = $("choices");
    box.innerHTML = "";

    scene.choices.forEach(function (choice, index) {
      if (choice.showIf && !choice.showIf(state)) return;
      if (choice.needItem && !hasItem(state, choice.needItem)) return;

      const wrap = document.createElement("div");
      wrap.className = "choice";
      wrap.style.animationDelay = (index * 0.04) + "s";

      /* Подпись над кнопкой: проверка навыка / пометка / сложность */
      if (choice.check) {
        const meta = document.createElement("div");
        meta.className = "choice-meta";
        const sk = SKILLS[choice.check.skill];
        const mySkill = state.skills[choice.check.skill] || 0;
        const chance = Math.round(Game.chance(mySkill, choice.check.dc, state.skills.luck) * 100);
        meta.textContent = "[" + sk.icon + " " + sk.name + " " + choice.check.dc + " · шанс " + chance + "%]";
        wrap.appendChild(meta);
      } else if (choice.note) {
        const meta = document.createElement("div");
        meta.className = "choice-meta";
        meta.textContent = choice.note;
        wrap.appendChild(meta);
      }

      const btn = document.createElement("button");
      btn.className = "btn";
      btn.type = "button";
      btn.textContent = choice.label;

      btn.addEventListener("click", function () {
        if (btn.disabled) return;
        // защита от двойного нажатия: пока идёт анимация проверки,
        // кнопки не должны срабатывать дважды
        Array.prototype.forEach.call(box.querySelectorAll("button"), function (b) { b.disabled = true; });
        onChoice(choice);
      });

      wrap.appendChild(btn);
      box.appendChild(wrap);
    });
  }

  /* ── Строка результата проверки ───────────────────────── */
  function showCheck(text, ok) {
    const line = $("check-line");
    line.textContent = text;
    line.className = "check-line " + (ok ? "ok" : "bad");
    line.classList.remove("hidden");
  }

  function hideCheck() {
    $("check-line").classList.add("hidden");
  }

  /* ── Выдвижные панели ─────────────────────────────────── */
  function openSheet(title, html) {
    $("sheet-title").textContent = title;
    $("sheet-body").innerHTML = html;
    $("sheet").classList.remove("hidden");
  }

  function closeSheet() {
    $("sheet").classList.add("hidden");
  }

  /* ── Содержимое панелей ───────────────────────────────── */
  function skillsHtml(state) {
    let html = "";
    Object.keys(SKILLS).forEach(function (key) {
      const value = state.skills[key] || 0;
      const pips = "●".repeat(value) + "○".repeat(Math.max(0, 5 - value));
      html += '<div class="skill-row"><span>' + SKILLS[key].icon + " " + SKILLS[key].name +
              '</span><span class="skill-val">' + pips + "</span></div>";
    });
    html += '<p class="muted" style="margin-top:14px">' +
            "Навык даёт шанс пройти проверку. Чем выше навык над сложностью — тем больше шанс. " +
            "Удача добавляет немного везения ко всем проверкам.</p>";
    return html;
  }

  function bagHtml(state) {
    if (state.items.length === 0) {
      return '<p class="muted">Сумка пуста. Совсем.</p>';
    }
    let html = "<ul>";
    state.items.forEach(function (item) {
      html += "<li><b>" + item + "</b></li>";
    });
    return html + "</ul>";
  }

  function logHtml(state) {
    if (!state.log || state.log.length === 0) {
      return '<p class="muted">Пока пусто. Дневник заполняется, когда ты делаешь выбор.</p>';
    }
    let html = "<ul>";
    state.log.forEach(function (entry) {
      html += "<li>" + entry + "</li>";
    });
    return html + "</ul>";
  }

  return {
    $: $,
    showScreen: showScreen,
    renderTopbar: renderTopbar,
    renderArt: renderArt,
    renderText: renderText,
    renderChoices: renderChoices,
    showCheck: showCheck,
    hideCheck: hideCheck,
    openSheet: openSheet,
    closeSheet: closeSheet,
    skillsHtml: skillsHtml,
    bagHtml: bagHtml,
    logHtml: logHtml
  };
})();
