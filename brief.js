/* Shared app code for the brief (index.html) and the Service Texts page
   (services.html, <body data-page="services">). */
(function () {
  "use strict";

  var app = document.getElementById("app");
  var dayCache = {};
  var index = null;
  var fastingData = null;
  var serviceOrder = [];
  var currentDate = null;
  var PAGE = document.body.getAttribute("data-page") || "brief";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function parseISODate(s) {
    var parts = s.split("-").map(Number);
    return new Date(parts[0], parts[1] - 1, parts[2]);
  }

  function formatISODate(d) {
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var day = String(d.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + day;
  }

  function addDays(dateStr, n) {
    var d = parseISODate(dateStr);
    d.setDate(d.getDate() + n);
    return formatISODate(d);
  }

  function friendlyDate(dateStr) {
    var d = parseISODate(dateStr);
    var weekday = d.toLocaleDateString(undefined, { weekday: "long" });
    var rest = d.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
    return { weekday: weekday, date: rest };
  }

  function formatEventTime(sqlDateTime) {
    if (!sqlDateTime) return "";
    var iso = sqlDateTime.replace(" ", "T");
    var d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    var isMidnight = d.getHours() === 0 && d.getMinutes() === 0;
    if (isMidnight) return "All day";
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function fetchJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status + " for " + url);
      return r.json();
    });
  }

  function loadIndex() {
    return fetchJSON("data/index.json").then(function (data) {
      index = data;
    });
  }

  var parish = null;
  function loadParish() {
    return fetchJSON("data/parish.json").then(function (d) { parish = d; }).catch(function () { parish = null; });
  }

  function loadDay(dateStr) {
    if (dayCache[dateStr]) return Promise.resolve(dayCache[dateStr]);
    return fetchJSON("data/days/" + dateStr + ".json").then(function (data) {
      dayCache[dateStr] = data;
      return data;
    });
  }

  function relativeStaleness(iso) {
    if (!iso) return "never";
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "unknown";
    var diffH = Math.round((Date.now() - then) / 36e5);
    if (diffH < 1) return "just now";
    if (diffH < 24) return diffH + "h ago";
    var diffD = Math.round(diffH / 24);
    return diffD + "d ago";
  }

  function renderFeastSection(lit) {
    if (!lit || !lit.feasts || !lit.feasts.length) return "";
    var badge = "";
    var heading = "Recognizing";
    if (lit.great_feast) {
      heading = "Feast";
      var label = lit.great_feast.is_pascha ? "Pascha" : "Great Feast";
      badge = '<p class="great-feast-badge">&#10022; ' + esc(label) + "</p>";
    }
    return (
      "<section class=\"card\"><h2>" + esc(heading) + "</h2>" +
      badge +
      '<p class="feast-title">' +
      lit.feasts.map(esc).join(" &middot; ") +
      "</p></section>"
    );
  }

  /* A collapsible section: thin bar (title, optional one-line meta, chevron),
     collapsed by default. See wireCollapsibles(). */
  function renderCollapsible(id, title, meta, inner, extraClass, foot) {
    return (
      '<section class="card cs' + (extraClass ? " " + extraClass : "") + '" data-cs="' + esc(id) + '">' +
      '<button type="button" class="cs-bar" aria-expanded="false" aria-controls="cs-body-' + esc(id) + '">' +
      '<span class="cs-titles"><span class="cs-title">' + esc(title) + '</span><span class="cs-meta">' + (meta ? esc(meta) : "") + "</span></span>" +
      '<span class="cs-chevron" aria-hidden="true"></span></button>' +
      '<div class="cs-body" id="cs-body-' + esc(id) + '" role="region" aria-label="' + esc(title) + '" hidden>' + inner + "</div>" +
      (foot || "") +
      "</section>"
    );
  }

  function renderSaintsSection(lit) {
    if (!lit || !lit.saints || !lit.saints.length) return "";
    var summary = lit.summary_title ? '<p class="summary-title">' + esc(lit.summary_title) + "</p>" : "";
    var synaxarion = lit.synaxarion
      ? '<div class="synaxarion-panel"><div class="service-text-body">' + esc(lit.synaxarion) + "</div></div>"
      : "";
    return renderCollapsible("synaxarion", "Synaxarion · Saints Commemorated", lit.summary_title || lit.saints.slice(0, 2).join(" · "),
      summary + synaxarion + renderNewAdvent(lit));
  }

  /* Biographies from the Catholic Encyclopedia (1913) on newadvent.org,
     attached by fetch.py only when the match is unambiguous. */
  function renderNewAdvent(lit) {
    if (!lit.newadvent || !lit.newadvent.length) return "";
    var blocks = lit.newadvent.map(function (b) {
      var more = b.more && b.more.length > b.excerpt.length + 40;
      return (
        '<details class="na-bio">' +
        '<summary><span class="na-title">' + esc(b.title) + '</span>' +
        '<span class="na-excerpt">' + esc(b.excerpt) + "</span>" +
        (more ? '<span class="na-more">Read more &#9662;</span>' : "") +
        "</summary>" +
        (more ? '<div class="na-full">' + b.more.split(/\n\n+/).map(function (para) { return "<p>" + esc(para) + "</p>"; }).join("") + "</div>" +
          '<button type="button" class="na-less">Read less &#9652;</button>' : "") +
        '<a class="na-link" href="' + esc(b.url) + '" target="_blank" rel="noopener">Full article on New Advent &#8599;</a>' +
        "</details>"
      );
    }).join("");
    return (
      '<div class="na-section"><h3 class="na-heading">From the Catholic Encyclopedia (New Advent)</h3>' +
      blocks +
      '<p class="na-note">Written in 1913 from a Roman Catholic perspective; Orthodox lives of the saints may differ.</p></div>'
    );
  }

  function renderStoriesSection(lit) {
    if (!lit || !lit.stories || !lit.stories.length) return "";
    var blocks = lit.stories
      .map(function (s) {
        return (
          '<div class="story-block"><p class="story-title">' +
          esc(s.title) +
          '</p><div class="story-text">' +
          s.html +
          "</div></div>"
        );
      })
      .join("");
    return '<section class="card"><h2>About Today&rsquo;s Saints</h2>' + blocks + "</section>";
  }

  /* Compact current-month fasting calendar shown at the top of the brief.
     Tapping it opens the full calendar (fasting.html) in a new tab. */
  function renderMiniFastingCalendar(dateStr) {
    if (!fastingData || !window.Fasting) return "";
    var F = window.Fasting;
    var y = +dateStr.slice(0, 4), m = +dateStr.slice(5, 7);
    var info = F.dayInfo(fastingData, dateStr);
    if (!info.covered) return "";
    var today = F.todayCentral();
    var who = dateStr === today ? "Today" : "This day";
    var line = info.levelInfo
      ? who + ": <strong>" + F.esc(info.levelInfo.label) + "</strong>"
      : info.fastFree ? who + ": fast-free" : who + ": no fast";
    var context = info.season ? info.season.name : info.fastFree ? info.fastFree.name.replace(/^Fast-free: /, "") : "";
    if (context) line += " &middot; " + F.esc(context);
    var levels = F.levelsInMonth(fastingData, y, m);
    var href = "fasting.html?m=" + y + "-" + F.pad(m) + "&d=" + dateStr;
    return (
      '<a class="mini-fast" href="' + href + '" target="_blank" rel="opener" aria-label="Open the full fasting calendar">' +
      '<section class="card"><div class="mini-fast-head"><h2>Fasting &middot; ' + F.MONTHS[m - 1] + " " + y + "</h2>" +
      '<span class="mini-fast-open">Full calendar &#8599;</span></div>' +
      F.monthGrid(fastingData, y, m, { today: today, selected: dateStr, compact: true }) +
      F.legend(fastingData, levels, { free: F.hasFastFree(fastingData, y, m), today: true }) +
      '<p class="mini-fast-today">' + line + "</p>" +
      (info.provisional ? '<p class="mini-fast-note">Provisional &mdash; the Archdiocese hasn&rsquo;t published this year&rsquo;s calendar yet.</p>' : "") +
      "</section></a>"
    );
  }

  function renderReadingSection(lit) {
    if (!lit || !lit.readings || !lit.readings.length) return "";
    var blocks = lit.readings
      .map(function (r) {
        var source = r.source_label ? '<span class="reading-source">from ' + esc(r.source_label) + "</span>" : "";
        return (
          '<div class="reading-block"><p class="reading-label">' +
          esc(r.display) +
          source +
          '</p><p class="reading-text">' +
          esc(r.text) +
          "</p></div>"
        );
      })
      .join("");
    var meta = lit.readings.map(function (r) { return r.display; }).join(" · ");
    return renderCollapsible("readings", "Daily Bible Readings", meta, blocks);
  }

  function renderTextChooser(groupId, items) {
    if (!items || !items.length) return "";
    // A row of chips (scrolls sideways if it doesn't fit), pinned at the top of
    // the section's scroll box so she can switch texts from anywhere in one.
    var chips = items
      .map(function (it, i) {
        return '<button type="button" class="text-chip" role="tab" aria-selected="false" data-group="' + esc(groupId) +
          '" data-index="' + i + '">' + esc(it.label) + "</button>";
      })
      .join("");
    var panels = items
      .map(function (it, i) {
        var body = it.html
          ? it.html
          : it.text
          ? '<div class="service-text-body">' + esc(it.text) + "</div>"
          : '<div class="service-text-body">' +
            '<p>Text isn&rsquo;t available inline for this one.</p>' +
            (it.pdf_url ? '<a href="' + esc(it.pdf_url) + '" target="_blank" rel="noopener">Open PDF &rarr;</a>' : "") +
            "</div>";
        return '<div class="service-text-panel" data-group="' + esc(groupId) + '" data-index="' + i + '" hidden>' + body + "</div>";
      })
      .join("");
    return (
      '<div class="text-switch" data-group="' + esc(groupId) + '"><div class="text-chips" role="tablist" aria-label="Choose a text">' + chips + "</div></div>" +
      '<p class="text-switch-prompt" data-group="' + esc(groupId) + '">Tap a text above to open it.</p>' +
      '<div class="service-text-scroll">' + panels + "</div>"
    );
  }

  function isHeadingLine(p) {
    return !/[a-z]/.test(p) && /[A-Z]/.test(p);
  }

  var SPEAKER_TAG_PATTERN = /^(priest|deacon|reader|people|choir|all|bishop|subdeacon|metropolitan)\s*:/i;

  // The first few lines of a service text are usually a title/date/commemorations
  // preamble, not anything actually said in the service. That preamble ends at
  // the first heading or the first line spoken by someone.
  function countPreambleLines(paragraphs) {
    var cap = Math.min(paragraphs.length, 8);
    for (var i = 0; i < cap; i++) {
      if (isHeadingLine(paragraphs[i]) || SPEAKER_TAG_PATTERN.test(paragraphs[i])) return i;
    }
    return 0;
  }

  /* ---- Parish practice (Divine Liturgy Full Outline) --------------------
     Aimee's notes from attending Liturgy at her parish, applied to the Full
     Outline every day it appears. This never adds liturgical text: it only
     re-styles existing lines (title / instruction / reading), hides blocks
     behind a toggle, repeats the choir response the book itself names after
     each petition, and adds short "Our parish" rubrics in her words.
     Paragraph indexes are untouched, so saved progress and pinned notes
     still line up. */
  var GOSPEL_BOOK_RE = /^(st\.?\s*)?(matthew|mark|luke|john)\b/i;
  function startsWithCI(p, prefix) { return p.toLowerCase().indexOf(prefix.toLowerCase()) === 0; }
  function findPara(paras, prefix, from) {
    for (var i = from || 0; i < paras.length; i++) if (startsWithCI(paras[i], prefix)) return i;
    return -1;
  }
  function verseSig(s) { var m = /[0-9]+:[0-9]+/.exec(s || ""); return m ? m[0] : null; }
  function isFallbackPara(p) { return /^\(See the parish bulletin/i.test(p); }

  function parishPractice(label, paras, lit) {
    var pp = { rows: {}, skips: [] };
    if (!/full outline/i.test(label || "")) return pp;
    function row(i) { return pp.rows[i] || (pp.rows[i] = { after: [] }); }
    var readings = (lit && lit.readings) || [];
    function matchReading(ref) {
      var sig = verseSig(ref);
      if (!sig) return null;
      return readings.filter(function (r) { return verseSig(r.display) === sig; })[0] || null;
    }

    // 1-2. Epistle and Gospel laid out as readings, with their titles shown
    // where the reader / deacon announces them.
    function reading(kicker, introPrefix, endPrefix, announcePrefix, pickFallback, rubric) {
      var intro = findPara(paras, introPrefix);
      if (intro < 0) return;
      var t = intro + 1, end = findPara(paras, endPrefix, t);
      if (t >= paras.length || end < 0 || end - t > 12) return;
      var ref = paras[t], fill = null;
      if (isFallbackPara(paras[t])) {
        fill = pickFallback();
        if (!fill) return;
        ref = fill.display;
        row(t).mode = "reading-title"; row(t).kicker = kicker; row(t).display = ref; row(t).fill = fill.text;
      } else {
        var r = matchReading(ref);
        if (r) ref = r.display;
        row(t).mode = "reading-title"; row(t).kicker = kicker; row(t).display = ref;
        for (var i = t + 1; i < end; i++) row(i).mode = "reading";
      }
      if (rubric) row(end - 1).after.push(rubric);
      var ann = findPara(paras, announcePrefix);
      if (ann >= 0 && ann < t) row(ann).ref = ref;
    }
    reading("The Epistle", "The reader chants the appointed epistle", "PRIEST: Peace be to thee that readest",
      "READER: (says the title of the epistle)",
      function () { return readings.filter(function (r) { return r.text && !GOSPEL_BOOK_RE.test(r.display || ""); })[0]; }, null);
    reading("The Gospel", "DEACON: Let us attend. (and he chants the appointed lection)", "PRIEST: Peace be to thee that proclaimest",
      "DEACON: The reading from the holy gospel according to",
      function () { return readings.filter(function (r) { return r.text && GOSPEL_BOOK_RE.test(r.display || ""); })[0]; },
      "The Gospel is read in English, then in Arabic.");

    // 3. The homily is a section title, not a checkbox.
    var hom = findPara(paras, "The priest preaches the homily");
    if (hom >= 0) { row(hom).mode = "title"; row(hom).display = "THE HOMILY"; }

    // 7. Prayer for the catechumens: skipped here.
    var cat = findPara(paras, "PRAYER FOR THE CATECHUMENS");
    if (cat >= 0) {
      var catEnd = cat + 1;
      while (catEnd < paras.length && !isHeadingLine(paras[catEnd])) catEnd++;
      if (catEnd - 1 > cat) pp.skips.push({ start: cat + 1, end: catEnd - 1, label: "Skipped at our parish (done during the Great Entrance)" });
    }

    // 8-9. "The choir responds X to each petition" is an instruction, not a
    // line to check off; the response goes after each petition instead.
    paras.forEach(function (p, i) {
      var m = /^The choir responds (.+?) to (this and the following|each) petitions?(?: until noted)?\.?$/i.exec(p);
      if (!m) return;
      var resp = "Choir: " + m[1].replace(/[.\s]+$/, "") + ".";
      row(i).mode = "instruction";
      var prev = i - 1;
      if (prev >= 0 && !isHeadingLine(paras[prev]) && (m[2].toLowerCase() !== "each" ? !SPEAKER_TAG_PATTERN.test(paras[prev]) : /^deacon\s*:/i.test(paras[prev]))) {
        row(prev).resp = resp;
      }
      for (var j = i + 1; j < paras.length; j++) {
        var q = paras[j];
        if (isHeadingLine(q) || SPEAKER_TAG_PATTERN.test(q) || /^The choir responds/i.test(q)) break;
        if (j + 1 < paras.length && /^choir\s*:/i.test(paras[j + 1])) break; // has its own response line
        row(j).resp = resp;
      }
    });

    // 10-14. Short parish rubrics.
    var bow = findPara(paras, "PRIEST: The grace of our Lord Jesus Christ and the love of God the Father");
    if (bow >= 0) row(bow).after.push("We bow.");
    var hearts = findPara(paras, "PRIEST: Let us lift up our hearts");
    if (hearts >= 0) row(hearts).after.push("We hold out our hands.");
    var epi = paras.indexOf("THE EPICLESIS");
    if (epi >= 0) {
      row(epi).after.push("The people say many of the deacon\u2019s responses here, mostly the Amens.");
      row(epi).after.push("The offering is collected during the Epiclesis.");
    }
    var ofa = paras.indexOf("THE OUR FATHER");
    if (ofa >= 0) row(ofa).after.push("We hold out our hands. Said in English, then in Arabic.");

    // 15. The fraction dialogue isn't said aloud here.
    var dv = findPara(paras, "DEACON: Divide, master, the holy bread");
    var dvEnd = dv >= 0 ? findPara(paras, "DEACON: The warmth of the Holy Spirit", dv) : -1;
    if (dv >= 0 && dvEnd >= dv && dvEnd - dv < 15) pp.skips.push({ start: dv, end: dvEnd, label: "Not said aloud at our parish" });
    return pp;
  }

  /* The parish's own apolytikion (data/parish.json), sung every Sunday at
     the Little Entrance after the day's apolytikia and before the kontakion.
     Not added when the day's texts already have it (their feast, May 21). */
  var PARISH_APOLY_PRESENT_RE = /apolytikion\s+(?:of|for)\s+(?:ss|sts|saints)\.?\s+constantine\s+and\s+helen|Constantine who is Thine Apostle/i;
  function parishApolytikionFor(lit, dateStr) {
    var cfg = parish && parish.sunday_apolytikion;
    if (!cfg || !cfg.text || parseISODate(dateStr).getDay() !== 0) return null;
    var present = (lit.service_texts || []).some(function (s) { return PARISH_APOLY_PRESENT_RE.test(s.text || ""); });
    return present ? null : cfg;
  }
  // Index of the paragraph the parish apolytikion goes after, or -1.
  function parishApolytikionSlot(label, paras) {
    if (!/divine liturgy/i.test(label || "")) return -1;
    var i;
    if (/full outline/i.test(label)) {
      // After the apolytikia the choir chants following the eisodikon,
      // before the thrice-holy prayer line and the kontakion.
      var start = findPara(paras, "Then, the choir chants the appointed apolytikia");
      if (start < 0) return -1;
      for (i = start + 1; i < paras.length; i++) {
        if (/^The priest says the prayer of the thrice-holy hymn/i.test(paras[i]) || /^The clergy then chant the appointed kontakion/i.test(paras[i])) return i - 1;
        if (isHeadingLine(paras[i])) return -1;
      }
      return -1;
    }
    // Variables: right after "Now sing the apolytikion of the patron saint
    // or feast of the temple", else after the last apolytikion before the
    // first kontakion.
    for (i = 0; i < paras.length; i++) if (/apolytikion of the patron saint/i.test(paras[i])) return i;
    var lastApoly = -1;
    for (i = 0; i < paras.length; i++) {
      if (/^KONTAKION\b/.test(paras[i])) return lastApoly >= 0 ? i - 1 : -1;
      if (isHeadingLine(paras[i]) && /APOLYTIKION/.test(paras[i])) lastApoly = i;
    }
    return -1;
  }
  function parishApolytikionHtml(cfg) {
    return '<div class="parish-insert"><div class="parish-insert-head"><span class="parish-tag">Our parish</span> <span class="parish-insert-title">' + esc(cfg.heading) +
      '</span></div><p class="parish-insert-text"><span class="line-text">' + esc(cfg.text) + "</span></p></div>";
  }

  function parishRubricHtml(text) {
    return '<div class="parish-rubric"><span class="parish-tag">Our parish</span> <span class="parish-rubric-text">' + esc(text) + "</span></div>";
  }

  function renderCheckableBody(text, storageKey, serviceLabel, lit, apoly) {
    var paragraphs = text.split(/\n+/).map(function (p) { return p.trim(); }).filter(Boolean);
    var apolySlot = apoly ? parishApolytikionSlot(serviceLabel, paragraphs) : -1;
    var preambleCount = countPreambleLines(paragraphs);
    var pp = parishPractice(serviceLabel, paragraphs, lit);
    function rowHtml(p, i) {
      var r = pp.rows[i] || { after: [] };
      var after = r.after.map(parishRubricHtml).join("");
      if (i < preambleCount) {
        return '<div class="checkable-line meta-line" data-para="' + i + '"><span class="line-text">' + esc(p) + "</span></div>";
      }
      if (r.mode === "title") {
        return '<div class="checkable-line heading-line parish-title" data-para="' + i + '"><span class="line-main"><span class="line-text">' + esc(r.display) + '</span><span class="title-sub">' + esc(p) + "</span></span></div>" + after;
      }
      if (r.mode === "instruction") {
        return '<div class="checkable-line rubric-line" data-para="' + i + '"><span class="line-text">' + esc(p) + "</span></div>" + after;
      }
      if (r.mode === "reading-title") {
        return '<div class="checkable-line reading-title" data-para="' + i + '"><span class="line-main"><span class="reading-kicker">&#x1F4D6; ' + esc(r.kicker) + '</span><span class="line-text">' + esc(r.display) + "</span></span></div>" +
          (r.fill ? '<div class="reading-fill"><p class="reading-fill-src">From today&rsquo;s readings</p><p>' + esc(r.fill) + "</p></div>" : "") + after;
      }
      if (isHeadingLine(p)) {
        return '<div class="checkable-line heading-line" data-para="' + i + '"><span class="line-text">' + esc(p) + "</span></div>" + after;
      }
      var extra = (r.resp ? '<span class="choir-resp">' + esc(r.resp) + "</span>" : "") + (r.ref ? '<span class="line-ref">' + esc(r.ref) + "</span>" : "");
      return (
        '<label class="checkable-line' + (r.mode === "reading" ? " reading-line" : "") + '" data-para="' + i + '">' +
        '<input type="checkbox" class="line-check" data-index="' + i + '">' +
        (extra ? '<span class="line-main"><span class="line-text">' + esc(p) + "</span>" + extra + "</span>" : '<span class="line-text">' + esc(p) + "</span>") +
        "</label>" + after
      );
    }
    var html = [];
    for (var i = 0; i < paragraphs.length; i++) {
      var sk = pp.skips.filter(function (s) { return s.start === i; })[0];
      if (sk) {
        var inner = [];
        for (var j = sk.start; j <= sk.end; j++) inner.push(rowHtml(paragraphs[j], j));
        html.push(
          '<div class="parish-skip collapsed">' +
          '<button type="button" class="parish-skip-toggle" aria-expanded="false">' +
          '<span class="parish-tag">Our parish</span> <span class="parish-rubric-text">' + esc(sk.label) + '</span><span class="parish-skip-show">Show text</span></button>' +
          '<div class="parish-skip-rows">' + inner.join("") + "</div></div>"
        );
        i = sk.end;
        continue;
      }
      html.push(rowHtml(paragraphs[i], i));
      if (i === apolySlot) html.push(parishApolytikionHtml(apoly));
    }
    var rows = html.join("");
    return (
      '<div class="checkable-body" data-storage-key="' + esc(storageKey) + '" data-service="' + esc(serviceLabel || "") + '">' +
      '<div class="checkable-header">' +
      '<div class="header-actions">' +
      '<button type="button" class="jump-to-progress" aria-label="Jump to last checked line">&#8617; Resume where I left off</button>' +
      '<button type="button" class="add-note-btn" aria-label="Add a note at the top visible line">&#xFF0B; Note</button>' +
      "</div>" +
      '<div class="text-search-bar">' +
      '<input type="text" class="text-search-input" placeholder="Search this text&hellip;" autocomplete="off" autocapitalize="off">' +
      '<button type="button" class="text-search-clear" aria-label="Clear search" hidden>&times;</button>' +
      '<span class="text-search-count"></span>' +
      '<button type="button" class="text-search-nav" data-dir="-1" aria-label="Previous match">&uarr;</button>' +
      '<button type="button" class="text-search-nav" data-dir="1" aria-label="Next match">&darr;</button>' +
      "</div>" +
      "</div>" +
      '<div class="checkable-rows">' + rows + "</div>" +
      "</div>"
    );
  }

  /* Chip order: the Full Outline sits immediately before the Variables
     (she follows the Full Outline; the Variables are the reference). */
  function orderedServiceTexts(lit) {
    var list = (lit && lit.service_texts ? lit.service_texts : []).slice();
    var fi = -1, vi = -1;
    list.forEach(function (s, i) {
      if (/full outline/i.test(s.label)) fi = i;
      else if (/divine liturgy variables/i.test(s.label)) vi = i;
    });
    if (fi > vi && vi >= 0) {
      var full = list.splice(fi, 1)[0];
      list.splice(vi, 0, full);
    }
    return list;
  }

  /* On the brief, Service Texts is a bar that opens the Service Texts page
     (services.html) in a new tab, like the fasting calendar. */
  function renderServicesLink(lit, dateStr) {
    var texts = orderedServiceTexts(lit);
    if (!texts.length) return "";
    return (
      '<section class="card cs cs-link-card" data-cs="services">' +
      '<a class="cs-bar cs-link" href="services.html?d=' + esc(dateStr) + '" target="_blank" rel="opener" aria-label="Open the service texts in a new tab">' +
      '<span class="cs-titles"><span class="cs-title">Service Texts</span><span class="cs-meta">' +
      esc(texts.map(function (s) { return s.label; }).join(" · ")) + "</span>" +
      '<span class="cs-notes-count" data-date="' + esc(dateStr) + '">' + notesCountText(dateStr) + "</span></span>" +
      '<span class="cs-open" aria-hidden="true">&#8599;</span></a></section>'
    );
  }
  function notesCountText(dateStr) {
    var n = loadAnchored(dateStr).length;
    return n ? "&#x1F4DD; " + n + (n === 1 ? " note" : " notes") : "";
  }
  // Notes added on the services page (another tab) show up in the count here.
  window.addEventListener("storage", function () {
    document.querySelectorAll(".cs-notes-count").forEach(function (el) { el.innerHTML = notesCountText(el.getAttribute("data-date")); });
  });

  // The old Liturgy Notes box (removed): text she typed there is kept, shown
  // read-only on the services page with a Delete button, and sent with Send
  // to Doxy, so nothing is lost.
  function legacyLiturgyNotes(dateStr) {
    var v = localStorage.getItem("liturgyNotes:" + dateStr) || "";
    return v.split("\n").filter(function (l) { return l.replace(/[\u2022\s]/g, ""); }).join("\n").trim();
  }

  function renderServicesPageBody(lit, dateStr) {
    var texts = orderedServiceTexts(lit);
    var apoly = parishApolytikionFor(lit, dateStr);
    var items = texts.map(function (s) {
      if (!s.text) return s;
      var storageKey = "serviceProgress:" + dateStr + ":" + s.label;
      return { label: s.label, html: renderCheckableBody(s.text, storageKey, s.label, lit, apoly) };
    });
    serviceOrder = texts.map(function (s) { return s.label; });
    return '<section class="card svc-page">' + renderTextChooser("service", items) + "</section>";
  }

  /* Parish Events and the Prayer List share one collapsible bar. Each part
     returns {body, meta} or null when there's nothing to show. */
  function eventsPart(day) {
    if (!day.events || !day.events.length) return null;
    var items = day.events
      .map(function (ev) {
        var time = formatEventTime(ev.start);
        var body = '<span class="event-time">' + esc(time) + '</span><span class="event-title">' + esc(ev.title) + "</span>";
        return (
          '<li class="event-item">' +
          (ev.url ? '<a href="' + esc(ev.url) + '" target="_blank" rel="noopener">' + body + "</a>" : body) +
          "</li>"
        );
      })
      .join("");
    return {
      meta: day.events.length + (day.events.length === 1 ? " event" : " events"),
      body: '<ul class="event-list">' + items +
        '</ul><p class="fast-cal-link" style="margin-top:0.6rem"><a href="' +
        esc(index.links.parish_calendar) +
        '" target="_blank" rel="noopener">Parish calendar &rarr;</a></p>',
    };
  }

  function prayerListPart() {
    var pl = index.prayer_list;
    if (!pl) return null;
    var groups = [
      { key: "sick", label: "For the health of body and soul" },
      { key: "military", label: "Serving in our armed forces" },
      { key: "catechumens", label: "Our catechumens" },
    ];
    var body = groups
      .map(function (g) {
        var names = pl[g.key];
        if (!names || !names.length) return "";
        return (
          '<div class="prayer-group"><h3>' +
          esc(g.label) +
          '</h3><p class="prayer-names">' +
          names.map(esc).join(", ") +
          "</p></div>"
        );
      })
      .join("");
    if (!body) return null;
    var total = groups.reduce(function (n, g) { return n + ((pl[g.key] || []).length); }, 0);
    return { meta: total + (total === 1 ? " name" : " names"), body: body };
  }

  function renderParishSection(day) {
    var ev = eventsPart(day), pr = prayerListPart();
    if (!ev && !pr) return "";
    var meta = [ev ? ev.meta : "No events today", pr ? pr.meta : ""].filter(Boolean).join(" · ");
    var inner =
      '<div class="parish-part parish-events"><h3 class="parish-sub">Parish Events Today</h3>' +
      (ev ? ev.body : '<p class="parish-none">No parish events today.</p>') + "</div>" +
      (pr ? '<div class="parish-part parish-prayer"><h3 class="parish-sub">Prayer List</h3>' + pr.body + "</div>" : "");
    return renderCollapsible("parish", "Parish Events & Prayer List", meta, inner);
  }

  function renderStalenessBanners() {
    var s = index.status || {};
    var messages = [];
    if (!s.liturgical_ok) messages.push("Couldn&rsquo;t reach the liturgical calendar this morning &mdash; showing the most recent data available.");
    if (!s.parish_events_ok) messages.push("Couldn&rsquo;t reach the parish calendar this morning &mdash; events shown may be out of date.");
    if (!s.bulletin_ok) messages.push("Couldn&rsquo;t find a new bulletin &mdash; linking to the last known one.");
    if (s.fasting_data_ok === false) messages.push("Couldn&rsquo;t refresh the fasting calendar &mdash; showing the last known one.");
    if (!s.prayer_list_ok) messages.push("Couldn&rsquo;t read the prayer list from the bulletin &mdash; showing the last known list.");
    if (!messages.length) return "";
    return messages.map(function (m) { return '<div class="banner">' + m + "</div>"; }).join("");
  }

  function renderFooter() {
    var ls = index.last_successful || {};
    return (
      "<footer>Liturgical data updated " +
      esc(relativeStaleness(ls.liturgical)) +
      " &middot; Parish calendar updated " +
      esc(relativeStaleness(ls.parish_events)) +
      "<br>Brief generated " +
      esc(relativeStaleness(index.generated_at)) +
      "</footer>"
    );
  }

  function canGoBack(dateStr) {
    return dateStr > index.available_dates[0];
  }
  function canGoForward(dateStr) {
    return dateStr < index.available_dates[index.available_dates.length - 1];
  }

  function renderNavBar(dateStr) {
    var fd = friendlyDate(dateStr);
    return (
      '<div class="nav-bar">' +
      '<button class="nav-btn" id="prevBtn" ' + (canGoBack(dateStr) ? "" : "disabled") + ' aria-label="Previous day">&larr;</button>' +
      '<button type="button" class="date-heading" id="datePickerBtn" aria-haspopup="dialog" aria-expanded="false" aria-label="Choose a date">' +
      '<span class="weekday">' + esc(fd.weekday) + '</span><span class="date">' + esc(fd.date) + ' <span class="date-caret">&#9662;</span></span></button>' +
      '<button class="nav-btn" id="nextBtn" ' + (canGoForward(dateStr) ? "" : "disabled") + ' aria-label="Next day">&rarr;</button>' +
      "</div>" +
      '<div class="date-picker" id="datePicker" role="dialog" aria-label="Choose a date" hidden>' +
      '<div class="date-picker-row">' +
      '<label><span>Year</span><select id="dpYear"></select></label>' +
      '<label><span>Month</span><select id="dpMonth"></select></label>' +
      '<label><span>Day</span><select id="dpDay"></select></label>' +
      "</div>" +
      '<div class="date-picker-actions">' +
      '<button type="button" class="dp-today" id="dpToday">Today</button>' +
      '<button type="button" class="dp-go" id="dpGo">Go</button>' +
      "</div></div>"
    );
  }

  var MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

  function wireDatePicker(dateStr) {
    var btn = document.getElementById("datePickerBtn");
    var box = document.getElementById("datePicker");
    var ySel = document.getElementById("dpYear"), mSel = document.getElementById("dpMonth"), dSel = document.getElementById("dpDay");
    if (!btn || !box) return;
    var dates = index.available_dates;
    function uniq(arr) { return arr.filter(function (v, i) { return arr.indexOf(v) === i; }); }
    function fill(sel, values, labelFn, want) {
      sel.innerHTML = values.map(function (v) { return '<option value="' + v + '">' + esc(labelFn(v)) + "</option>"; }).join("");
      sel.value = values.indexOf(want) !== -1 ? want : values[0];
    }
    function fillMonths(wantM, wantD) {
      var months = uniq(dates.filter(function (d) { return d.slice(0, 4) === ySel.value; }).map(function (d) { return d.slice(5, 7); }));
      fill(mSel, months, function (m) { return MONTH_NAMES[+m - 1]; }, wantM);
      fillDays(wantD);
    }
    function fillDays(wantD) {
      var prefix = ySel.value + "-" + mSel.value + "-";
      var days = dates.filter(function (d) { return d.indexOf(prefix) === 0; }).map(function (d) { return d.slice(8, 10); });
      fill(dSel, days, function (d) {
        var wd = parseISODate(prefix + d).toLocaleDateString("en-US", { weekday: "short" });
        return +d + " (" + wd + ")";
      }, wantD);
    }
    function open() {
      fill(ySel, uniq(dates.map(function (d) { return d.slice(0, 4); })), function (y) { return y; }, dateStr.slice(0, 4));
      fillMonths(dateStr.slice(5, 7), dateStr.slice(8, 10));
      box.hidden = false;
      btn.setAttribute("aria-expanded", "true");
    }
    function close() { box.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    btn.addEventListener("click", function () { if (box.hidden) open(); else close(); });
    ySel.addEventListener("change", function () { fillMonths(mSel.value, dSel.value); });
    mSel.addEventListener("change", function () { fillDays(dSel.value); });
    document.getElementById("dpGo").addEventListener("click", function () {
      close();
      goTo(ySel.value + "-" + mSel.value + "-" + dSel.value);
    });
    document.getElementById("dpToday").addEventListener("click", function () { close(); goTo(index.today); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); }, { once: true });
  }

  // Arrows step to the nearest day we actually have (a backfill may leave gaps).
  function stepAvailable(dateStr, dir) {
    var dates = index.available_dates;
    if (dir > 0) {
      for (var i = 0; i < dates.length; i++) if (dates[i] > dateStr) return dates[i];
    } else {
      for (var j = dates.length - 1; j >= 0; j--) if (dates[j] < dateStr) return dates[j];
    }
    return null;
  }

  function wireNavBar(dateStr) {
    var prevBtn = document.getElementById("prevBtn");
    var nextBtn = document.getElementById("nextBtn");
    var todayBtn = document.getElementById("todayBtn");
    if (prevBtn) prevBtn.addEventListener("click", function () { var d = stepAvailable(dateStr, -1); if (d) goTo(d); });
    if (nextBtn) nextBtn.addEventListener("click", function () { var d = stepAvailable(dateStr, 1); if (d) goTo(d); });
    if (todayBtn) todayBtn.addEventListener("click", function () { goTo(index.today); });
    wireDatePicker(dateStr);
  }

  function wireTextChoosers() {
    document.querySelectorAll(".text-switch").forEach(function (sw) {
      var group = sw.getAttribute("data-group");
      var sec = sw.closest(".cs, .svc-page");
      // Stack the text's own sticky header (Resume / Note / search) right under the chips.
      function measure() { if (sec && sw.offsetHeight) sec.style.setProperty("--switch-h", sw.offsetHeight + "px"); }
      if (sec) sec.addEventListener("cs-toggle", function () { setTimeout(measure, 0); });
      window.addEventListener("resize", measure);
      measure();
      sw.addEventListener("click", function (e) {
        var chip = e.target.closest(".text-chip");
        if (!chip) return;
        var idx = chip.getAttribute("data-index");
        sw.querySelectorAll(".text-chip").forEach(function (c) {
          var on = c === chip;
          c.classList.toggle("active", on);
          c.setAttribute("aria-selected", on ? "true" : "false");
        });
        document.querySelectorAll('.service-text-panel[data-group="' + group + '"]').forEach(function (p) {
          p.hidden = p.getAttribute("data-index") !== idx;
        });
        var prompt = document.querySelector('.text-switch-prompt[data-group="' + group + '"]');
        if (prompt) prompt.hidden = true;
        chip.scrollIntoView({ block: "nearest", inline: "nearest" });
        // Start the newly chosen text at its top (Resume jumps to her place).
        var scroller = sw.closest(".cs-body");
        var panel = document.querySelector('.service-text-panel[data-group="' + group + '"][data-index="' + idx + '"]');
        if (scroller && panel) scroller.scrollTop = Math.max(0, panel.offsetTop - scroller.offsetTop - sw.offsetHeight);
        measure();
      });
    });
  }

  function applyProgress(container, n) {
    container.querySelectorAll(".line-check").forEach(function (cb) {
      cb.checked = parseInt(cb.getAttribute("data-index"), 10) < n;
    });
  }

  function wireCheckableBodies() {
    document.querySelectorAll(".checkable-body").forEach(function (container) {
      var key = container.getAttribute("data-storage-key");
      var saved = parseInt(localStorage.getItem(key) || "0", 10);
      applyProgress(container, saved);
      container.addEventListener("change", function (e) {
        if (!e.target.classList.contains("line-check")) return;
        var idx = parseInt(e.target.getAttribute("data-index"), 10);
        var n = e.target.checked ? idx + 1 : idx;
        applyProgress(container, n);
        localStorage.setItem(key, String(n));
      });
    });
  }

  function wireJumpButtons() {
    document.querySelectorAll(".checkable-body").forEach(function (container) {
      var btn = container.querySelector(".jump-to-progress");
      if (!btn) return;
      btn.addEventListener("click", function () {
        var key = container.getAttribute("data-storage-key");
        var n = parseInt(localStorage.getItem(key) || "0", 10);
        if (n <= 0) return;
        var line = null;
        container.querySelectorAll(".checkable-rows .checkable-line[data-para]").forEach(function (r) {
          if (+r.getAttribute("data-para") <= n - 1) line = r;
        });
        if (!line) return;
        ensureSectionOpen(line);
        line.scrollIntoView({ block: "center" });
        line.classList.add("search-current");
        setTimeout(function () { line.classList.remove("search-current"); }, 1500);
      });
    });
  }

  function wireTextSearch() {
    document.querySelectorAll(".checkable-body").forEach(function (container) {
      var input = container.querySelector(".text-search-input");
      var clearBtn = container.querySelector(".text-search-clear");
      var countEl = container.querySelector(".text-search-count");
      var lineEls = container.querySelectorAll(".line-text");
      var matches = [];
      var currentIndex = -1;

      function goToMatch(i) {
        if (!matches.length) return;
        currentIndex = ((i % matches.length) + matches.length) % matches.length;
        matches.forEach(function (el) { el.parentElement.classList.remove("search-current"); });
        var target = matches[currentIndex];
        ensureSectionOpen(target);
        target.parentElement.classList.add("search-current");
        target.scrollIntoView({ block: "center" });
        countEl.textContent = (currentIndex + 1) + " of " + matches.length;
      }

      function runSearch() {
        var query = input.value.trim().toLowerCase();
        clearBtn.hidden = !input.value;
        matches = [];
        currentIndex = -1;
        lineEls.forEach(function (el) {
          el.parentElement.classList.remove("search-current");
          var text = el.textContent;
          if (!query) {
            el.textContent = text;
            return;
          }
          var idx = text.toLowerCase().indexOf(query);
          if (idx === -1) {
            el.textContent = text;
            return;
          }
          el.innerHTML =
            esc(text.slice(0, idx)) +
            "<mark>" + esc(text.slice(idx, idx + query.length)) + "</mark>" +
            esc(text.slice(idx + query.length));
          matches.push(el);
        });
        if (matches.length) {
          goToMatch(0);
        } else {
          countEl.textContent = query ? "No matches" : "";
        }
      }

      input.addEventListener("input", runSearch);
      // Clearing must not pop the keyboard up or move her place in the text:
      // keep the tap from focusing anything, clear, then blur the input and
      // restore the scroll positions the keyboard/relayout may have nudged.
      // (mousedown, not touchstart: cancelling touchstart would also cancel
      // the click on touch screens.)
      ["pointerdown", "mousedown"].forEach(function (evt) {
        clearBtn.addEventListener(evt, function (e) { e.preventDefault(); });
      });
      clearBtn.addEventListener("click", function (e) {
        e.preventDefault();
        var panel = container.closest(".cs-body") || container.closest(".service-text-panel");
        var panelTop = panel ? panel.scrollTop : 0;
        var winY = window.scrollY;
        input.value = "";
        runSearch();
        input.blur();
        if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
        if (panel) panel.scrollTop = panelTop;
        window.scrollTo(window.scrollX, winY);
      });
      container.querySelectorAll(".text-search-nav").forEach(function (btn) {
        btn.addEventListener("click", function () {
          goToMatch(currentIndex + parseInt(btn.getAttribute("data-dir"), 10));
        });
      });
    });
  }

  /* ---- Anchored notes -------------------------------------------------
     Notes pinned to a line of a service text, saved on this device only
     (localStorage, one list per day). Press-and-hold a line, or tap
     "＋ Note" to pin to the top visible line. */
  function anchoredKey(dateStr) { return "anchoredNotes:" + dateStr; }
  function loadAnchored(dateStr) {
    try { return JSON.parse(localStorage.getItem(anchoredKey(dateStr)) || "[]"); } catch (e) { return []; }
  }
  function saveAnchored(dateStr, list) {
    if (list.length) localStorage.setItem(anchoredKey(dateStr), JSON.stringify(list));
    else localStorage.removeItem(anchoredKey(dateStr));
  }
  function quoteOf(text, words) {
    var w = text.trim().split(/\s+/);
    return w.slice(0, words).join(" ") + (w.length > words ? "…" : "");
  }
  function sortedAnchored(dateStr) {
    return loadAnchored(dateStr).slice().sort(function (a, b) {
      var sa = serviceOrder.indexOf(a.service), sb = serviceOrder.indexOf(b.service);
      if (sa !== sb) return (sa < 0 ? 999 : sa) - (sb < 0 ? 999 : sb);
      return a.para - b.para;
    });
  }

  function renderAnchoredMarkers(dateStr) {
    var notes = loadAnchored(dateStr);
    document.querySelectorAll(".checkable-body .anchored-note").forEach(function (el) { el.remove(); });
    notes.forEach(function (n) {
      document.querySelectorAll(".checkable-body").forEach(function (body) {
        if (body.getAttribute("data-service") !== n.service) return;
        var row = body.querySelector('.checkable-line[data-para="' + n.para + '"]');
        if (!row) return;
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "anchored-note";
        btn.setAttribute("data-note-id", n.id);
        btn.textContent = n.text;
        row.insertAdjacentElement("afterend", btn);
      });
    });
    var summary = document.querySelector(".anchored-summary");
    if (summary) {
      var counts = {};
      notes.forEach(function (n) { counts[n.service] = (counts[n.service] || 0) + 1; });
      summary.innerHTML = notes.length
        ? "&#x1F4DD; " + notes.length + (notes.length === 1 ? " note" : " notes") + " in " +
          Object.keys(counts).length + (Object.keys(counts).length === 1 ? " text" : " texts")
        : "No notes yet &middot; press and hold a line to add one";
    }
  }

  function openNoteSheet(dateStr, opts) {
    // opts: {id} to edit, or {service, para, lineText} to add
    var list = loadAnchored(dateStr);
    var existing = opts.id ? list.filter(function (n) { return n.id === opts.id; })[0] : null;
    var quote = existing ? existing.quote : quoteOf(opts.lineText, 8);
    var service = existing ? existing.service : opts.service;
    var backdrop = document.createElement("div");
    backdrop.className = "note-sheet-backdrop";
    backdrop.innerHTML =
      '<div class="note-sheet" role="dialog" aria-label="Note">' +
      "<h3>Note &middot; " + esc(service) + "</h3>" +
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
    backdrop.addEventListener("click", function (e) { if (e.target === backdrop) close(); });
    backdrop.querySelector(".note-cancel").addEventListener("click", close);
    backdrop.querySelector(".note-save").addEventListener("click", function () {
      var text = ta.value.trim();
      var cur = loadAnchored(dateStr);
      if (existing) {
        cur = cur.map(function (n) { return n.id === existing.id ? Object.assign({}, n, { text: text, updated: new Date().toISOString() }) : n; });
        if (!text) cur = cur.filter(function (n) { return n.id !== existing.id; });
      } else if (text) {
        cur.push({ id: "n" + Date.now().toString(36), service: service, para: opts.para, quote: quote, text: text, updated: new Date().toISOString() });
      }
      saveAnchored(dateStr, cur);
      close();
      renderAnchoredMarkers(dateStr);
    });
    var del = backdrop.querySelector(".note-delete");
    if (del) del.addEventListener("click", function () {
      if (!confirm("Delete this note?")) return;
      saveAnchored(dateStr, loadAnchored(dateStr).filter(function (n) { return n.id !== existing.id; }));
      close();
      renderAnchoredMarkers(dateStr);
    });
  }

  function wireAnchoredNotes(dateStr) {
    document.querySelectorAll(".checkable-body").forEach(function (body) {
      var service = body.getAttribute("data-service");
      var rows = body.querySelector(".checkable-rows");
      var timer = null, pressed = null, suppressClick = false, startX = 0, startY = 0;
      function cancel() {
        if (timer) clearTimeout(timer);
        timer = null;
        if (pressed) pressed.classList.remove("note-pressing");
        pressed = null;
      }
      rows.addEventListener("pointerdown", function (e) {
        var row = e.target.closest(".checkable-line");
        if (!row || e.target.closest(".anchored-note")) return;
        startX = e.clientX; startY = e.clientY;
        pressed = row;
        row.classList.add("note-pressing");
        timer = setTimeout(function () {
          timer = null;
          row.classList.remove("note-pressing");
          suppressClick = true;
          if (navigator.vibrate) navigator.vibrate(15);
          openNoteSheet(dateStr, { service: service, para: +row.getAttribute("data-para"), lineText: row.querySelector(".line-text").textContent });
        }, 550);
      });
      rows.addEventListener("pointermove", function (e) {
        if (timer && (Math.abs(e.clientX - startX) > 8 || Math.abs(e.clientY - startY) > 8)) cancel();
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach(function (t) { rows.addEventListener(t, cancel); });
      rows.addEventListener("contextmenu", function (e) { if (e.target.closest(".checkable-line")) e.preventDefault(); });
      // A long-press must not also toggle the line's checkbox.
      rows.addEventListener("click", function (e) {
        if (suppressClick) { suppressClick = false; e.preventDefault(); e.stopPropagation(); return; }
        var marker = e.target.closest(".anchored-note");
        if (marker) { e.preventDefault(); openNoteSheet(dateStr, { id: marker.getAttribute("data-note-id") }); }
      }, true);

      body.querySelector(".add-note-btn").addEventListener("click", function () {
        // Pin to the first line visible below the sticky header.
        var panel = body.closest(".service-text-panel");
        var header = body.querySelector(".checkable-header");
        var minTop = header.getBoundingClientRect().bottom;
        var target = null;
        body.querySelectorAll(".checkable-rows .checkable-line").forEach(function (row) {
          if (target) return;
          if (row.getBoundingClientRect().bottom > minTop + 4) target = row;
        });
        if (!target) return;
        openNoteSheet(dateStr, { service: service, para: +target.getAttribute("data-para"), lineText: target.querySelector(".line-text").textContent });
      });
    });
    renderAnchoredMarkers(dateStr);
  }

  document.addEventListener("click", onDoxyClick);
  function onDoxyClick(e) {
    var btn = e.target.closest(".notes-doxy");
    if (!btn) return;
    window.open(doxyIssueUrl(currentDate, legacyLiturgyNotes(currentDate)), "_blank", "noopener");
  }

  /* "Send to Doxy": a prefilled GitHub new-issue link. No token or secret is
     involved; GitHub asks her to sign in and she submits it herself. */
  var DOXY_ISSUE_BASE = "https://github.com/aimee-blanchard/daily-orthodox-brief/issues/new";
  var DOXY_MAX_URL = 7500;

  function doxyIssueUrl(dateStr, generalNote) {
    var fd = friendlyDate(dateStr);
    var title = "Service notes – " + fd.weekday + ", " + fd.date;
    var general = (generalNote || "").replace(/^[•\s]+$/, "").trim();
    var anchored = sortedAnchored(dateStr);
    function clip(t, n) { return n == null || t.length <= n ? t : t.slice(0, n) + "…"; }
    function build(limit) {
      var out = "**Date:** " + fd.weekday + ", " + fd.date + " (" + dateStr + ")\n";
      var lastService = null;
      anchored.forEach(function (n, i) {
        if (n.service !== lastService) {
          out += "\n### " + n.service + "\n";
          lastService = n.service;
        }
        out += "\n**" + (i + 1) + ".** At “" + n.quote + "” (paragraph " + (n.para + 1) + ")\n\n" +
          clip(n.text, limit).split("\n").map(function (l) { return "> " + l; }).join("\n") + "\n";
      });
      if (general) out += "\n### Liturgy Notes (earlier notes box)\n\n" + clip(general, limit) + "\n";
      if (!anchored.length && !general) out += "\n(No notes yet.)\n";
      if (limit != null) out += "\n> ⚠️ Some notes were too long for a link and were cut off (marked …). Paste the rest below.\n";
      return DOXY_ISSUE_BASE + "?labels=doxy-notes&title=" + encodeURIComponent(title) + "&body=" + encodeURIComponent(out);
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

  function render(dateStr, day) {
    var lit = day.liturgical && day.liturgical.status === "ok" ? day.liturgical : null;

    var html = "";
    html += renderMiniFastingCalendar(dateStr);
    html += renderNavBar(dateStr);

    if (dateStr !== index.today) {
      html += '<button class="today-btn" id="todayBtn">Back to today</button>';
    }

    html += renderStalenessBanners();
    html += renderFeastSection(lit);
    html += renderSaintsSection(lit);
    html += renderStoriesSection(lit);
    html += renderServicesLink(lit, dateStr);
    html += '<div id="catechismSlot"></div>';
    html += renderParishSection(day);
    html += renderReadingSection(lit);
    html += renderFooter();

    if (!lit) {
      html =
        renderMiniFastingCalendar(dateStr) +
        renderNavBar(dateStr) +
        '<div class="banner">Couldn&rsquo;t reach the liturgical calendar for this day.</div>' +
        '<div id="catechismSlot"></div>' +
        renderParishSection(day) +
        renderFooter();
    }

    app.innerHTML = html;
    currentDate = dateStr;
    wireNavBar(dateStr);
    if (window.Catechism) {
      window.Catechism.mount(document.getElementById("catechismSlot"), { dateStr: dateStr, today: index.today, events: day.events });
    }
  }

  /* Collapsible sections. Several can be open at once. window.BriefSections
     lets other code (catechism.js, jumps to a note or search hit) open one. */
  function setSectionOpen(sec, open) {
    if (!sec) return;
    var bar = sec.querySelector(".cs-bar"), body = sec.querySelector(".cs-body");
    sec.classList.toggle("open", open);
    bar.setAttribute("aria-expanded", open ? "true" : "false");
    body.hidden = !open;
    sec.dispatchEvent(new CustomEvent("cs-toggle", { detail: { open: open }, bubbles: true }));
  }
  function ensureSectionOpen(el) {
    var sec = el && el.closest ? el.closest(".cs") : null;
    if (sec && !sec.classList.contains("open")) setSectionOpen(sec, true);
    var skip = el && el.closest ? el.closest(".parish-skip.collapsed") : null;
    if (skip) setSkipOpen(skip, true);
  }
  function setSkipOpen(skip, open) {
    skip.classList.toggle("collapsed", !open);
    var b = skip.querySelector(".parish-skip-toggle");
    b.setAttribute("aria-expanded", open ? "true" : "false");
    b.querySelector(".parish-skip-show").textContent = open ? "Hide text" : "Show text";
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".parish-skip-toggle");
    if (b) setSkipOpen(b.closest(".parish-skip"), b.getAttribute("aria-expanded") !== "true");
  });
  window.BriefSections = { render: renderCollapsible, open: function (id) { setSectionOpen(document.querySelector('.cs[data-cs="' + id + '"]'), true); }, ensureOpen: ensureSectionOpen, setOpen: setSectionOpen };
  document.addEventListener("click", function (e) {
    var bar = e.target.closest && e.target.closest(".cs-bar");
    if (!bar || bar.classList.contains("cs-link")) return;
    var sec = bar.closest(".cs");
    var opening = !sec.classList.contains("open");
    setSectionOpen(sec, opening);
    // When closing a section she had scrolled into, bring its bar back into view.
    if (!opening && sec.getBoundingClientRect().top < 0) sec.scrollIntoView({ block: "start" });
  });

  // "Read less" collapses a New Advent biography and brings its start back into view.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".na-less");
    if (!btn) return;
    var bio = btn.closest("details.na-bio");
    if (!bio) return;
    e.preventDefault();
    bio.open = false;
    var top = bio.getBoundingClientRect().top;
    if (top < 0 || top > window.innerHeight * 0.6) bio.scrollIntoView({ block: "start" });
  });

  /* ---- Service Texts page (services.html) ------------------------------ */
  function briefHref(dateStr) { return "index.html?d=" + dateStr; }
  function briefOpener() {
    try {
      var o = window.opener;
      if (!o || o.closed || o.location.origin !== location.origin) return null;
      var path = o.location.pathname, base = location.pathname.replace(/[^\/]*$/, "");
      return (path === base || path === base + "index.html") ? o : null;
    } catch (e) { return null; }
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("#backToBrief");
    if (!a) return;
    var o = briefOpener();
    if (!o) return; // plain link to the brief
    e.preventDefault();
    try { o.focus(); } catch (err) {}
    window.close();
    setTimeout(function () { if (!window.closed) location.href = a.href; }, 300);
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest(".legacy-notes-delete");
    if (!b) return;
    if (!window.confirm("Delete these earlier Liturgy Notes? This can't be undone.")) return;
    localStorage.removeItem("liturgyNotes:" + currentDate);
    var card = b.closest(".legacy-notes");
    if (card) card.remove();
  });

  function renderServicesPage(dateStr, day) {
    var lit = day.liturgical && day.liturgical.status === "ok" ? day.liturgical : null;
    var fd = friendlyDate(dateStr);
    document.title = "Service Texts – " + fd.weekday + ", " + fd.date;
    var html = '<div class="back-bar"><a class="back-link" id="backToBrief" href="' + esc(briefHref(dateStr)) + '">&larr; Back to brief</a></div>' +
      '<header class="svc-page-head"><h1>Service Texts</h1><p>' + esc(fd.weekday + ", " + fd.date) + "</p></header>";
    var body = lit ? renderServicesPageBody(lit, dateStr) : "";
    if (!body || !orderedServiceTexts(lit).length) {
      html += '<div class="banner">No service texts for this day.</div>';
    } else {
      html += '<p class="notes-hint">Tip: press and hold any line (or tap &ldquo;&#xFF0B; Note&rdquo;) to add a note there. Send to Doxy sends every note from every text for this day.</p>' + body;
    }
    var legacy = legacyLiturgyNotes(dateStr);
    if (legacy) {
      html += '<section class="card legacy-notes"><h2>Earlier Liturgy Notes</h2>' +
        '<p class="legacy-notes-hint">From the old notes box. Read-only; they&rsquo;re included when you Send to Doxy.</p>' +
        '<div class="legacy-notes-text">' + esc(legacy) + "</div>" +
        '<button type="button" class="legacy-notes-delete">Delete these notes</button></section>';
    }
    html += '<div class="svc-page-foot"><span class="anchored-summary"></span>' +
      '<button type="button" class="notes-doxy">Send to Doxy &#8599;</button></div>';
    app.innerHTML = html;
    currentDate = dateStr;
    wireTextChoosers();
    wireCheckableBodies();
    wireTextSearch();
    wireJumpButtons();
    wireAnchoredNotes(dateStr);
    // Open the text she last had open for this day, else the first with text.
    var choiceKey = "serviceTextChoice:" + dateStr;
    var chips = [].slice.call(document.querySelectorAll(".text-chip"));
    var saved = localStorage.getItem(choiceKey);
    var pick = chips.filter(function (c) { return c.textContent === saved; })[0] ||
      chips.filter(function (c) { return document.querySelector('.service-text-panel[data-index="' + c.getAttribute("data-index") + '"] .checkable-body'); })[0] || chips[0];
    document.addEventListener("click", function (e) {
      var c = e.target.closest && e.target.closest(".text-chip");
      if (c) localStorage.setItem(choiceKey, c.textContent);
    });
    if (pick) { pick.click(); window.scrollTo(0, 0); }
  }

  function goTo(dateStr) {
    if (index.available_dates.indexOf(dateStr) === -1) return;
    app.innerHTML = '<div class="empty-state">Loading&hellip;</div>';
    loadDay(dateStr)
      .then(function (day) { render(dateStr, day); })
      .catch(function () {
        app.innerHTML = renderNavBar(dateStr) + '<div class="banner">Couldn&rsquo;t load this day. Pull to refresh or try again later.</div>';
        currentDate = dateStr;
        wireNavBar(dateStr);
      });
  }

  var fastingReady = window.Fasting
    ? window.Fasting.load().then(function (d) { fastingData = d; }).catch(function () { fastingData = null; })
    : Promise.resolve();

  // Optional ?d=YYYY-MM-DD opens the brief (or the services page) on that day.
  var startDate = null;
  var ready = PAGE === "services" ? [loadIndex(), loadParish()] : [loadIndex(), fastingReady, loadParish()];
  Promise.all(ready)
    .then(function () {
      var q = new URLSearchParams(location.search).get("d") || "";
      startDate = (/^\d{4}-\d{2}-\d{2}$/.test(q) && index.available_dates.indexOf(q) !== -1) ? q : index.today;
      return loadDay(startDate);
    })
    .then(function (day) { (PAGE === "services" ? renderServicesPage : render)(startDate, day); })
    .catch(function (err) {
      app.innerHTML = '<div class="empty-state">Couldn&rsquo;t load the brief.<br><span style="font-size:0.8rem">' + esc(err.message) + "</span></div>";
    });
})();
