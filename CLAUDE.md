# Daily Orthodox Brief — Project Brief

A personal daily-brief web app for Aimee, delivered as an icon on her iPhone home
screen. It pulls the day's liturgical information from the Antiochian Archdiocese
and parish news from Saints Constantine & Helen (Carrollton, TX), and presents
them on a single screen she can check each morning.

This document is the handoff from a planning conversation in Claude chat. It
captures everything decided so far so the build can resume without re-explaining.

---

## Status: pre-build

Nothing has been built yet. A throwaway discovery script (`discover.py`) was
written to probe the data sources, because the chat sandbox could not reach
either domain. **If you are Claude Code running on Aimee's machine, you have
normal network access — fetch the sources directly and skip the GitHub Actions
discovery detour entirely.**

Next action: inspect both sources, resolve the unknowns in the Data Sources
section below, then build.

---

## Content specification

Aimee's requirements, as given. Everything is conditional — show it if the day
has it.

### Liturgical (from antiochian.org, or an equivalent structured source)

- Liturgy link
- Feast for the day
- Reading for the day
- Fasting for the day, plus a quick link to the full-year fasting calendar
- Vespers link
- Saints commemorated today, with a link to read more about them

### Parish (from constantinehelen.com)

- Link to the weekly bulletin
- Any events happening that day, plus a link to the parish calendar for
  future events

### Navigation

- Toggle forward and backward through about **six months each way**
  (changed from the original 7-days-back / 90-ahead at Aimee's request)
- Tap the date heading for a Year / Month / Day dropdown picker limited to
  the days that exist in `data/days/`
- Day view (one day at a time), not a list or month grid

---

## Architecture

**Rolling pre-fetched window, static hosting, no server.**

A scheduled job runs once daily and maintains a window of `today−183` through
`today+183`. Only missing days are fetched (nearest to today first, at most
`MAX_LITURGICAL_FETCHES_PER_RUN` per run, with a pause between antiochian.org
page loads), so a fresh backfill spreads over several runs; `status.window_complete`
stays false until it's done, which keeps the 2-hourly health-check run going.
Days that haven't been fetched yet simply don't exist (the UI skips them). Each day is written as its own small JSON file, plus a lightweight
index. The page loads today instantly and fetches other days on demand when the
user toggles.

```
repo/
├── fetch.py                  # daily job: builds the data files
├── data/
│   ├── index.json            # available dates + today pointer + last-run stamp
│   └── days/2026-09-09.json  # one file per day in the window
├── index.html                # the app (single page)
├── manifest.webmanifest      # makes "Add to Home Screen" behave like an app
├── icons/
└── .github/workflows/daily.yml
```

### Why this shape

- **The backward limit is free.** The window slides and old files are
  pruned, so there is nothing to enforce in the UI — old days simply don't exist.
- **Resilient.** If a source is down on a given morning, yesterday's cached data
  is still on disk and the app still works.
- **Fast on mobile.** Per-day files keep the initial load tiny. A single blob
  containing 98 days of reading text would be multiple megabytes.
- **No CORS dependency.** The build fetches server-side; the phone only ever
  reads static JSON from the same origin.
- **Free.** GitHub Actions cron + static hosting. No server to maintain.

### Staleness handling — do not skip this

A scraper that returns zero results does **not** fail the job, so GitHub's
failure emails won't catch it. Required:

1. Write a `last_successful_run` timestamp into `index.json` and display it in
   the UI footer.
2. If a source returns nothing where content was expected, surface a visible
   "couldn't reach the parish site" state rather than rendering an empty section
   that looks like a quiet day.
3. Never overwrite good cached data with an empty result. Keep the prior file.

---

## Data sources

### orthocal.info — likely primary for liturgical data

Strongly preferred over scraping. It publishes an RSS feed and a JSON API of
daily readings intended for embedding elsewhere. The default is Slavic tradition
on the Gregorian calendar, but a Greek tradition option covering the Antiochian
and Greek archdioceses exists — noted as **beta** by the maintainers.

- Docs: https://orthocal.info/feeds/
- Expected API shape: `https://orthocal.info/api/greek/gregorian/YYYY/M/D/`

**Unverified — confirm before writing parsers:**
- Exact endpoint path and whether the `greek` variant is live
- Actual JSON field names (readings, saints, feasts, fast level, titles)
- How far into the future arbitrary dates resolve
- Whether the Greek/Antiochian beta is accurate enough to trust, or whether
  antiochian.org must be the authority with orthocal as a fallback

### antiochian.org

The daily lectionary lives at `https://www.antiochian.org/epistleliturgicday/`.

**Unverified:**
- Whether the daily content is server-rendered or injected by JavaScript
  (determines whether plain HTTP fetching works or a headless browser is needed)
- Whether the site exposes a date parameter, or only ever serves "today" —
  this directly determines whether forward-toggling is possible from this source
- Where the annual fasting calendar PDF lives and whether its URL is
  year-predictable or must be hardcoded and updated each September

### Fasting calendar (data/fasting.json, fasting.html)

Built daily by `build_fasting_calendar()` in `fetch.py` for this year and next.
Per-day precedence: antiochian.org daily page (in the 98-day window) >
the Archdiocese's annual color-coded PDF
(`antiochianprodsa.blob.core.windows.net/liturgicalinstructions/{year} Fasting Calendar.pdf`,
cell colors mapped through the legend printed on the PDF) > orthocal.info Greek
API, used only for a year whose PDF isn't published yet and flagged
`provisional`. The next year's PDF has appeared in late November. Season names
(Great Lent, Apostles' Fast, fast-free weeks...) are computed from the
Paschalion purely as labels; fast levels always come from a source.
`index.html` shows a mini month calendar at the top; tapping opens
`fasting.html` in a new tab. Shared code: `fasting.js`, `fasting.css`.

### newadvent.org (Catholic Encyclopedia biographies)

`NewAdventMatcher` in `fetch.py` attaches short biographies to a day's
commemorated saints (`liturgical.newadvent`), shown under the Synaxarion.
It only uses CE entries titled as saints, requires the name *and* every
distinguishing word (place, epithet) to agree in both directions, ignores
"X of Place" bynames of other people in the article, skips "New Martyr" /
"father of ..." commemorations and joint articles, and attaches nothing when
two candidates tie. Prefer no match over a wrong one. The index and fetched
articles are cached in `data/cache/newadvent.json`; bump
`NEWADVENT_MATCH_VERSION` to re-match every day after changing the rules.

### Collapsible sections

- Synaxarion (Saints Commemorated + New Advent bios), Service Texts, Catechism and Daily Bible Readings render via `renderCollapsible()` in index.html as thin bars (title, one-line summary, chevron), collapsed by default. Several can be open at once; open/closed state isn't remembered.
- An open section's body is the one scroll box: `max-height: calc(100dvh - 80px)` (vh fallback), `overflow-y: auto`, vertical scroll chaining left on (a swipe past the end carries on scrolling the page), white scrollbar like the old service-text panel. The service-text panel no longer scrolls on its own inside it. The bar is sticky, and the service-text search header and catechism lesson picker stick to the top of the scroll box.
- `window.BriefSections` (`render`, `open`, `setOpen`, `ensureOpen`) lets other code open a section: search hits, "Resume where I left off", and the catechism review reminder auto-expand a collapsed section. Opening and closing fire a bubbling `cs-toggle` event; catechism.js uses it to load lessons lazily.

### Catechism class (data/catechism/lessons.json, catechism.js, catechism.css)

- Lesson text comes from the parish's Adult Catechism handout ("Adult Catechism – All Lessons (complete) – rev 05", 80 pp, 14 lessons). `tools/extract_catechism.py` turns the PDF into `data/catechism/lessons.json`: per lesson `id`, `title`, `synopsis`, `blocks` (`heading` / `para` / `bullet` with `level` 1–3) and `footnotes` (`[^n]` refs inline). Page numbers and PDF artifacts are dropped and line wraps re-joined heuristically. It's static: re-run the script only when the parish issues a new revision. `fetch.py` doesn't touch it.
- Class detection is client-side: a day's parish events (`events[].title`) matching /catechism|catechumen|inquirer/i, excluding titles starting with "No" or containing "cancel"/"postpon" (the parish posts "No Catechism Class" on skipped Saturdays). On those days the collapsible bar is titled "Catechism Class" (highlighted, with the class time). Every other day it reads "Catechism lessons & class notes". A reminder button above it appears if there are notes from a class in the past 6 days.
- Lesson choice: remembered per class date (`catechismLesson:<date>`) and overall (`catechismLastLesson`). A later class day suggests the next lesson.
- Notes: localStorage `catechismNotes` (one list: lesson, classDate, para index, quote, section, text). Same press-and-hold / "＋ Note" interaction and note sheet as the service-text notes, but separate storage. "Notes from class" lists them by class date and lesson.
- "Send to Doxy" opens a prefilled GitHub new-issue URL (label `doxy-catechism`, title "Catechism notes – Lesson NN – YYYY-MM-DD", notes in lesson order with anchor quotes, URL capped at 7,500 chars with truncation notice). No token in the site.

### constantinehelen.com

Entirely uninspected. Nothing is known about the platform, the calendar
mechanism, or the bulletin format.

**Determine first:**
- Platform (Squarespace / Wix / WordPress / a church CMS) — some expose clean
  data endpoints that remove the need to scrape
- Whether events come from an embedded Google Calendar, JSON-LD structured
  data, an iCal feed, or rendered HTML. An iCal or Google Calendar feed would be
  by far the best outcome and should be looked for before writing any scraper.
- Whether the weekly bulletin is a stable URL or a new PDF link each week

---

## Open questions for Aimee

Do not guess these — ask.

1. **Liturgy and Vespers links.** Does she want the *service texts* for that day
   (published by the archdiocese), or her *parish's* service times and livestream
   links? Different sources, different work.

2. **Empty-day behavior.** On an ordinary weekday with no feast, no parish event,
   and no fast, should those sections hide, or show greyed-out with "nothing
   today"? Recommendation offered in chat: hide the liturgical sections, but
   always show an explicit "no events today" for the *parish* sections, since
   those are the most likely to break silently.

3. **Repo visibility and host.** Public repo + GitHub Pages is free and simplest.
   Private repo requires a paid plan for Pages, so a private build would deploy
   to Cloudflare Pages instead — same effort, different target. Nothing here is
   sensitive, but it is her personal spiritual routine, so it is her call.

---

## Design direction

Aimee's stated aesthetic preference is clean and minimal. This is a screen read
once each morning, often one-handed, sometimes before coffee.

- Single column, generous type, no chrome
- Today is the default view; date toggling is secondary and should not compete
- The reading is the longest content block — it needs real reading typography,
  not UI text
- Must work as a fullscreen home-screen app (`display: standalone` in the
  manifest, proper apple-touch-icon, respect safe-area insets)
- Dark mode via `prefers-color-scheme` — this gets opened early in the morning
- No build step. Plain HTML, CSS, and vanilla JS. Nothing here justifies a
  framework, and a build step is one more thing to break in a year.

---

## Build sequence

1. **Inspect** both sources directly. Resolve every "unverified" item above.
   Report findings before writing parsers.
2. **Build `fetch.py`** — one function per source, each returning a normalized
   dict, each failing soft and independently. One source being down must not
   take down the whole run.
3. **Run it locally**, inspect the generated JSON for a real day, and check a
   feast day and a fast day specifically — not just a plain Tuesday.
4. **Build `index.html`** against the real data.
5. **Preview on her actual phone** before wiring the cron.
6. **Add the daily workflow** — cron in UTC; pick a time that lands early
   morning US Central, accounting for the fact that GitHub cron does not observe
   daylight saving.
7. **Deploy**, add to home screen, verify the icon and fullscreen behavior.

---

## Constraints and notes

- **Timezone.** Everything is US Central. "Today" must be computed in Central,
  not UTC, or the app flips to tomorrow at 7pm.
- **GitHub Actions cron is not punctual.** Scheduled runs are queued and can be
  delayed by 15+ minutes under load. Do not schedule for 6:00am and expect 6:00am.
- **Be a polite client.** One request per source per day is trivial load, but set
  a real User-Agent and don't hammer during development — cache responses to disk
  while iterating on parsers.
- **Scrapers rot.** Anything scraped rather than pulled from a documented feed
  will break eventually. Prefer feeds and APIs everywhere they exist, and keep
  each parser small and isolated so a break is a five-minute fix.
