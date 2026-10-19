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

  /* ── Картинка сцены: покадровая анимация ────────────────
     Сцена может дать один кадр (строка) или несколько (массив).
     Несколько — значит место «живёт»: свет в реакторе пульсирует,
     ядро ВЕРЫ дышит, волна в атмосфере накатывает.
     Показ устроен как в кино: кадр A проявляет кадр B, потом
     B становится основным, и так по кругу.                       */

  let artTimer = null;
  let artToken = 0;   // чтобы старый цикл не «догнал» новую сцену

  function stopArt() {
    if (artTimer) { clearTimeout(artTimer); artTimer = null; }
    artToken++;
  }

  function renderArt(artPath, frameMs) {
    stopArt();

    const wrap = $("art-wrap");
    const frames = Array.isArray(artPath) ? artPath.slice() : (artPath ? [artPath] : []);

    if (frames.length === 0) {
      wrap.classList.add("hidden");
      return;
    }

    wrap.classList.remove("hidden");
    // перезапускаем анимацию появления сцены
    wrap.style.animation = "none";
    void wrap.offsetWidth;
    wrap.style.animation = "";

    const a = $("art");
    const b = $("art-b");

    a.onerror = function () { wrap.classList.add("hidden"); };
    a.src = frames[0];
    a.alt = "Иллюстрация сцены";
    b.src = "";
    b.style.opacity = "0";

    if (frames.length < 2) return;

    // заранее загружаем все кадры, чтобы не мигало при смене
    frames.forEach(function (src) { const im = new Image(); im.src = src; });

    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;        // человек просил меньше движения — показываем один кадр
    if (window.__NO_ANIM) return;  // автотест без экрана: кадры не крутим

    const token = artToken;
    const speed = frameMs || 2600;
    let i = 0;

    function step() {
      if (token !== artToken) return;          // сцена уже сменилась

      const next = frames[(i + 1) % frames.length];
      b.src = next;

      // даём браузеру кадр на отрисовку, потом плавно проявляем
      requestAnimationFrame(function () {
        if (token !== artToken) return;
        b.style.opacity = "1";
      });

      artTimer = setTimeout(function () {
        if (token !== artToken) return;
        a.src = next;               // основной слой догнал
        b.style.opacity = "0";      // верхний слой снова прозрачный
        i = (i + 1) % frames.length;
        artTimer = setTimeout(step, speed);
      }, 950);
    }

    artTimer = setTimeout(step, speed);
  }

  /* ── Плашка поверх арта ────────────────────────────────
     Здесь на картинке видно: где ты сейчас (состояние)
     и какие есть ходы. Это не «нарисованный» текст, а живой слой —
     он пересчитывается после каждого твоего решения. */
  function renderOverlay(scene, state, visibleChoices, onChoice) {
    const romans = ["", "Глава I", "Глава II", "Глава III"];
    $("ov-chapter").textContent = romans[state.chapter] || "Глава";

    /* Строка состояния: сцена + что важно про сейчас */
    const status = $("ov-status");
    const line = (typeof scene.status === "function") ? scene.status(state) : scene.status;
    status.innerHTML = line || defaultStatus(scene, state);

    /* Ходы: короткие «чипы», по ним тоже можно нажимать */
    const box = $("ov-choices");
    box.innerHTML = "";
    visibleChoices.forEach(function (choice, i) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "ov-chip";
      const num = document.createElement("span");
      num.className = "num";
      num.textContent = (i + 1);
      chip.appendChild(num);
      chip.appendChild(document.createTextNode(shortLabel(choice.label)));
      chip.addEventListener("click", function (e) {
        e.stopPropagation();
        onChoice(choice);
      });
      box.appendChild(chip);
    });
  }

  /* Состояние по умолчанию: если у сцены не написано своё */
  function defaultStatus(scene, state) {
    const items = state.items.length ? state.items.join(", ") : "пусто";
    const decisions = state.log ? state.log.length : 0;
    return "<b>" + (scene.title || "") + "</b> · сумка: " + items +
           " · решений: " + decisions;
  }

  /* Короткая подпись для чипа: длинные фразы не влезают на картинку */
  function shortLabel(text) {
    let t = String(text).replace(/[«»"]/g, "").trim();
    if (t.length > 34) t = t.slice(0, 32).trim() + "…";
    return t;
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
    renderOverlay: renderOverlay,
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
