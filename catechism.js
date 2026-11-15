/* Catechism class lessons + anchored class notes for the Daily Orthodox Brief.
   Lesson text: data/catechism/lessons.json (extracted from the parish's Adult
   Catechism handout by tools/extract_catechism.py). Notes stay on this device
   (localStorage); "Send to Doxy" opens a prefilled GitHub issue that she submits
   herself -- no token or secret is involved. */
(function () {
  "use strict";

  var NOTES_KEY = "catechismNotes";            // [{id, lesson, classDate, para, quote, section, text, updated}]
  var LAST_KEY = "catechismLastLesson";        // {lesson, date}
  var DATE_KEY = "catechismLesson:";           // + YYYY-MM-DD -> "03"
  var DOXY_ISSUE_BASE = "https://github.com/aimee-blanchard/daily-orthodox-brief/issues/new";
  var DOXY_MAX_URL = 7500;
  var CLASS_RE = /catechism|catechumen|inquirer/i;
  var NOT_CLASS_RE = /^\s*no\b|cancel|postpon/i;

  var lessonsPromise = null;
  var lessons = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function parseISO(s) { var p = s.split("-"); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function longDate(s) {
    return parseISO(s).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  }
  function shortDate(s) {
    return parseISO(s).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }
  function daysBetween(a, b) { return Math.round((parseISO(b) - parseISO(a)) / 86400000); }
  function eventTime(sql) {
    var m = /(\d{2}):(\d{2})/.exec((sql || "").slice(11));
    if (!m) return "";
    var h = +m[1], mm = m[2];
    if (h === 0 && mm === "00") return "";
    return (h % 12 || 12) + ":" + mm + (h < 12 ? " AM" : " PM");
  }

  function load() {
    if (!lessonsPromise) {
      lessonsPromise = fetch("data/catechism/lessons.json", { cache: "no-cache" })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (d) { lessons = d.lessons; return lessons; });
    }
    return lessonsPromise;
  }
  function lessonById(id) { return lessons.filter(function (l) { return l.id === id; })[0] || lessons[0]; }

  /* ---------- class detection ---------- */
  function classEvent(events) {
    return (events || []).filter(function (ev) {
      return CLASS_RE.test(ev.title || "") && !NOT_CLASS_RE.test(ev.title || "");
    })[0] || null;
  }

  /* ---------- storage ---------- */
  function loadNotes() { try { return JSON.parse(localStorage.getItem(NOTES_KEY) || "[]"); } catch (e) { return []; } }
  function saveNotes(list) {
    if (list.length) localStorage.setItem(NOTES_KEY, JSON.stringify(list)); else localStorage.removeItem(NOTES_KEY);
  }
  function lastLesson() { try { return JSON.parse(localStorage.getItem(LAST_KEY) || "null"); } catch (e) { return null; } }
  function rememberLesson(dateStr, id) {
    localStorage.setItem(DATE_KEY + dateStr, id);
    var last = lastLesson();
    if (!last || last.date <= dateStr) localStorage.setItem(LAST_KEY, JSON.stringify({ lesson: id, date: dateStr }));
  }
  // Which lesson to show for a date, and whether it is a suggestion.
  function lessonFor(dateStr, isClassDay) {
    var saved = localStorage.getItem(DATE_KEY + dateStr);
    if (saved) return { id: saved, suggested: false };
    var last = lastLesson();
    if (!last) return { id: "01", suggested: false };
    if (isClassDay && last.date < dateStr) {
      var next = Math.min(+last.lesson + 1, lessons.length);
      return { id: String(next).padStart(2, "0"), suggested: true, after: last.lesson };
    }
    return { id: last.lesson, suggested: false };
  }

  /* ---------- lesson rendering ---------- */
  function inline(text, lessonId) {
    return esc(text).replace(/\[\^(\d+)\]/g, function (_, n) {
      return '<sup class="cat-fnref"><a href="#cat-fn-' + lessonId + "-" + n + '">' + n + "</a></sup>";
    });
  }
  function sectionOf(lesson, para) {
    for (var i = para; i >= 0; i--) if (lesson.blocks[i].type === "heading") return lesson.blocks[i].text;
    return "";
  }
  function quoteOf(text, words) {
    var w = text.replace(/\[\^\d+\]/g, "").trim().split(/\s+/);
    return w.slice(0, words).join(" ") + (w.length > words ? "…" : "");
  }
  function renderLessonBody(lesson) {
    var rows = lesson.blocks.map(function (b, i) {
      var cls = "cat-para cat-" + b.type + (b.level ? " cat-l" + b.level : "");
      var tag = b.type === "heading" ? "h4" : "div";
      return "<" + tag + ' class="' + cls + '" data-para="' + i + '"><span class="cat-text">' + inline(b.text, lesson.id) + "</span></" + tag + ">";
    }).join("");
    var fns = lesson.footnotes && lesson.footnotes.length
      ? '<ol class="cat-footnotes">' + lesson.footnotes.map(function (f) {
          return '<li id="cat-fn-' + lesson.id + "-" + f.n + '" value="' + f.n + '">' + esc(f.text) + "</li>";
        }).join("") + "</ol>"
      : "";
    return (
      '<h3 class="cat-lesson-title">Lesson ' + esc(lesson.id) + " &middot; " + esc(lesson.title) + "</h3>" +
      (lesson.synopsis ? '<p class="cat-synopsis"><span>Synopsis</span> ' + inline(lesson.synopsis, lesson.id) + "</p>" : "") +
      '<div class="cat-rows">' + rows + "</div>" + fns
    );
  }

  /* ---------- Doxy issue link ---------- */
  function doxyUrl(lessonId, classDate, notes) {
    var lesson = lessonById(lessonId);
    var title = "Catechism notes – Lesson " + lessonId + " – " + classDate;
    var sorted = notes.slice().sort(function (a, b) {
      return a.classDate === b.classDate ? a.para - b.para : (a.classDate < b.classDate ? -1 : 1);
    });
    var multiDate = sorted.some(function (n) { return n.classDate !== classDate; });
    function clip(t, n) { return n == null || t.length <= n ? t : t.slice(0, n) + "…"; }
    function build(limit) {
      var out = "**Lesson " + lessonId + " – " + lesson.title + "**\n" +
        "**Class:** " + longDate(classDate) + " (" + classDate + ")\n";
      var lastSection = null, lastDate = null;
      sorted.forEach(function (n, i) {
        if (multiDate && n.classDate !== lastDate) {
          out += "\n## Class of " + longDate(n.classDate) + "\n";
          lastDate = n.classDate; lastSection = null;
        }
        var sec = n.section || sectionOf(lesson, n.para);
        if (sec && sec !== lastSection) { out += "\n### " + sec + "\n"; lastSection = sec; }
        out += "\n**" + (i + 1) + ".** At “" + n.quote + "” (paragraph " + (n.para + 1) + ")\n\n" +
          clip(n.text, limit).split("\n").map(function (l) { return "> " + l; }).join("\n") + "\n";
      });
      if (!sorted.length) out += "\n(No notes yet.)\n";
      if (limit != null) out += "\n> ⚠️ Some notes were too long for a link and were cut off (marked …). Paste the rest below.\n";
      out += "\n_Sent from the Daily Orthodox Brief catechism notes._\n";
      return DOXY_ISSUE_BASE + "?labels=doxy-catechism&title=" + encodeURIComponent(title) + "&body=" + encodeURIComponent(out);
    }
    var url = build(null);
    if (url.length <= DOXY_MAX_URL) return url;
    var lo = 20, hi = 5000;
    while (lo < hi) {
      var mid = Math.ceil((lo + hi) / 2);
      if (build(mid).length <= DOXY_MAX_URL) lo = mid; else hi = mid - 1;
    }
    return build(lo);
  }
  // Notes to send for a lesson: this class date's if any, else all of the lesson's.
  function doxyScope(lessonId, classDate) {
    var all = loadNotes().filter(function (n) { return n.lesson === lessonId; });
    var today = all.filter(function (n) { return n.classDate === classDate; });
    if (today.length || !all.length) return { notes: today, date: classDate };
    var latest = all.reduce(function (m, n) { return n.classDate > m ? n.classDate : m; }, "");
    return { notes: all, date: latest };
  }

  /* ---------- the card ---------- */
  function mount(slot, opts) {
    // opts: {dateStr, events, collapsed}
    if (!slot) return;
    var dateStr = opts.dateStr;
    var ev = classEvent(opts.events);
    var state = { lesson: null, suggestion: null, view: "lesson", open: !!ev };

    function recentClassNotes() {
      // Notes from a class in the past week (for resurfacing later in the week).
      var notes = loadNotes().filter(function (n) {
        var d = daysBetween(n.classDate, dateStr);
        return d > 0 && d <= 6;
      });
      if (!notes.length) return null;
      var latest = notes.reduce(function (m, n) { return n.classDate > m ? n.classDate : m; }, "");
      var those = notes.filter(function (n) { return n.classDate === latest; });
      return { date: latest, count: those.length, lesson: those[0].lesson };
    }

    function inner() { return slot.querySelector(".cat-inner"); }
    function section() { return slot.querySelector('.cs[data-cs="catechism"]'); }

    function notesView() {
      var all = loadNotes();
      if (!all.length) return '<p class="cat-empty">No class notes yet. In a lesson, press and hold any paragraph (or tap &ldquo;&#xFF0B; Note&rdquo;) to add one.</p>';
      var groups = {};
      all.forEach(function (n) { (groups[n.classDate + "|" + n.lesson] = groups[n.classDate + "|" + n.lesson] || []).push(n); });
      return Object.keys(groups).sort().reverse().map(function (k) {
        var p = k.split("|"), date = p[0], lid = p[1], lesson = lessonById(lid);
        var items = groups[k].sort(function (a, b) { return a.para - b.para; }).map(function (n) {
          return '<li class="cat-note-item">' +
            '<button type="button" class="cat-goto" data-lesson="' + esc(lid) + '" data-para="' + n.para + '">&ldquo;' + esc(n.quote) + "&rdquo;</button>" +
            '<button type="button" class="anchored-note cat-note" data-note-id="' + esc(n.id) + '">' + esc(n.text) + "</button>" +
            "</li>";
        }).join("");
        return '<div class="cat-note-group">' +
          '<div class="cat-note-group-head"><div><strong>' + esc(longDate(date)) + "</strong><span>Lesson " + esc(lid) + " &middot; " + esc(lesson.title) + "</span></div>" +
          '<button type="button" class="cat-doxy cat-doxy-small" data-lesson="' + esc(lid) + '" data-date="' + esc(date) + '">Send to Doxy &#8599;</button></div>' +
          '<ul class="cat-note-list">' + items + "</ul></div>";
      }).join("");
    }

    function renderOpen() {
      var lesson = lessonById(state.lesson);
      var notesCount = loadNotes().length;
      var options = lessons.map(function (l) {
        var label = "Lesson " + l.id + " – " + l.title + (state.suggestion && state.suggestion.id === l.id && state.suggestion.suggested ? "  (next)" : "");
        return '<option value="' + esc(l.id) + '"' + (l.id === lesson.id ? " selected" : "") + ">" + esc(label) + "</option>";
      }).join("");
      var sub = ev
        ? (dateStr === opts.today ? "Tonight" : esc(shortDate(dateStr))) + (eventTime(ev.start) ? " &middot; " + esc(eventTime(ev.start)) : "") + " &middot; " + esc(ev.title)
        : "Look back at a lesson or your class notes anytime";
      var hint = state.suggestion && state.suggestion.suggested && state.suggestion.after
        ? '<p class="cat-suggest">Suggested: the lesson after Lesson ' + esc(state.suggestion.after) + " from your last class. Change it if class covers something else.</p>"
        : "";
      inner().innerHTML =
        '<div class="cat-card">' +
        '<p class="cat-sub">' + sub + "</p>" +
        '<div class="cat-tabs" role="tablist">' +
        '<button type="button" role="tab" class="cat-tab' + (state.view === "lesson" ? " active" : "") + '" data-view="lesson">Lesson</button>' +
        '<button type="button" role="tab" class="cat-tab' + (state.view === "notes" ? " active" : "") + '" data-view="notes">Notes from class' + (notesCount ? " (" + notesCount + ")" : "") + "</button>" +
        "</div>" +
        (state.view === "lesson"
          ? '<div class="cat-body" data-lesson="' + esc(lesson.id) + '">' +
            '<div class="cat-sticky"><label class="cat-select-wrap"><span class="sr-only">Lesson</span>' +
            '<select class="cat-select" aria-label="Lesson">' + options + "</select></label>" +
            '<button type="button" class="add-note-btn cat-add-note" aria-label="Add a note at the top visible paragraph">&#xFF0B; Note</button></div>' +
            hint +
            renderLessonBody(lesson) +
            "</div>" +
            '<p class="notes-hint">Tip: press and hold any paragraph (or tap &ldquo;&#xFF0B; Note&rdquo;) to add a note there. Notes stay on this device.</p>' +
            '<div class="cat-lesson-actions"><span class="cat-lesson-count"></span>' +
            '<button type="button" class="cat-doxy" data-lesson="' + esc(lesson.id) + '">Send to Doxy &#8599;</button></div>'
          : '<div class="cat-notes-view">' + notesView() + "</div>") +
        "</div>";
      wireOpen();
      renderMarkers();
    }

    function renderMarkers() {
      var body = slot.querySelector(".cat-body");
      if (!body) return;
      var lid = body.getAttribute("data-lesson");
      body.querySelectorAll(".cat-note").forEach(function (el) { el.remove(); });
      var notes = loadNotes().filter(function (n) { return n.lesson === lid; })
        .sort(function (a, b) { return a.classDate < b.classDate ? -1 : a.classDate > b.classDate ? 1 : 0; });
      notes.forEach(function (n) {
        var row = body.querySelector('.cat-para[data-para="' + n.para + '"]');
        if (!row) return;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "anchored-note cat-note";
        btn.setAttribute("data-note-id", n.id);
        btn.textContent = n.text;
        if (n.classDate !== dateStr) {
          var tag = document.createElement("span");
          tag.className = "cat-note-date";
          tag.textContent = shortDate(n.classDate);
          btn.appendChild(tag);
        }
        // after the row and any notes already placed after it
        var after = row;
        while (after.nextElementSibling && after.nextElementSibling.classList.contains("cat-note")) after = after.nextElementSibling;
        after.insertAdjacentElement("afterend", btn);
      });
      var mine = notes.filter(function (n) { return n.classDate === dateStr; }).length;
      var count = slot.querySelector(".cat-lesson-count");
      if (count) count.textContent = notes.length
        ? "\u{1F4DD} " + notes.length + (notes.length === 1 ? " note" : " notes") + " on this lesson" + (mine && mine !== notes.length ? " (" + mine + " today)" : "")
        : "";
    }

    function openSheet(o) {
      // o: {id} to edit, or {para} to add on the current lesson
      var list = loadNotes();
      var existing = o.id ? list.filter(function (n) { return n.id === o.id; })[0] : null;
      var lid = existing ? existing.lesson : state.lesson;
      var lesson = lessonById(lid);
      var para = existing ? existing.para : o.para;
      var quote = existing ? existing.quote : quoteOf(lesson.blocks[para].text, 8);
      var backdrop = document.createElement("div");
      backdrop.className = "note-sheet-backdrop";
      backdrop.innerHTML =
        '<div class="note-sheet" role="dialog" aria-label="Class note">' +
        "<h3>Class note &middot; Lesson " + esc(lid) + (existing && existing.classDate !== dateStr ? " &middot; " + esc(shortDate(existing.classDate)) : "") + "</h3>" +
        '<p class="note-quote">&ldquo;' + esc(quote) + "&rdquo;</p>" +
        '<textarea class="notes-textarea" autocapitalize="sentences" placeholder="Your note…"></textarea>' +
        '<div class="note-sheet-actions">' +
        (existing ? '<button type="button" class="note-delete">Delete</button>' : "") +
        '<button type="button" class="note-cancel">Cancel</button>' +
        '<button type="button" class="note-save">Save</button>' +
        "</div></div>";
      document.body.appendChild(backdrop);
      var ta = backdrop.querySelector("textarea");
      ta.value = existing ? existing.text : "";
      setTimeout(function () { ta.focus(); }, 50);
      function close() { backdrop.remove(); }
      function after() { close(); if (state.view === "notes") renderOpen(); else { renderMarkers(); updateTabCount(); } }
      backdrop.addEventListener("click", function (e) { if (e.target === backdrop) close(); });
      backdrop.querySelector(".note-cancel").addEventListener("click", close);
      backdrop.querySelector(".note-save").addEventListener("click", function () {
        var text = ta.value.trim();
        var cur = loadNotes();
        if (existing) {
          cur = text
            ? cur.map(function (n) { return n.id === existing.id ? Object.assign({}, n, { text: text, updated: new Date().toISOString() }) : n; })
            : cur.filter(function (n) { return n.id !== existing.id; });
        } else if (text) {
          cur.push({ id: "c" + Date.now().toString(36), lesson: lid, classDate: dateStr, para: para, quote: quote,
                     section: sectionOf(lesson, para), text: text, updated: new Date().toISOString() });
          rememberLesson(dateStr, lid);
        }
        saveNotes(cur);
        after();
      });
      var del = backdrop.querySelector(".note-delete");
      if (del) del.addEventListener("click", function () {
        if (!confirm("Delete this note?")) return;
        saveNotes(loadNotes().filter(function (n) { return n.id !== existing.id; }));
        after();
      });
    }

    function updateTabCount() {
      var tab = slot.querySelector('.cat-tab[data-view="notes"]');
      var n = loadNotes().length;
      if (tab) tab.textContent = "Notes from class" + (n ? " (" + n + ")" : "");
    }

    function wireOpen() {
      var card = slot.querySelector(".cat-card");
      card.querySelectorAll(".cat-tab").forEach(function (t) {
        t.addEventListener("click", function () { state.view = t.getAttribute("data-view"); renderOpen(); });
      });
      var sel = card.querySelector(".cat-select");
      if (sel) sel.addEventListener("change", function () {
        state.lesson = sel.value;
        state.suggestion = null;
        rememberLesson(dateStr, sel.value);
        renderOpen();
        slot.querySelector(".cs-body").scrollTop = 0;
      });
      var rows = card.querySelector(".cat-rows");
      if (rows) {
        var timer = null, pressed = null, suppress = false, sx = 0, sy = 0;
        var cancel = function () {
          if (timer) clearTimeout(timer);
          timer = null;
          if (pressed) pressed.classList.remove("note-pressing");
          pressed = null;
        };
        rows.addEventListener("pointerdown", function (e) {
          suppress = false;
          var row = e.target.closest(".cat-para");
          if (!row || e.target.closest(".cat-note") || e.target.closest("a")) return;
          sx = e.clientX; sy = e.clientY; pressed = row;
          row.classList.add("note-pressing");
          timer = setTimeout(function () {
            timer = null;
            row.classList.remove("note-pressing");
            suppress = true;
            setTimeout(function () { suppress = false; }, 700);
            if (navigator.vibrate) navigator.vibrate(15);
            openSheet({ para: +row.getAttribute("data-para") });
          }, 550);
        });
        rows.addEventListener("pointermove", function (e) {
          if (timer && (Math.abs(e.clientX - sx) > 8 || Math.abs(e.clientY - sy) > 8)) cancel();
        });
        ["pointerup", "pointercancel", "pointerleave"].forEach(function (t) { rows.addEventListener(t, cancel); });
        rows.addEventListener("contextmenu", function (e) { if (e.target.closest(".cat-para")) e.preventDefault(); });
        rows.addEventListener("click", function (e) {
          if (suppress) { suppress = false; e.preventDefault(); e.stopPropagation(); return; }
          var marker = e.target.closest(".cat-note");
          if (marker) { e.preventDefault(); openSheet({ id: marker.getAttribute("data-note-id") }); }
        }, true);
      }
      var add = card.querySelector(".cat-add-note");
      if (add) add.addEventListener("click", function () {
        var minTop = card.querySelector(".cat-sticky").getBoundingClientRect().bottom;
        var target = null;
        card.querySelectorAll(".cat-rows .cat-para").forEach(function (row) {
          if (!target && row.getBoundingClientRect().bottom > minTop + 4) target = row;
        });
        if (target) openSheet({ para: +target.getAttribute("data-para") });
      });
      card.querySelectorAll(".cat-notes-view .cat-note").forEach(function (b) {
        b.addEventListener("click", function () { openSheet({ id: b.getAttribute("data-note-id") }); });
      });
      card.querySelectorAll(".cat-goto").forEach(function (b) {
        b.addEventListener("click", function () {
          state.lesson = b.getAttribute("data-lesson");
          state.suggestion = null;
          state.view = "lesson";
          renderOpen();
          var row = slot.querySelector('.cat-para[data-para="' + b.getAttribute("data-para") + '"]');
          if (row) {
            row.scrollIntoView({ block: "center" });
            row.classList.add("cat-flash");
            setTimeout(function () { row.classList.remove("cat-flash"); }, 1600);
          }
        });
      });
      card.querySelectorAll(".cat-doxy").forEach(function (b) {
        b.addEventListener("click", function () {
          var lid = b.getAttribute("data-lesson");
          var date = b.getAttribute("data-date");
          var scope = date
            ? { notes: loadNotes().filter(function (n) { return n.lesson === lid && n.classDate === date; }), date: date }
            : doxyScope(lid, dateStr);
          window.open(doxyUrl(lid, scope.date, scope.notes), "_blank", "noopener");
        });
      });
    }

    var loading = null;
    function ensureLoaded() {
      if (!loading) {
        inner().innerHTML = '<p class="cat-sub">Loading lessons&hellip;</p>';
        loading = load().then(function () {
          if (!state.lesson) {
            state.suggestion = lessonFor(dateStr, !!ev);
            state.lesson = state.suggestion.id;
          }
          renderOpen();
        }).catch(function () {
          loading = null;
          inner().innerHTML = '<p class="cat-sub">Couldn&rsquo;t load the lessons. Try again later.</p>';
        });
      }
      return loading;
    }
    // Open the section (if closed) on a given view, e.g. from the review reminder.
    function show(view) {
      var sec = section();
      var changed = state.view !== view;
      state.view = view;
      if (!sec.classList.contains("open")) window.BriefSections.setOpen(sec, true);  // fires cs-toggle -> ensureLoaded
      ensureLoaded().then(function () { if (changed) renderOpen(); sec.scrollIntoView({ block: "start" }); });
    }

    var recent = recentClassNotes();
    var meta = ev
      ? (dateStr === opts.today ? "Tonight" : shortDate(dateStr)) + (eventTime(ev.start) ? " · " + eventTime(ev.start) : "") + " · " + ev.title
      : "Lessons & class notes · look back anytime";
    slot.innerHTML =
      (recent
        ? '<button type="button" class="cat-open cat-recent" data-view="notes">&#x1F4DD; ' + recent.count +
          (recent.count === 1 ? " note" : " notes") + " from " + esc(shortDate(recent.date)) + "&rsquo;s catechism class (Lesson " +
          esc(recent.lesson) + ") &mdash; review</button>"
        : "") +
      window.BriefSections.render("catechism", ev ? "Catechism Class" : "Catechism lessons & class notes", meta,
        '<div class="cat-inner"></div>', ev ? "cs-highlight" : "");
    var sec = section();
    sec.id = "catechismCard";
    sec.addEventListener("cs-toggle", function (e) { if (e.detail.open) ensureLoaded(); });
    slot.addEventListener("click", function (e) {
      var b = e.target.closest(".cat-open");
      if (b) show(b.getAttribute("data-view"));
    });
  }

  window.Catechism = { mount: mount, classEvent: classEvent, doxyUrl: doxyUrl, _load: load };
})();
