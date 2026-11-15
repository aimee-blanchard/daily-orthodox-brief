/* Shared fasting-calendar helpers for index.html (mini calendar) and
 * fasting.html (full calendar). Plain browser JS, no build step.
 * Data comes from data/fasting.json, written daily by fetch.py. */
(function (global) {
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  var LEVEL_ORDER = ["strict", "wine", "wine_oil", "fish", "dairy"];
  var SHORT_LABELS = {
    strict: "Strict",
    wine: "Wine",
    wine_oil: "Wine & oil",
    fish: "Fish",
    dairy: "Dairy",
  };

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(y, m, d) { return y + "-" + pad(m) + "-" + pad(d); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* Today's date in US Central, regardless of the device's own time zone. */
  function todayCentral() {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date());
    var get = function (t) { return parts.filter(function (p) { return p.type === t; })[0].value; };
    return get("year") + "-" + get("month") + "-" + get("day");
  }

  function load() {
    return fetch("data/fasting.json", { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function dayInfo(data, dateStr) {
    var entry = data.days[dateStr] || null;
    var year = (data.years || {})[dateStr.slice(0, 4)] || null;
    var seasons = (data.seasons || []).filter(function (s) { return s.start <= dateStr && dateStr <= s.end; });
    var season = seasons.filter(function (s) { return s.kind === "season"; })[0] || null;
    var fastFree = seasons.filter(function (s) { return s.kind === "fast_free"; })[0] || null;
    return {
      date: dateStr,
      covered: !!year,
      provisional: !!(year && year.provisional),
      level: entry ? entry.level : null,
      levelInfo: entry ? data.levels[entry.level] : null,
      source: entry ? entry.source : year ? year.source : null,
      season: season,
      fastFree: fastFree,
    };
  }

  /* Returns the HTML for one month grid. opts: {today, selected, compact} */
  function monthGrid(data, year, month, opts) {
    opts = opts || {};
    var first = new Date(Date.UTC(year, month - 1, 1)).getUTCDay(); // 0 = Sunday
    var nDays = new Date(Date.UTC(year, month, 0)).getUTCDate();
    var html = '<div class="fc-grid' + (opts.compact ? " fc-compact" : "") + '" role="grid">';
    ["S", "M", "T", "W", "T", "F", "S"].forEach(function (d) {
      html += '<div class="fc-dow" aria-hidden="true">' + d + "</div>";
    });
    for (var i = 0; i < first; i++) html += '<div class="fc-cell fc-empty"></div>';
    for (var d = 1; d <= nDays; d++) {
      var ds = iso(year, month, d);
      var info = dayInfo(data, ds);
      var cls = ["fc-cell"];
      if (info.level) cls.push("fc-" + info.level);
      else if (info.fastFree) cls.push("fc-free");
      if (info.provisional) cls.push("fc-provisional");
      if (!info.covered) cls.push("fc-nodata");
      if (ds === opts.today) cls.push("fc-today");
      if (ds === opts.selected && ds !== opts.today) cls.push("fc-selected");
      var label = ds + ": " + (info.levelInfo ? info.levelInfo.label : info.fastFree ? "Fast-free" : info.covered ? "No fast" : "No data");
      html += '<div class="' + cls.join(" ") + '" data-date="' + ds + '" title="' + esc(label) + '" aria-label="' + esc(label) + '">' +
        '<span class="fc-num">' + d + "</span></div>";
    }
    html += "</div>";
    return html;
  }

  function legend(data, levels, opts) {
    opts = opts || {};
    var html = '<div class="fc-legend">';
    levels.forEach(function (k) {
      var info = data.levels[k];
      html += '<span class="fc-legend-item"><span class="fc-swatch fc-' + k + '"></span>' +
        esc(opts.long ? info.label : SHORT_LABELS[k]) + "</span>";
    });
    if (opts.free) html += '<span class="fc-legend-item"><span class="fc-swatch fc-free"></span>Fast-free</span>';
    if (opts.today) html += '<span class="fc-legend-item"><span class="fc-swatch fc-swatch-today"></span>Today</span>';
    return html + "</div>";
  }

  function levelsInMonth(data, year, month) {
    var prefix = year + "-" + pad(month) + "-";
    var seen = {};
    Object.keys(data.days).forEach(function (k) { if (k.indexOf(prefix) === 0) seen[data.days[k].level] = true; });
    return LEVEL_ORDER.filter(function (k) { return seen[k]; });
  }

  function hasFastFree(data, year, month) {
    var start = iso(year, month, 1), end = iso(year, month, 31);
    return (data.seasons || []).some(function (s) { return s.kind === "fast_free" && s.start <= end && s.end >= start; });
  }

  global.Fasting = {
    MONTHS: MONTHS,
    LEVEL_ORDER: LEVEL_ORDER,
    pad: pad,
    iso: iso,
    esc: esc,
    todayCentral: todayCentral,
    load: load,
    dayInfo: dayInfo,
    monthGrid: monthGrid,
    legend: legend,
    levelsInMonth: levelsInMonth,
    hasFastFree: hasFastFree,
  };
})(window);
