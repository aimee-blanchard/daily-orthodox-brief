#!/usr/bin/env python3
"""Daily job: builds data/index.json and data/days/*.json for the brief.

Each source fails independently and soft. If a fetch fails, we fall back to
the last good cached value rather than overwriting it with an empty result.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber
from striprtf.striprtf import rtf_to_text
from playwright.sync_api import sync_playwright

CENTRAL = ZoneInfo("America/Chicago")
# Keep roughly six months of days on either side of today. A fresh window is
# filled incrementally: each run fetches at most MAX_LITURGICAL_FETCHES_PER_RUN
# missing days (nearest to today first), so a full backfill is spread over a
# few runs instead of blowing through the Action's time limit. Once filled,
# a normal day only needs to fetch the one new day at the far end.
WINDOW_BACK_DAYS = 183
WINDOW_FORWARD_DAYS = 183
MAX_LITURGICAL_FETCHES_PER_RUN = int(os.environ.get("MAX_LITURGICAL_FETCHES_PER_RUN", "60"))
# Pause between antiochian.org page loads so a backfill is a polite trickle.
ANTIOCHIAN_DELAY_SECONDS = 1.5
# A day can legitimately have no service texts (an ordinary weekday), or it can
# have none, or have some with missing text, because a transient scrape glitch
# found no links (or failed to fetch a link's RTF text) that one time. We can't
# tell those apart, so keep retrying incomplete days while they're within this
# many days of today, instead of trusting the first result forever.
SERVICE_TEXT_RETRY_DAYS = 21
USER_AGENT = "DailyOrthodoxBrief/1.0 (personal use; contact aimee)"
REQUEST_TIMEOUT = 15

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DAYS_DIR = DATA_DIR / "days"
LITURGY_OUTLINE_PATH = BASE_DIR / "liturgy_outline.txt"

ORTHOCAL_URL = "https://orthocal.info/api/greek/gregorian/{year}/{month}/{day}/"
ANTIOCHIAN_BASE = "https://www.antiochian.org"
PARISH_EVENTS_URL = "https://www.constantinehelen.com/wp-json/tribe/events/v1/events"
PARISH_MEDIA_URL = "https://www.constantinehelen.com/wp-json/wp/v2/media"
PARISH_HOME_URL = "https://www.constantinehelen.com/"

STATIC_LINKS = {
    "parish_calendar": "https://www.constantinehelen.com/calendar/",
}


def today_central() -> date:
    return datetime.now(CENTRAL).date()


# ---------------------------------------------------------------------------
# The Twelve Great Feasts + Pascha
#
# antiochian.org's page doesn't flag which commemorations are Great Feasts,
# so we identify them by matching known feast names against the actual
# scraped title/commemoration text for that day (whichever source produced
# it), rather than computing dates independently.
# ---------------------------------------------------------------------------

# Each entry: (canonical name, is_pascha, [word-groups; any group matching is a hit]).
# Word groups include known naming variants across Orthodox jurisdictions/sources
# (e.g. antiochian.org says "Elevation" where others say "Exaltation").
GREAT_FEAST_PATTERNS = [
    ("Pascha", True, [["pascha"], ["resurrection", "christ"]]),
    ("Entry of the Lord into Jerusalem (Palm Sunday)", False, [["palm", "sunday"], ["entry", "jerusalem"]]),
    ("Ascension", False, [["ascension"]]),
    ("Pentecost", False, [["pentecost"]]),
    ("Theophany", False, [["theophany"], ["epiphany"], ["baptism", "christ"]]),
    ("Nativity of the Theotokos", False, [["nativity", "theotokos"], ["birth", "theotokos"]]),
    ("Exaltation of the Holy Cross", False, [["exaltation", "cross"], ["elevation", "cross"]]),
    ("Presentation of the Theotokos in the Temple", False, [
        ["entrance", "theotokos", "temple"],
        ["presentation", "theotokos", "temple"],
    ]),
    ("Nativity of Christ", False, [["nativity", "christ"], ["birth", "christ"], ["christmas"]]),
    ("Presentation of Christ in the Temple", False, [["presentation", "temple"], ["meeting", "lord"]]),
    ("Annunciation", False, [["annunciation"]]),
    ("Transfiguration", False, [["transfiguration"]]),
    ("Dormition of the Theotokos", False, [["dormition"], ["falling", "asleep", "theotokos"]]),
]


FEAST_PERIOD_REFERENCE_PATTERN = re.compile(
    r"(fore|after)feast of [^,;]*|leave-?taking of [^,;]*|apodosis of [^,;]*",
    re.IGNORECASE,
)
# "Nth Sunday/week/etc. after Pentecost" is the routine way antiochian.org labels
# ordinary weeks, not a reference to the Feast of Pentecost itself.
ORDINARY_TIME_PATTERN = re.compile(r"after pentecost", re.IGNORECASE)


def identify_great_feast(text_blob: str) -> dict | None:
    # Strip "afterfeast of X" / "forefeast of X" / "leave-taking of X" / "apodosis of X"
    # clauses first, so a day within a feast's surrounding period isn't mistaken for
    # the feast itself, and strip routine "N weeks after Pentecost" week-numbering.
    text = FEAST_PERIOD_REFERENCE_PATTERN.sub("", text_blob or "")
    text = ORDINARY_TIME_PATTERN.sub("", text).lower()
    for name, is_pascha, groups in GREAT_FEAST_PATTERNS:
        if name == "Presentation of Christ in the Temple" and "theotokos" in text:
            continue  # avoid clashing with the Theotokos presentation/entrance feast
        if any(all(word in text for word in group) for group in groups):
            return {"name": name, "is_pascha": is_pascha}
    return None


def date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read()


def http_get_json(url: str):
    return json.loads(http_get(url))


def fetch_rtf_text(url: str) -> str:
    raw = http_get(url).decode("cp1252", errors="replace")
    text = rtf_to_text(raw).strip()
    if not text:
        raise ValueError("RTF converted to empty text")
    return text


# ---------------------------------------------------------------------------
# Source: antiochian.org (primary liturgical data, via headless browser)
#
# The site is a client-rendered Angular app, so plain HTTP fetching returns an
# empty shell. Each day's page is addressable by a sequential integer ID
# (confirmed: id N = id of today +/- N days), which lets us navigate directly
# instead of driving the on-page date picker.
# ---------------------------------------------------------------------------

DATE_HEADING_PATTERN = re.compile(r"([A-Z]+),\s*([A-Z]+)\s+(\d+),\s*(\d{4})")
MONTH_NAMES = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
               "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]

TITLECASE_LOWERCASE_WORDS = {"of", "and", "the", "in", "on", "for", "to", "a", "an", "at", "by", "with"}


def titlecase(text: str) -> str:
    """antiochian.org stores these strings in ALL CAPS; render them more readably."""
    words = text.title().split(" ")
    fixed = []
    for i, w in enumerate(words):
        w = re.sub(r"'([A-Z])", lambda m: "'" + m.group(1).lower(), w)
        lw = w.lower()
        fixed.append(lw if i != 0 and lw in TITLECASE_LOWERCASE_WORDS else w)
    return " ".join(fixed)


def parse_date_heading(body_text: str) -> date:
    m = DATE_HEADING_PATTERN.search(body_text)
    if not m:
        raise ValueError("date heading not found on antiochian.org page")
    month = MONTH_NAMES.index(m.group(2)) + 1
    return date(int(m.group(4)), month, int(m.group(3)))


def antiochian_find_today_id(page) -> tuple:
    """Returns (id, date) for whatever antiochian.org currently reports as
    "today". Callers should anchor their own date-to-id offsets to the
    returned date rather than assume it matches today_central() -- a
    long-running batch (e.g. a full backfill) can straddle midnight between
    when today_central() was computed and when this actually runs."""
    page.goto(f"{ANTIOCHIAN_BASE}/liturgicday", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1200)
    href = page.eval_on_selector("a[href*='/epistleliturgicday/']", "el => el.getAttribute('href')")
    m = re.search(r"/epistleliturgicday/(\d+)", href or "")
    if not m:
        raise ValueError("could not find today's antiochian.org id")
    found_date = parse_date_heading(page.inner_text("body"))
    return int(m.group(1)), found_date


def antiochian_parse_liturgicday(page, id_: int, expected_date: date) -> dict:
    page.goto(f"{ANTIOCHIAN_BASE}/liturgicday/{id_}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1200)

    body_text = page.inner_text("body")
    found_date = parse_date_heading(body_text)
    if found_date != expected_date:
        raise ValueError(f"antiochian.org id {id_} resolved to {found_date}, expected {expected_date}")

    items = page.eval_on_selector_all(".liturgicalDayListItem", """els => els.map(el => ({
        hasLink: !!el.querySelector('a'),
        title: (el.querySelector('.liturgicalSubItemTitle') || {}).innerText || null,
        desc: (el.querySelector('.liturgicalSubItemDesc') || {}).innerText || null,
        iconSrc: (el.querySelector('img') || {}).src || ""
    }))""")

    feast_item = next((it for it in items if not it["hasLink"] and "feast" in it["iconSrc"].lower()), None)
    fasting_item = next((it for it in items if not it["hasLink"] and "fasting" in it["iconSrc"].lower()), None)

    if not feast_item or not feast_item["title"]:
        raise ValueError(f"antiochian.org id {id_}: feast title not found")

    fasting = None
    if fasting_item and fasting_item["title"]:
        text = fasting_item["title"].strip()
        if text.upper() != "NO FAST":
            abstentions = [a.strip().lower() for a in re.sub(r"(?i)^abstain from\s*", "", text).split(",") if a.strip()]
            fasting = {"description": titlecase(text), "abstentions": abstentions}

    service_links_raw = page.eval_on_selector_all(
        "a.dailyLiturgicalTextUrl",
        "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))",
    )
    by_label = {}
    for link in service_links_raw:
        if "bilingual" in link["text"].lower():
            continue
        if link["text"].endswith("(PDF)"):
            by_label.setdefault(link["text"][:-len("(PDF)")].strip(), {})["pdf_url"] = link["href"]
        elif link["text"].endswith("(RTF)"):
            by_label.setdefault(link["text"][:-len("(RTF)")].strip(), {})["rtf_url"] = link["href"]

    service_texts = []
    for label, urls in by_label.items():
        entry = {"label": label, "pdf_url": urls.get("pdf_url"), "rtf_url": urls.get("rtf_url")}
        if urls.get("rtf_url"):
            try:
                entry["text"] = fetch_rtf_text(urls["rtf_url"])
            except Exception as exc:
                print(f"[warn] RTF fetch failed for {label!r} ({urls['rtf_url']}): {exc}", file=sys.stderr)
        service_texts.append(entry)

    commemorations = [titlecase(c.strip()) for c in (feast_item["desc"] or "").split(",") if c.strip()]

    return {
        "title": titlecase(feast_item["title"].strip()),
        "commemorations": commemorations,
        "fasting": fasting,
        "service_texts": service_texts,
        "synaxarion": extract_synaxarion(service_texts),
    }


# The Synaxarion (a short reading on the saints/feast of the day) is read at
# Orthros/Matins, so it only ever appears in that service text, never Vespers
# or the Divine Liturgy readings. Not every day has an Orthros text at all.
SYNAXARION_PATTERN = re.compile(r"THE SYNAXARION[^\n]*\n(.*?)(?=\n[A-Z][A-Z ,'-]{4,}\n|\Z)", re.DOTALL)


def extract_synaxarion(service_texts: list) -> str | None:
    for entry in service_texts:
        label = entry["label"].lower()
        if "orthros" in label and "bilingual" not in label and entry.get("text"):
            m = SYNAXARION_PATTERN.search(entry["text"])
            if m:
                text = m.group(1).strip()
                # Sanity cap: a genuine Synaxarion reading is a paragraph or two.
                # A much longer match means the boundary heading wasn't found
                # (e.g. an unexpected format) and it ran to the end of the file.
                if 0 < len(text) <= 4000:
                    return text
    return None


# Any service text (Vespers, Orthros, Divine Liturgy Variables, ...) may lay
# out one or more Scripture readings verbatim -- Old Testament paremias at a
# vigil, the Orthros/Matins Gospel, the Liturgy's Epistle and Gospel. Each is
# introduced by a citation sentence like "The reading from the holy gospel
# according to Saint John (12:28-36)" or "The Reading from the Book of
# Proverbs. (9:1-11)", sometimes followed by a block of sung/spoken dialogue
# that isn't part of the reading itself. "Bilingual" texts interleave Arabic
# and English per line and aren't worth parsing since the plain English
# version of the same text is always also present.
CITATION_PATTERN = re.compile(
    r"The\s+[Rr]eading\b[^\n(]*?(?:according to|is from|from)\s+"
    r"(?P<source>[^\n(]+?)\s*\n?\s*"
    r"\((?P<verses>[0-9][0-9:,;\-–\s]*)\)\.?",
    re.IGNORECASE,
)
LET_US_ATTEND_PATTERN = re.compile(r"Let us attend\.?\s*", re.IGNORECASE)
NEXT_READING_HEADING_PATTERN = re.compile(r"\n[A-Z][A-Z0-9 ,.:'’()&-]{4,}\n")
LEADING_SPEAKER_TAG_PATTERN = re.compile(r"^\s*(?:\*\*[^\n]*\*\*\s*\n+)?(?:Priest|Reader|Deacon):?\s*", re.IGNORECASE)
TRAILING_SPEAKER_TAG_PATTERN = re.compile(r"\s*(?:The\s+\w+\s+Reading\s*)?(?:Reader|Priest|Deacon):\s*$", re.IGNORECASE)

# Named, purposeful Psalm headings (e.g. Vespers' opening Psalm 103) are worth
# surfacing; the bare "PSALM 3" / "PSALM 37" section markers used for things
# like the Six Psalms at Orthros are not -- they're repetitive structural
# boilerplate, not a specific reading laid out for the day.
PSALM_INTRO_PATTERN = re.compile(r"(?:THE\s+)?PSALM\s+OF\s+INTRODUCTION\D{0,10}?([0-9]+)", re.IGNORECASE)

ORDINAL_WORDS = {"first": "1", "second": "2", "third": "3"}
BOOK_NAME_FILLER_PATTERN = re.compile(
    r"\b(the|book|prophecy|of|epistle|general|catholic|prophet|first|second|third|st\.?|paul|apostle)\b\.?",
    re.IGNORECASE,
)


def simplify_book_name(source_desc: str) -> str:
    """Turn a verbose citation description into a short book name, e.g.
    "the holy gospel according to Saint Mark" -> "Mark", or
    "the Third Book of Kings" -> "3 Kings"."""
    d = source_desc.strip().rstrip(".")
    m = re.search(r"gospel according to (?:saint |st\.?\s*)?([A-Za-z]+)", d, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    if re.search(r"acts of the apostles", d, re.IGNORECASE):
        return "Acts"
    ordinal = ""
    om = re.search(r"\b(first|second|third)\b", d, re.IGNORECASE)
    if om:
        ordinal = ORDINAL_WORDS[om.group(1).lower()]
    if "epistle" in d.lower():
        m = re.search(r"to (?:the )?([A-Za-z]+)$", d, re.IGNORECASE)
        if m:
            return f"{ordinal} {m.group(1)}".strip()
    remaining = BOOK_NAME_FILLER_PATTERN.sub("", d)
    remaining = re.sub(r"\s+", " ", remaining).strip()
    if ordinal:
        remaining = f"{ordinal} {remaining}".strip()
    return remaining or d


def verse_signature(s: str) -> str:
    """Identifies a reading by its starting chapter:verse, since antiochian.org
    and the RTF service texts format multi-chapter ranges completely
    differently (e.g. "1:21-24; 2:1-4" vs "1:21-2:4" for the same passage)."""
    m = re.search(r"[0-9]+:[0-9]+", s)
    return m.group() if m else re.sub(r"\s+", "", s)


def extract_citations(text: str) -> list:
    """Returns [{'source': str, 'verses': str, 'text': str}, ...] for every
    citation-introduced reading found in a service text's RTF-derived text,
    in document order, with no deduping."""
    results = []
    matches = list(CITATION_PATTERN.finditer(text))
    for i, m in enumerate(matches):
        window_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        after = text[m.end():window_end]

        attend_match = LET_US_ATTEND_PATTERN.search(after[:400])
        body = after[attend_match.end():] if attend_match else after
        body = LEADING_SPEAKER_TAG_PATTERN.sub("", body)

        heading_match = NEXT_READING_HEADING_PATTERN.search(body)
        passage = body[: heading_match.start()] if heading_match else body

        glory_match = re.search(r"\n?\s*Choir:[^\n]*Glory to thee", passage, re.IGNORECASE)
        if glory_match:
            passage = passage[: glory_match.start()]
        bullet_idx = passage.find("·")  # "·" marks admin/rubric notes, never Scripture
        if bullet_idx != -1:
            passage = passage[:bullet_idx]

        passage = re.sub(r"\s*\n\s*", " ", passage).strip()
        passage = TRAILING_SPEAKER_TAG_PATTERN.sub("", passage).strip()

        if len(passage) < 30:
            continue
        results.append({
            "source": m.group("source").strip(),
            "verses": m.group("verses").strip(),
            "text": passage,
        })
    return results


def extract_service_text_readings(service_texts: list, existing_readings: list) -> list:
    seen_signatures = {verse_signature(r["display"]) for r in existing_readings}
    results = []
    for entry in service_texts:
        if "bilingual" in entry["label"].lower() or not entry.get("text"):
            continue
        text = entry["text"]
        for citation in extract_citations(text):
            verses = citation["verses"]
            sig = verse_signature(verses)
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            results.append({
                "display": f"{simplify_book_name(citation['source'])} {verses}",
                "text": citation["text"],
                "source_label": entry["label"],
            })

        for m in PSALM_INTRO_PATTERN.finditer(text):
            sig = f"psalm{m.group(1)}"
            if sig in seen_signatures:
                continue
            body = LEADING_SPEAKER_TAG_PATTERN.sub("", text[m.end():])
            heading_match = NEXT_READING_HEADING_PATTERN.search(body)
            passage = body[: heading_match.start()] if heading_match else body
            passage = re.sub(r"\s*\n\s*", " ", passage).strip()
            if len(passage) < 30:
                continue
            seen_signatures.add(sig)
            results.append({
                "display": f"Psalm {m.group(1)}",
                "text": passage,
                "source_label": entry["label"],
            })
    return results


# ---------------------------------------------------------------------------
# Divine Liturgy outline (liturgy_outline.txt), filled in with the day's
# variable parts from "Divine Liturgy Variables". Anything not confidently
# found (parish-specific names, rare feast-only replacements) is left as a
# "see bulletin" note rather than guessed.
# ---------------------------------------------------------------------------

def find_heading_blocks(text: str, heading_pattern: str, ignorecase: bool = True) -> list:
    flags = re.IGNORECASE if ignorecase else 0
    blocks = []
    for m in re.finditer(heading_pattern, text, flags):
        after = text[m.end():]
        heading_match = NEXT_READING_HEADING_PATTERN.search(after)
        block = after[: heading_match.start()] if heading_match else after
        bullet_idx = block.find("·")  # "·" marks admin/rubric notes, not the hymn text
        if bullet_idx != -1:
            block = block[:bullet_idx]
        block = re.sub(r"\s*\n\s*", " ", block).strip()
        if block:
            blocks.append(block)
    return blocks


def build_divine_liturgy_text(service_texts: list, liturgical: dict) -> str | None:
    if not LITURGY_OUTLINE_PATH.exists():
        return None
    entry = next(
        (e for e in service_texts if "divine liturgy" in e["label"].lower() and "bilingual" not in e["label"].lower() and e.get("text")),
        None,
    )
    if not entry:
        return None
    text = entry["text"]
    fallback = "(See the parish bulletin for this.)"
    tokens = {}

    apolytikia = find_heading_blocks(text, r"[A-Z0-9 ,.'’-]*\bAPOLYTIKION\b[A-Z0-9 ,.'’-]*", ignorecase=False)
    tokens["HYMN_OF_DAY"] = "\n\n".join(apolytikia) if apolytikia else fallback
    tokens["TROPARIA"] = "\n\n".join(apolytikia) if apolytikia else fallback

    kontakia = find_heading_blocks(text, r"KONTAKION OF [^\n]+")
    tokens["KONTAKION"] = "\n\n".join(kontakia) if kontakia else fallback

    megalynaria = find_heading_blocks(text, r"MEGALYNARION OF [^\n]+")
    tokens["MEGALYNARION"] = "\n\n".join(megalynaria) if megalynaria else fallback

    koinonika = find_heading_blocks(text, r"KOINONIKON[^\n]*")
    tokens["COMMUNION_HYMN"] = "\n\n".join(koinonika) if koinonika else fallback

    thrice_holy = find_heading_blocks(text, r"REPLACEMENT FOR THE THRICE-HOLY[^\n]*")
    tokens["THRICE_HOLY_REPLACEMENT"] = "\n\n".join(thrice_holy) if thrice_holy else fallback

    true_light = find_heading_blocks(text, r"REPLACEMENT FOR[^\n]*TRUE LIGHT[^\n]*")
    tokens["TRUE_LIGHT_REPLACEMENT"] = "\n\n".join(true_light) if true_light else fallback

    tokens["NAMES"] = fallback
    tokens["ALLELUIA"] = fallback

    citation_matches = list(CITATION_PATTERN.finditer(text))
    epistle_heading = re.search(r"THE EPISTLE\s*\n", text, re.IGNORECASE)
    if epistle_heading and citation_matches:
        prokeimenon = text[epistle_heading.end():citation_matches[0].start()]
        prokeimenon = re.sub(r"(?i)^\s*\([^)]*\)\s*\n?", "", prokeimenon)  # drop "(For the Sunday...)" rubric
        prokeimenon = re.sub(r"\s*\n\s*", " ", prokeimenon).strip()
        tokens["PROKEIMENON"] = prokeimenon or fallback
    else:
        tokens["PROKEIMENON"] = fallback

    citations = extract_citations(text)
    tokens["EPISTLE"] = (
        f"{simplify_book_name(citations[0]['source'])} {citations[0]['verses']}\n\n{citations[0]['text']}"
        if len(citations) >= 1 else fallback
    )
    tokens["GOSPEL"] = (
        f"{simplify_book_name(citations[1]['source'])} {citations[1]['verses']}\n\n{citations[1]['text']}"
        if len(citations) >= 2 else fallback
    )

    saints = liturgical.get("feasts") or liturgical.get("saints") or []
    tokens["SAINT_OF_DAY"] = ", ".join(saints) if saints else fallback

    dismissal_heading = re.search(r"THE DISMISSAL\s*\n", text, re.IGNORECASE)
    if dismissal_heading:
        after = text[dismissal_heading.end():]
        stop_match = re.search(
            r"Priest:\s*Through the prayers of our holy fathers|Pronunciation Guide|Portions of the Archdiocesan",
            after, re.IGNORECASE,
        )
        block = after[:stop_match.start()] if stop_match else after
        tokens["DISMISSAL"] = re.sub(r"\s*\n\s*", " ", block).strip() or fallback
    else:
        tokens["DISMISSAL"] = fallback

    outline = LITURGY_OUTLINE_PATH.read_text()
    for key, value in tokens.items():
        outline = outline.replace("{{" + key + "}}", value)
    return outline


def antiochian_parse_readings(page, id_: int) -> list:
    page.goto(f"{ANTIOCHIAN_BASE}/epistleliturgicday/{id_}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1200)

    pairs = page.eval_on_selector_all(".dailyReadingSubTitle", "els => els.map(e => e.innerText.trim())")
    texts = page.eval_on_selector_all(".dailyReadingSubDesc", "els => els.map(e => e.innerText.trim())")
    return [
        {"display": titlecase(title), "text": text}
        for title, text in zip(pairs, texts)
        if title and text
    ]


def fetch_antiochian_day(page, id_: int, expected_date: date) -> dict:
    day_info = antiochian_parse_liturgicday(page, id_, expected_date)
    readings = antiochian_parse_readings(page, id_)
    if not readings:
        raise ValueError(f"antiochian.org id {id_}: no readings found")

    readings.extend(extract_service_text_readings(day_info["service_texts"], readings))

    service_texts = day_info["service_texts"]
    divine_liturgy_text = build_divine_liturgy_text(
        service_texts, {"feasts": [day_info["title"]], "saints": day_info["commemorations"]}
    )
    if divine_liturgy_text:
        service_texts = service_texts + [{
            "label": "Divine Liturgy (Full Outline)",
            "pdf_url": None,
            "text": divine_liturgy_text,
        }]

    return {
        "status": "ok",
        "source": "antiochian",
        "feasts": [day_info["title"]],
        "saints": day_info["commemorations"],
        "summary_title": ", ".join(day_info["commemorations"]) or day_info["title"],
        "stories": [],
        "fasting": day_info["fasting"],
        "readings": readings,
        "service_texts": service_texts,
        "synaxarion": day_info["synaxarion"],
    }


# ---------------------------------------------------------------------------
# Source: orthocal.info (fallback liturgical data)
# ---------------------------------------------------------------------------

def fetch_orthocal_day(d: date) -> dict:
    url = ORTHOCAL_URL.format(year=d.year, month=d.month, day=d.day)
    raw = http_get_json(url)

    readings = []
    for r in raw.get("readings") or []:
        text = " ".join(
            (verse.get("content") or "").strip()
            for verse in (r.get("passage") or [])
        ).strip()
        readings.append({
            "display": r.get("display"),
            "text": text,
        })

    stories = [
        {"title": s.get("title"), "html": s.get("story")}
        for s in (raw.get("stories") or [])
        if s.get("story")
    ]

    fast_level = raw.get("fast_level") or 0
    fasting = None
    if fast_level:
        fasting = {
            "description": raw.get("fast_level_desc"),
            "abstentions": raw.get("fast_abstentions") or [],
        }

    return {
        "status": "ok",
        "source": "orthocal",
        "feasts": raw.get("feasts"),
        "saints": raw.get("saints") or [],
        "summary_title": raw.get("summary_title"),
        "stories": stories,
        "fasting": fasting,
        "readings": readings,
        "service_texts": [],
        "synaxarion": None,
    }


def fetch_liturgical_day(page, id_map: dict, d: date) -> dict:
    """Try antiochian.org first (via headless browser); fall back to orthocal.info."""
    try:
        return fetch_antiochian_day(page, id_map[d], d)
    except Exception as exc:
        print(f"[warn] antiochian.org fetch failed for {d}: {exc}", file=sys.stderr)
    return fetch_orthocal_day(d)


# ---------------------------------------------------------------------------
# Source: constantinehelen.com parish events (The Events Calendar REST API)
# ---------------------------------------------------------------------------

def fetch_parish_events(start: date, end: date) -> dict:
    """Returns {date_str: [event, ...]} for every date in [start, end]."""
    events_by_date: dict[str, list] = {d.isoformat(): [] for d in date_range(start, end)}

    page = 1
    while True:
        url = (
            f"{PARISH_EVENTS_URL}?start_date={start.isoformat()}"
            f"&end_date={end.isoformat()}&per_page=100&page={page}"
        )
        raw = http_get_json(url)
        for ev in raw.get("events") or []:
            start_date = (ev.get("start_date") or "")[:10]
            if start_date not in events_by_date:
                continue
            events_by_date[start_date].append({
                "title": html.unescape(ev.get("title") or ""),
                "start": ev.get("start_date"),
                "end": ev.get("end_date"),
                "url": ev.get("url"),
            })
        total_pages = raw.get("total_pages") or 1
        if page >= total_pages:
            break
        page += 1

    return events_by_date


# ---------------------------------------------------------------------------
# Source: constantinehelen.com bulletin (WP media library)
# ---------------------------------------------------------------------------

def fetch_latest_bulletin() -> dict:
    url = f"{PARISH_MEDIA_URL}?search=bulletin&orderby=date&order=desc&per_page=1"
    raw = http_get_json(url)
    if not raw:
        raise ValueError("no bulletin found")
    item = raw[0]
    return {
        "url": item.get("source_url"),
        "posted": item.get("date"),
    }


# ---------------------------------------------------------------------------
# Source: prayer list (extracted from the bulletin PDF text)
# ---------------------------------------------------------------------------

PRAYER_LIST_PATTERN = re.compile(
    r"please\s+pray\s+for\s+the\s+health\s+of\s+body\s+and\s+soul\s+of:?\s*(?P<sick>.*?)\s*;\s*"
    r"for\s+those\s+serving\s+in[^:]*:\s*(?P<military>.*?)\s*;\s*"
    r"(?:and\s+)?for\s+our\s+catechumens:?\s*(?P<catechumens>.*?)\s*\.",
    re.IGNORECASE | re.DOTALL,
)


def split_names(blob: str) -> list[str]:
    names = [n.strip() for n in blob.replace("\n", " ").split(",")]
    names = [n for n in names if n]
    if names and names[-1].lower().startswith("and "):
        names[-1] = names[-1][4:].strip()
    return names


def find_prayer_box_text(pdf) -> str:
    """The prayer list sits in a boxed column next to unrelated text (e.g. the
    Lord's Prayer transliteration). Extracting a whole page's text interleaves
    the two side-by-side columns line by line, so instead we locate the
    anchor phrase by word position and crop just that box before extracting.
    """
    for page in pdf.pages:
        words = page.extract_words()
        for w0, w1 in zip(words, words[1:]):
            if w0["text"].lower() == "please" and w1["text"].lower() == "pray" and abs(w0["top"] - w1["top"]) < 3:
                px0, py0, px1, py1 = page.bbox
                crop_box = (
                    max(px0, w0["x0"] - 5),
                    max(py0, w0["top"] - 5),
                    px1,
                    min(py1, w0["top"] + 320),
                )
                return page.crop(crop_box).extract_text() or ""
    raise ValueError("prayer list box not found in bulletin")


def fetch_prayer_list(bulletin_url: str) -> dict:
    pdf_bytes = http_get(bulletin_url)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = find_prayer_box_text(pdf)

    match = PRAYER_LIST_PATTERN.search(text)
    if not match:
        raise ValueError("prayer list paragraph not found in bulletin text")

    return {
        "sick": split_names(match.group("sick")),
        "military": split_names(match.group("military")),
        "catechumens": split_names(match.group("catechumens")),
        "source_bulletin_url": bulletin_url,
    }


# ---------------------------------------------------------------------------
# Source: fasting calendar PDF link (scraped from parish homepage)
# ---------------------------------------------------------------------------

def fetch_fasting_calendar_link() -> str:
    body = http_get(PARISH_HOME_URL).decode("utf-8", errors="replace")
    match = re.search(r'href="([^"]*Fasting[^"]*Calendar[^"]*\.pdf)"', body, re.IGNORECASE)
    if not match:
        raise ValueError("fasting calendar link not found on parish homepage")
    return html.unescape(match.group(1))


# ---------------------------------------------------------------------------
# Source: Antiochian Archdiocese annual fasting calendar (PDF) -> data/fasting.json
#
# The Archdiocese publishes one color-coded PDF per year in a public Azure blob
# container, at a predictable name ("2026 Fasting Calendar.pdf"). The next
# year's PDF has historically appeared in late November. We read each day's
# cell fill color and map it to the legend printed on the same PDF.
#
# Per-day precedence (most to least authoritative):
#   1. antiochian.org's own daily page (already in data/days/, source=antiochian)
#   2. the Archdiocese's annual fasting-calendar PDF
#   3. orthocal.info Greek-tradition API -- only for a year whose Antiochian
#      PDF isn't published yet; marked "provisional" so the UI can say so.
# Days are never inferred from rules: every level comes from one of the above.
# ---------------------------------------------------------------------------

FASTING_PDF_URL = "https://antiochianprodsa.blob.core.windows.net/liturgicalinstructions/{year}%20Fasting%20Calendar.pdf"
ORTHOCAL_MONTH_URL = "https://orthocal.info/api/greek/gregorian/{year}/{month}/"

# Keyed by what is abstained from, matching the Antiochian PDF legend.
FAST_LEVELS = {
    "strict": {
        "label": "Strict fast",
        "allows": "No meat, dairy, eggs, fish, olive oil or wine",
        "abstain": ["meat", "dairy", "eggs", "fish", "olive oil", "wine"],
    },
    "wine": {
        "label": "Wine allowed",
        "allows": "Wine allowed; no meat, dairy, eggs, fish or olive oil",
        "abstain": ["meat", "dairy", "eggs", "fish", "olive oil"],
    },
    "wine_oil": {
        "label": "Wine & oil allowed",
        "allows": "Wine and olive oil allowed; no meat, dairy, eggs or fish",
        "abstain": ["meat", "dairy", "eggs", "fish"],
    },
    "fish": {
        "label": "Fish, wine & oil allowed",
        "allows": "Fish, wine and olive oil allowed; no meat, dairy or eggs",
        "abstain": ["meat", "dairy", "eggs"],
    },
    "dairy": {
        "label": "Dairy, eggs & fish allowed",
        "allows": "Dairy, eggs and fish allowed; no meat",
        "abstain": ["meat"],
    },
}


def fast_level_from_abstentions(abstentions) -> str | None:
    a = {x.strip().lower() for x in abstentions or []}
    a = {"olive oil" if x == "oil" else x for x in a}
    if not a:
        return None
    if {"wine", "olive oil"} <= a:
        return "strict"
    if "olive oil" in a:
        return "wine"
    if "fish" in a:
        return "wine_oil"
    if "dairy" in a or "eggs" in a:
        return "fish"
    if "meat" in a:
        return "dairy"
    return None


def _pdf_color(c) -> tuple | None:
    if c is None:
        return None
    if isinstance(c, (int, float)):
        c = (c,)
    c = tuple(float(x) for x in c)
    if len(c) == 1:
        c = (c[0], c[0], c[0])
    if len(c) != 3:
        return None
    return c


def _is_neutral(c) -> bool:
    return max(c) - min(c) < 0.05  # black / white / gray: borders & headers


def parse_fasting_pdf(pdf_bytes: bytes, year: int) -> dict:
    """Returns {"YYYY-MM-DD": level_key} for every fasting day in the PDF."""
    import calendar as _cal
    month_names = list(_cal.month_name)[1:]
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        rects = [r for r in page.rects if r.get("fill") and r["width"] > 3 and r["height"] > 3]

        def fill_at(x, y):
            for r in rects:
                if r["x0"] <= x <= r["x1"] and r["top"] <= y <= r["bottom"]:
                    c = _pdf_color(r.get("non_stroking_color"))
                    if c and not _is_neutral(c):
                        return c
            return None

        # Legend: each colored row holds an "Abstain from ..." line.
        lines = {}
        for w in words:
            lines.setdefault(round(w["top"]), []).append(w)
        legend = []
        for top, ws in lines.items():
            text = " ".join(w["text"] for w in sorted(ws, key=lambda w: w["x0"]))
            if not text.lower().startswith("abstain from"):
                continue
            c = fill_at((ws[0]["x0"] + ws[-1]["x1"]) / 2, (ws[0]["top"] + ws[0]["bottom"]) / 2)
            level = fast_level_from_abstentions(re.sub(r"(?i)^abstain from\s*", "", text).split(","))
            if c and level:
                legend.append((c, level))
        if len({lvl for _, lvl in legend}) < 4:
            raise ValueError(f"fasting PDF {year}: legend not recognised ({legend})")

        def nearest_level(c):
            best = min(legend, key=lambda e: sum((a - b) ** 2 for a, b in zip(e[0], c)))
            dist = sum((a - b) ** 2 for a, b in zip(best[0], c)) ** 0.5
            if dist > 0.25:
                raise ValueError(f"fasting PDF {year}: unknown cell color {c}")
            return best[1]

        heads = [w for w in words if w["text"] in month_names]
        if len(heads) != 12:
            raise ValueError(f"fasting PDF {year}: found {len(heads)} month headings")
        title = " ".join(w["text"] for w in words[:6])
        if str(year) not in title:
            raise ValueError(f"fasting PDF {year}: title doesn't mention the year ({title!r})")

        seen = {m: set() for m in range(1, 13)}
        result = {}
        for w in words:
            if not w["text"].isdigit():
                continue
            cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
            above = [h for h in heads if h["top"] < cy and abs((h["x0"] + h["x1"]) / 2 - cx) < 110]
            if not above:
                continue
            month = month_names.index(max(above, key=lambda h: h["top"])["text"]) + 1
            day = int(w["text"])
            seen[month].add(day)
            c = fill_at(cx, cy)
            if c:
                result[date(year, month, day).isoformat()] = nearest_level(c)
        for m, days in seen.items():
            if days != set(range(1, _cal.monthrange(year, m)[1] + 1)):
                raise ValueError(f"fasting PDF {year}: month {m} grid incomplete")
    return result


def fetch_antiochian_fasting_year(year: int) -> dict:
    url = FASTING_PDF_URL.format(year=year)
    return {"url": url, "days": parse_fasting_pdf(http_get(url), year)}


def fetch_orthocal_fasting_year(year: int) -> dict:
    days = {}
    for month in range(1, 13):
        for raw in http_get_json(ORTHOCAL_MONTH_URL.format(year=year, month=month)):
            if not raw.get("fast_level"):
                continue
            level = fast_level_from_abstentions(raw.get("fast_abstentions"))
            if raw.get("fast_exception_desc") == "Strict Fast":
                level = "strict"
            if level:
                days[date(year, month, raw["day"]).isoformat()] = level
    return {"url": "https://orthocal.info/api/greek/gregorian/", "days": days}


def orthodox_pascha(year: int) -> date:
    """Orthodox Pascha (Julian computus), as a Gregorian date. Used only to
    *name* fasting seasons; the fasting levels themselves come from sources."""
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


def fasting_seasons(year: int) -> list:
    p = orthodox_pascha(year)
    pentecost = p + timedelta(days=49)
    seasons = [
        ("fast_free", "Fast-free: Nativity to Theophany", date(year, 1, 1), date(year, 1, 4)),
        ("fast_free", "Fast-free week after the Publican & Pharisee", p - timedelta(days=69), p - timedelta(days=63)),
        ("season", "Cheesefare Week", p - timedelta(days=55), p - timedelta(days=49)),
        ("season", "Great Lent", p - timedelta(days=48), p - timedelta(days=7)),
        ("season", "Holy Week", p - timedelta(days=6), p - timedelta(days=1)),
        ("fast_free", "Fast-free: Bright Week", p, p + timedelta(days=6)),
        ("fast_free", "Fast-free: Week after Pentecost", pentecost, pentecost + timedelta(days=6)),
        ("season", "Apostles' Fast", pentecost + timedelta(days=8), date(year, 6, 28)),
        ("season", "Dormition Fast", date(year, 8, 1), date(year, 8, 14)),
        ("season", "Nativity Fast", date(year, 11, 15), date(year, 12, 24)),
        ("fast_free", "Fast-free: Nativity to Theophany", date(year, 12, 25), date(year, 12, 31)),
    ]
    return [
        {"kind": k, "name": n, "start": s.isoformat(), "end": e.isoformat()}
        for k, n, s, e in seasons if s <= e
    ]


def build_fasting_calendar(today: date) -> bool:
    """Writes data/fasting.json covering this year and next. Returns True if
    every year got data from some source this run."""
    path = DATA_DIR / "fasting.json"
    prior = load_json(path) or {}
    prior_years = prior.get("years", {})
    years = [today.year, today.year + 1]
    now_iso = datetime.now(CENTRAL).isoformat()

    out_years, days = {}, {}
    all_ok = True
    for y in years:
        meta, year_days = None, None
        try:
            got = fetch_antiochian_fasting_year(y)
            meta = {"source": "antiochian_pdf", "provisional": False, "url": got["url"], "fetched_at": now_iso}
            year_days = got["days"]
        except Exception as exc:
            print(f"[info] Antiochian fasting PDF for {y} unavailable: {exc}", file=sys.stderr)
            prev = prior_years.get(str(y))
            if prev and prev.get("source") == "antiochian_pdf":
                # Keep the last good official data rather than downgrading.
                meta = prev
                year_days = {k: v["level"] for k, v in prior.get("days", {}).items()
                             if k.startswith(f"{y}-") and v.get("source") == "antiochian_pdf"}
            else:
                try:
                    got = fetch_orthocal_fasting_year(y)
                    meta = {"source": "orthocal", "provisional": True, "url": got["url"], "fetched_at": now_iso}
                    year_days = got["days"]
                except Exception as exc2:
                    print(f"[warn] orthocal fasting fallback for {y} failed: {exc2}", file=sys.stderr)
                    if prev:
                        meta = prev
                        year_days = {k: v["level"] for k, v in prior.get("days", {}).items() if k.startswith(f"{y}-")}
                    else:
                        all_ok = False
                        continue
        out_years[str(y)] = meta
        for k, lvl in year_days.items():
            days[k] = {"level": lvl, "source": meta["source"]}

    # antiochian.org's own daily pages override the annual sources.
    for p in DAYS_DIR.glob("*.json"):
        d = load_json(p) or {}
        lit = d.get("liturgical") or {}
        if lit.get("status") != "ok" or lit.get("source") != "antiochian" or d.get("date", "")[:4] not in out_years:
            continue
        key = d["date"]
        level = fast_level_from_abstentions((lit.get("fasting") or {}).get("abstentions"))
        prev = days.get(key, {}).get("level")
        if prev != level:
            print(f"[info] fasting {key}: antiochian.org daily page says {level}, annual source said {prev}", file=sys.stderr)
        if level:
            days[key] = {"level": level, "source": "antiochian_daily"}
        else:
            days.pop(key, None)

    seasons = [s for y in years if str(y) in out_years for s in fasting_seasons(y)]
    data = {
        "generated_at": now_iso,
        "years": out_years,
        "levels": FAST_LEVELS,
        "seasons": seasons,
        "days": dict(sorted(days.items())),
        "sources": {
            "antiochian_daily": "antiochian.org daily liturgical page",
            "antiochian_pdf": "Antiochian Archdiocese annual fasting calendar (PDF)",
            "orthocal": "orthocal.info (Greek/Antiochian tradition, beta) -- provisional until the Archdiocese publishes its calendar",
        },
        "note": "On the day of a parish's patronal feast, fish, wine and oil are allowed if it falls on a fasting day. When fasting, consult your spiritual father.",
    }
    if out_years:
        path.write_text(json.dumps(data, indent=1))
    return all_ok and bool(out_years)


# ---------------------------------------------------------------------------
# Source: newadvent.org -- Catholic Encyclopedia (1913) saint biographies
#
# Attached to a day's commemorated saints only when we're confident the
# article is about the same person. The rule of thumb is "no match beats a
# wrong match": many saints share a first name, and post-schism Orthodox
# saints usually have no (or no fair) entry.
#
# Matching, per commemorated saint:
#   1. Only CE index entries the Encyclopedia itself titles as a saint
#      ("Cyril of Alexandria, Saint", "Nicholas I, Saint, Pope").
#   2. The given name must match (after normalising Greek/Latin spellings:
#      Dionysios ~ Dionysius, Nektarios ~ Nectarius).
#   3. Every distinguishing word in the CE title (a place, an epithet like
#      "Chrysostom") must appear in the commemoration or the day's
#      Synaxarion -- so "Nicholas of Tolentino" can never match "Nicholas,
#      Archbishop of Myra".
#   4. Every distinguishing word in the commemoration (place names,
#      epithets) must appear somewhere in the article text. A bare name with
#      no qualifier ("Guria") must be the only CE saint with that name AND
#      the article must mention the commemoration's role (apostle, prophet,
#      martyr...), or it's skipped.
#   5. If more than one entry survives, nothing is attached.
# Index pages and articles are cached in data/cache/newadvent.json so a
# given article is fetched once, not daily.
# ---------------------------------------------------------------------------

NEWADVENT_BASE = "https://www.newadvent.org/cathen/"
NEWADVENT_CACHE_PATH = DATA_DIR / "cache" / "newadvent.json"
NEWADVENT_MATCH_VERSION = 3  # 3: no more excerpts cut off at "St."
NEWADVENT_INDEX_MAX_AGE_DAYS = 180
NEWADVENT_MAX_FETCHES_PER_RUN = 150
NEWADVENT_DELAY_SECONDS = 1.0
NEWADVENT_EXCERPT_CHARS = 420
NEWADVENT_MORE_CHARS = 3200
NEWADVENT_CACHE_TEXT_CHARS = 12000

# Honorifics / roles that say nothing about *which* person this is.
NA_GENERIC_WORDS = {
    "saint", "saints", "st", "sts", "holy", "great", "martyr", "martyrs", "greatmartyr",
    "hieromartyr", "new", "righteous", "venerable", "ven", "our", "father", "fathers",
    "mother", "among", "the", "of", "and", "his", "her", "their", "with", "those", "who",
    "apostle", "apostles", "evangelist", "prophet", "prophetess", "virgin", "virginmartyr",
    "confessor", "confessors", "bishop", "archbishop", "patriarch", "metropolitan", "pope",
    "abbot", "abbess", "monk", "nun", "deacon", "archdeacon", "presbyter", "priest",
    "hermit", "ascetic", "wonderworker", "first", "called", "disciple", "blessed", "in",
    "at", "a", "an", "to", "or", "also", "pope", "bishops", "archbishops", "patriarchs",
    "wife", "children", "companions", "companion", "others", "other", "emperor", "empress",
    "king", "queen", "i", "ii", "iii", "iv", "v", "vi", "bearer", "godbearer", "elder",
    "younger", "doctor", "church", "founder", "teacher",
}
# Same person, different English names in Orthodox use vs the 1913 Encyclopedia.
NA_ALIASES = [
    (r"\bthe Studite\b", "of Studium"),
    (r"\bthe Merciful\b", "the Almsgiver"),
    (r"^Lucia\b", "Lucy"),
    (r"^Spyridon\b", "Spiridion"),
    (r"\bTrymithous\b", "Tremithus"),
]
# "New Martyr" / "the New" saints are almost always post-schism (often
# Ottoman-era) namesakes of an ancient saint; skip rather than risk a mismatch.
# Also skip relatives described through a more famous saint ("father of St
# Gregory the Theologian"): their words would point at the famous one.
NA_SKIP_MENTION = re.compile(r"\bnew\b|of the (70|seventy)\b|\b(father|mother|brother|sister|son|daughter|disciple|wife) of\b", re.IGNORECASE)
_NA_OF_OK = {"bishop", "archbishop", "patriarch", "church", "see", "city", "diocese", "province",
             "saint", "st", "native", "born", "metropolitan", "abbot", "monastery", "desert", "people"}


def na_word_in_lead(lead: str, word: str, given: str) -> bool:
    """True if `word` (normalised) occurs in `lead` other than as another
    person's byname: "St. Alexander of Alexandria" doesn't make an article
    about Macarius of Jerusalem a match for "Macarius of Alexandria"."""
    toks = re.findall(r"[A-Za-z\u00C0-\u024F'-]+", lead)
    for i, t in enumerate(toks):
        if word not in na_words(t):
            continue
        if i >= 2 and toks[i - 1].lower() == "of" and toks[i - 2][:1].isupper():
            prev = toks[i - 2]
            if na_norm_word(prev) != given and prev.lower() not in _NA_OF_OK:
                continue  # someone else's "X of Place"
        return True
    return False
# Roles we can verify in the article text for an otherwise bare name.
NA_ROLE_WORDS = {"apostle", "evangelist", "prophet", "martyr", "virgin", "bishop",
                 "archbishop", "patriarch", "pope", "abbot", "hermit", "deacon", "confessor"}
# A commemoration's role is confirmed by any word of its family in the article.
_NA_HIERARCH = {"bishop", "archbishop", "patriarch", "pope", "metropolitan", "prelate", "see"}
NA_ROLE_FAMILIES = {r: _NA_HIERARCH for r in ("bishop", "archbishop", "patriarch", "pope")}
NA_ROLE_FAMILIES.update({
    "martyr": {"martyr", "martyrdom", "martyred", "suffered"},
    "virgin": {"virgin", "martyr"},
    "apostle": {"apostle", "apostles"},
    "evangelist": {"evangelist", "apostle", "gospel"},
})
# Pieces after a comma that describe the previous name rather than start a new one.
NA_CONTINUATION = re.compile(
    r"^(and |& |archbishop|bishop|patriarch|metropolitan|pope|abbess|abbot|nun|monk|"
    r"founder|brother|sister|mother|father of|wife|martyrs? and|apostles? of|new hieromartyr of|"
    r"missionary|patron|poet|who |ascetic|archdeacon|president|the mother|monastery|"
    r"kanons|the martyrs?$|the righteous$|\(|\d)", re.IGNORECASE)
NA_NOT_A_SAINT = re.compile(
    r"\b(sunday|saturday|forefeast|afterfeast|apodosis|leavetaking|synaxis|nativity|"
    r"theophany|entrance|presentation|conception|consecration|commemoration|rememberance|"
    r"remembrance|opening|founding|fast|week|eve of|adoration|translation of the relics)\b",
    re.IGNORECASE)
NA_HONORIFIC_PREFIX = re.compile(
    r"^((our|holy|righteous|venerable|ven\.?|st\.?|saint|great|new|the|hieromartyr|martyrs?|"
    r"father|fathers|mother|among|saints|apostle|prophet|holy prophet|glorious|king|blessed|"
    r"empress|virgin-martyr|nun-martyr)\s+)+", re.IGNORECASE)


class NewAdventBudgetExhausted(Exception):
    pass


def na_norm_word(w: str) -> str:
    w = unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode().lower()
    w = re.sub(r"[^a-z]", "", w)
    w = w.replace("k", "c").replace("ph", "f").replace("y", "i").replace("th", "t")
    w = re.sub(r"(os|us|as|es|ius|ios)$", "", w)
    w = re.sub(r"(.)\1", r"\1", w)
    return w


def na_words(text: str) -> set:
    """Word set for comparing places/epithets. Also folds demonyms onto the
    place ("Egyptian" ~ "Egypt", "Persian" ~ "Persia")."""
    out = set()
    for w in re.findall(r"[A-Za-z\u00C0-\u024F'-]+", html.unescape(text or "")):
        n = na_norm_word(w)
        n = re.sub(r"(ians?|ia|ns)$", "", n) if len(n) > 5 else n
        if n:
            out.add(n)
    return out


NA_GENERIC_NORM = {na_norm_word(w) for w in NA_GENERIC_WORDS}
NA_CONTEXT_STOP = {na_norm_word(w) for w in (
    "God", "Christ", "Jesus", "Lord", "Holy", "Spirit", "Saint", "Saints", "Church", "Christian",
    "Christians", "Amen", "Verses", "Glory", "Father", "Son", "Theotokos", "Virgin", "Emperor",
    "When", "After", "Then", "This", "They", "There", "Their", "With", "From", "Being", "Having",
    "Mercy", "Through", "Intercessions", "Also", "Today", "Commemorate", "Orthodox", "January",
    "February", "March", "April", "June", "July", "August", "September", "October", "November",
    "December", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")}




def na_split_mentions(saints: list) -> list:
    """Regroup antiochian.org's comma-split commemorations into one string
    per saint (e.g. ["John Chrysostom", "Archbishop of Constantinople"] ->
    ["John Chrysostom, Archbishop of Constantinople"])."""
    out = []
    for piece in saints or []:
        piece = re.sub(r"\s*\(\d{3,4}\)\.?$", "", piece.strip()).strip(" .")
        if not piece:
            continue
        if out and NA_CONTINUATION.match(piece):
            out[-1] = out[-1] + ", " + piece
        else:
            out.append(piece)
    return out


def na_parse_mention(text: str) -> dict | None:
    if NA_NOT_A_SAINT.search(text) or NA_SKIP_MENTION.search(text):
        return None
    original = text
    text = NA_HONORIFIC_PREFIX.sub("", text.strip())
    for pattern, repl in NA_ALIASES:
        text = re.sub(pattern, repl, text)
    head = re.split(r",| & | and ", text, maxsplit=1)[0]
    head = NA_HONORIFIC_PREFIX.sub("", head.strip())
    m = re.match(r"([A-Z][A-Za-z\u00C0-\u024F'-]+)", head)
    if not m:
        return None
    given = m.group(1)
    words = na_words(text)
    distinct = words - NA_GENERIC_NORM - na_words(given)
    roles = {r for r in NA_ROLE_WORDS if re.search(rf"\b{r}", original, re.IGNORECASE)}
    if "pope" in roles:
        distinct -= {na_norm_word("rome")}  # "Pope of Rome" adds nothing
    return {"text": original, "given": na_norm_word(given), "distinct": distinct, "roles": roles}


def na_parse_index_title(title: str) -> dict | None:
    t = html.unescape(title)
    if not re.search(r"\bSaints?\b", t) or t.lower().startswith("sainte "):
        return None
    if re.search(r"\b(Order|Monastery|Church|Abbey|Congregation|Sisters|Brothers|Diocese|Feast|Island|Oratory|Gospel|Epistle|Rule|Liturgy|Acts|Mass)\b", t):
        return None
    paren = " ".join(re.findall(r"\(([^)]*)\)", t))
    base = re.sub(r"\([^)]*\)", "", t)
    parts = [p.strip() for p in base.split(",") if p.strip()]
    # "Gonzaga, Saint Aloysius" -> given name follows "Saint"
    m = re.match(r"^(?:Pope\s+)?Saints?\s+(.+)$", parts[-1]) if len(parts) > 1 else None
    if m and len(parts) == 2:
        name_part, extra = m.group(1), parts[0]
    else:
        name_part = parts[0]
        extra = " ".join(p for p in parts[1:] if not re.fullmatch(r"(Pope\s+)?Saints?( and Martyrs?)?|Pope|Saint", p))
    group = re.split(r",| and ", name_part)
    givens = []
    for g in group:
        g = g.strip()
        gm = re.match(r"([A-Z][A-Za-z'-]+)", g)
        if gm:
            givens.append(na_norm_word(gm.group(1)))
    if not givens:
        return None
    distinct = na_words(name_part + " " + extra + " " + paren) - NA_GENERIC_NORM - set(givens)
    distinct -= {w for g in group for w in na_words(g.strip().split(" ")[0])}
    distinct = {w for w in distinct if not re.fullmatch(r"[ivxl]+", w)}
    return {"title": t, "givens": givens, "distinct": distinct, "pope": bool(re.search(r"\bPope\b", t))}


def na_article_text(page_html: str) -> tuple[str, list]:
    """Returns (h1 title, [paragraph texts]) for a Catholic Encyclopedia page."""
    h1 = re.search(r"<h1>(.*?)</h1>", page_html, re.DOTALL)
    title = html.unescape(re.sub(r"<[^>]+>", "", h1.group(1))).strip() if h1 else ""
    body = page_html[h1.end():] if h1 else page_html
    body = re.split(r'<div class="cenotes"|<div id="ogdenville"', body)[0]
    paras = []
    for p in re.findall(r"<p>(.*?)</p>", body, re.DOTALL):
        if "gumroad.com" in p or "Please help support" in p:
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", p))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            paras.append(text)
    return title, paras


# Abbreviations whose full stop is not the end of a sentence ("St. Andrew",
# "c. 450"): cutting there left excerpts ending in a dangling "St.".
NA_ABBREV_END = re.compile(r"(?:\b(?i:sts?|ss|ven|bl|fr|mt|mk|lk|jn|dr|mr|ca|cf|c|vol|ch|chap|ed|op|vv?|ff|i\.e|e\.g|viz|etc|abp|bp|gen|ex|ps|is|rom|cor|gal|eph|heb|rev|acts)|\b[A-Z])\.$")


def na_truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = -1
    for m in re.finditer(r"[.;](?= )", cut):
        if m.group() == "." and NA_ABBREV_END.search(cut[: m.end()]):
            continue
        stop = m.start()
    return (cut[: stop + 1] if stop > limit * 0.5 else cut.rsplit(" ", 1)[0] + "\u2026").strip()


class NewAdventMatcher:
    def __init__(self):
        self.cache = load_json(NEWADVENT_CACHE_PATH) or {}
        self.cache.setdefault("articles", {})
        self.fetches = 0
        self.dirty = False
        self._entries = None

    def _get(self, url: str) -> str:
        if self.fetches >= NEWADVENT_MAX_FETCHES_PER_RUN:
            raise NewAdventBudgetExhausted()
        if self.fetches:
            time.sleep(NEWADVENT_DELAY_SECONDS)
        self.fetches += 1
        return http_get(url).decode("latin-1")

    def _ensure_index(self):
        fetched = self.cache.get("index_fetched_at")
        fresh = fetched and (datetime.now(CENTRAL) - datetime.fromisoformat(fetched)).days < NEWADVENT_INDEX_MAX_AGE_DAYS
        if not (fresh and self.cache.get("index")):
            todo = [f"{c}.htm" for c in "abcdefghijklmnopqrstuvwxyz"]
            seen, index = set(), {}
            while todo:
                page = todo.pop(0)
                if page in seen:
                    continue
                seen.add(page)
                h = self._get(NEWADVENT_BASE + page)
                todo += [s for s in re.findall(r'href="\.\./cathen/([a-z]-[a-z]+\.htm)"', h) if s not in seen]
                for href, title in re.findall(r'<a href="\.\./cathen/(\d+[a-z]?)\.htm">([^<]+)</a>', h):
                    if na_parse_index_title(title):
                        index[href] = html.unescape(title).strip()
            if len(index) < 300:
                raise ValueError(f"New Advent index looks wrong ({len(index)} saint entries)")
            self.cache["index"] = index
            self.cache["index_fetched_at"] = datetime.now(CENTRAL).isoformat()
            self.dirty = True
        if self._entries is None:
            self._entries = []
            for aid, title in self.cache["index"].items():
                parsed = na_parse_index_title(title)
                if parsed:
                    parsed["id"] = aid
                    self._entries.append(parsed)

    def _article(self, aid: str) -> dict:
        art = self.cache["articles"].get(aid)
        if art is None:
            title, paras = na_article_text(self._get(f"{NEWADVENT_BASE}{aid}.htm"))
            text = "\n\n".join(paras)
            art = {"title": title, "text": text[:NEWADVENT_CACHE_TEXT_CHARS]}
            self.cache["articles"][aid] = art
            self.dirty = True
        return art

    @staticmethod
    def _bare_context(mention: dict, context_text: str) -> set:
        """Distinctive capitalised words from the day's Synaxarion/stories
        sentences that mention this saint (e.g. "Tarsus" for Boniface)."""
        words = set()
        for sentence in re.split(r"(?<=[.;!?])\s+", context_text or ""):
            toks = re.findall(r"[A-Za-z\u00C0-\u024F'-]+", sentence)
            if not any(na_norm_word(t) == mention["given"] for t in toks):
                continue
            for t in toks:
                if t[:1].isupper() and len(t) > 3:
                    words |= na_words(t)
        return words - NA_GENERIC_NORM - NA_CONTEXT_STOP - na_words(mention["given"]) - {mention["given"]}

    def match(self, mention: dict, context_words: set, context_text: str = "") -> dict | None:
        cands = [e for e in self._entries if mention["given"] in e["givens"]]
        known = mention["distinct"]  # (not the day's context: too loose)
        survivors = []
        for e in cands:
            if len(e["givens"]) > 1:
                continue  # joint articles ("Cyril and Methodius") -- skip, too easy to mismatch
            if not e["distinct"] <= known:
                continue  # CE title names a place/epithet the commemoration doesn't
            if e["pope"] != ("pope" in mention["roles"]):
                continue  # a pope only matches a commemoration that says "Pope"
            survivors.append(e)
        bare_ctx = set()
        if not mention["distinct"]:
            # Bare name ("Philip the Apostle"): exactly one plain CE entry may
            # survive, and its opening must confirm the role (apostle...).
            if len(survivors) != 1 or survivors[0]["distinct"] or not mention["roles"]:
                return None
            # If the day's Synaxarion says more about this saint (a place, a
            # persecutor...), the article must share at least one of those.
            bare_ctx = self._bare_context(mention, context_text)
            # With nothing else to go on, only a name the Encyclopedia uses
            # for a single saint (or a biblical apostle/prophet) is safe:
            # "Boniface the Martyr" must not land on Boniface of Germany.
            biblical = mention["roles"] & {"apostle", "evangelist", "prophet", "pope"}
            if not bare_ctx and not biblical and len(cands) != 1:
                return None
        results = []
        for e in survivors:
            art = self._article(e["id"])
            art_words = na_words(art["title"] + " " + art["text"])
            lead_words = na_words(art["title"] + " " + art["text"][:500])
            # If the CE title itself carries the distinguishing words
            # ("Nicholas of Myra"), the article body may confirm them anywhere;
            # a plain title ("Peter, Saint") must confirm them in its opening,
            # otherwise "Peter, Archbishop of Alexandria" could land on the
            # Apostle Peter just because his article mentions Alexandria.
            if e["distinct"] & mention["distinct"]:
                if not mention["distinct"] <= art_words:
                    continue
            else:
                lead = art["title"] + " " + art["text"][:260]  # opening only
                if not all(na_word_in_lead(lead, w, mention["given"]) for w in mention["distinct"]):
                    continue
            if bare_ctx and not (bare_ctx & art_words):
                continue
            # An article that opens "Bishop of Samosata..." is about a hierarch;
            # don't give it to a commemoration that isn't one ("Eusebius the
            # Righteous of Syria") unless the CE title itself pins the match.
            opening = art["text"][:80].lower()
            if (len(cands) > 1 and not (e["distinct"] & mention["distinct"])
                    and not re.search(r"hieromartyr", mention["text"], re.IGNORECASE)
                    and re.match(r"(arch)?bishop|patriarch|pope", opening)
                    and not (mention["roles"] & {"bishop", "archbishop", "patriarch", "pope"})):
                continue
            strong = bool(e["distinct"] & mention["distinct"])
            if mention["roles"] and not strong:
                ok_words = set()
                for r in mention["roles"]:
                    ok_words |= NA_ROLE_FAMILIES.get(r, {r})
                if not any(na_norm_word(w) in lead_words for w in ok_words):
                    continue
            score = len(mention["distinct"] & art_words) + 2 * len(e["distinct"] & mention["distinct"])
            results.append((score, e, art))
        if not results:
            return None
        results.sort(key=lambda r: -r[0])
        if len(results) > 1 and results[0][0] == results[1][0]:
            return None  # ambiguous
        _, e, art = results[0]
        paras = art["text"].split("\n\n")
        lead = paras[0] if paras else ""
        # The first paragraph is sometimes just a one-line feast/date note.
        if len(lead) < 200 and len(paras) > 1:
            lead = lead + " " + paras[1]
        return {
            "commemoration": mention["text"],
            "title": art["title"] or e["title"],
            "url": f"{NEWADVENT_BASE}{e['id']}.htm",
            "excerpt": na_truncate(lead, NEWADVENT_EXCERPT_CHARS),
            "more": na_truncate(art["text"], NEWADVENT_MORE_CHARS),
        }

    def biographies_for(self, liturgical: dict) -> list:
        self._ensure_index()
        context_text = " ".join(filter(None, [liturgical.get("synaxarion")] + [
            re.sub(r"<[^>]+>", " ", s.get("html") or "") for s in liturgical.get("stories") or []]))
        context = na_words(context_text) - NA_GENERIC_NORM
        out, seen = [], set()
        for text in na_split_mentions(liturgical.get("saints") or []):
            mention = na_parse_mention(text)
            if not mention:
                continue
            found = self.match(mention, context, context_text)
            if found and found["url"] not in seen:
                seen.add(found["url"])
                out.append(found)
        return out

    def save(self):
        if self.dirty:
            NEWADVENT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            NEWADVENT_CACHE_PATH.write_text(json.dumps(self.cache, indent=0, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Source: oca.org -- Orthodox lives of the saints, shown before New Advent
#
# For each saint that got a New Advent biography, look for the same saint's
# life on the OCA's "Lives of the Saints" page for that date (New Calendar,
# like antiochian.org). Same rule as New Advent: no match beats a wrong one.
# The OCA name (parenthetical asides removed) must contain the saint's given
# name; when several do, the one sharing the most places/epithets/roles with
# the commemoration wins, and a tie attaches nothing. Results live in
# data/orthodox_lives.json (by date), not the day files.
# ---------------------------------------------------------------------------

OCA_BASE = "https://www.oca.org"
ORTHODOX_LIVES_PATH = DATA_DIR / "orthodox_lives.json"
ORTHODOX_LIVES_VERSION = 1
OCA_MAX_FETCHES_PER_RUN = 120
OCA_DELAY_SECONDS = 1.0
OCA_EXCERPT_CHARS = 420
OCA_MORE_CHARS = 3200


# OCA titles' honorifics that say nothing about which saint this is.
OCA_HONORIFIC_NORM = {na_norm_word(w) for w in (
    "all-praised", "allpraised", "praised", "glorious", "enlightener", "evangelizer", "equal", "equal-to-the-apostles",
    "monastic", "prince", "princess", "author", "twelve", "seventy", "children", "with", "him", "her", "them",
    "those", "nun", "schemamonk", "passion-bearer", "unmercenary", "unmercenaries", "fool-for-christ", "wonderworking")}


class OCABudgetExhausted(Exception):
    pass


def oca_clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def oca_parse_day(page_html: str, d: date) -> list:
    """[{name, url, description}] from an OCA Lives of the Saints day page."""
    out = []
    for art in re.findall(r'<article class="saint[^"]*">(.*?)</article>', page_html, re.DOTALL):
        nm = re.search(r'<h2 class="name">(.*?)</h2>', art, re.DOTALL)
        link = re.search(r'href="(/saints/lives/%d/%02d/%02d/[^"]+)"' % (d.year, d.month, d.day), art)
        if not nm or not link:
            continue
        desc = re.search(r'<p class="description">(.*?)</p>', art, re.DOTALL)
        out.append({"name": oca_clean(re.sub(r"<!--.*?-->", "", nm.group(1))), "url": OCA_BASE + link.group(1),
                    "description": oca_clean(desc.group(1)) if desc else ""})
    return out


def oca_parse_life(page_html: str) -> list:
    """Paragraph texts of one OCA life page (the <article> body)."""
    m = re.search(r"<article>(.*?)</article>", page_html, re.DOTALL)
    body = m.group(1) if m else ""
    body = re.sub(r"<header.*?</header>|<figure.*?</figure>", "", body, flags=re.DOTALL)
    paras = [oca_clean(p) for p in re.split(r"<p[^>]*>", body)]
    return [p for p in paras if len(p) > 1]


class OCALives:
    def __init__(self):
        self.data = load_json(ORTHODOX_LIVES_PATH) or {}
        if self.data.get("version") != ORTHODOX_LIVES_VERSION:
            self.data = {"version": ORTHODOX_LIVES_VERSION, "days": {}}
        self.fetches = 0
        self.dirty = False

    def _get(self, url: str) -> str:
        if self.fetches >= OCA_MAX_FETCHES_PER_RUN:
            raise OCABudgetExhausted()
        if self.fetches:
            time.sleep(OCA_DELAY_SECONDS)
        self.fetches += 1
        return http_get(url).decode("utf-8", errors="replace")

    def has(self, d: date) -> bool:
        return d.isoformat() in self.data["days"]

    @staticmethod
    def match(mention: dict, entries: list, bio_text: str = "") -> dict | None:
        """The OCA entry for the saint of `mention`, checked against the New
        Advent article (`bio_text`) so both sources are about one person."""
        bio_words = na_words(bio_text)
        hierarch = re.compile(r"\b(bishop|archbishop|patriarch|pope|metropolitan)\b", re.IGNORECASE)
        scored = []
        for e in entries:
            name = re.sub(r"\([^)]*\)", " ", re.sub(r"^[^:]*:\s*", "", e["name"]))
            words = na_words(name)
            if mention["given"] not in words:
                continue
            # The given name must name this saint, not a relative or teacher
            # mentioned after "sister of" / "disciple of".
            rel = re.search(r"\b(sister|brother|mother|father|son|daughter|wife|husband|disciple|teacher)s? of\b", name, re.IGNORECASE)
            if rel and mention["given"] not in na_words(name[: rel.start()]):
                continue
            others = words - NA_GENERIC_NORM - {mention["given"]} - OCA_HONORIFIC_NORM
            others = {w for w in others if not w.isdigit()}
            # The OCA name names a place/epithet the commemoration doesn't
            # share at all: a namesake, not this saint.
            if others and mention["distinct"] and not (others & mention["distinct"]):
                continue
            # Places/epithets only the OCA name has must be confirmed by the
            # New Advent article ("Babylas of Nicomedia" vs Babylas of Antioch).
            extra = others - mention["distinct"]
            if extra and not (others & mention["distinct"]) and not (extra & bio_words):
                continue
            # A hierarch on one side only: the article must say so too.
            if hierarch.search(name) and not hierarch.search(mention["text"]) and not hierarch.search(bio_text[:600]):
                continue
            overlap = mention["distinct"] & (others | na_words(e["description"][:400]))
            roles = {r for r in mention["roles"] if re.search(rf"{r}", name, re.IGNORECASE)}
            scored.append((2 * len(overlap) + len(roles) + len(extra & bio_words), e))
        if not scored:
            return None
        scored.sort(key=lambda t: -t[0])
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    def lives_for(self, d: date, liturgical: dict) -> list:
        """Orthodox lives for the saints that have a New Advent bio."""
        bios = liturgical.get("newadvent") or []
        if not bios:
            return []
        entries = oca_parse_day(self._get(f"{OCA_BASE}/saints/lives/{d.year}/{d.month:02d}/{d.day:02d}"), d)
        out = []
        for b in bios:
            mention = na_parse_mention(b["commemoration"])
            e = self.match(mention, entries, b.get("title", "") + " " + (b.get("more") or b.get("excerpt") or "")) if mention else None
            if not e:
                continue
            paras = oca_parse_life(self._get(e["url"])) or [e["description"].rstrip("\u2026")]
            text = "\n\n".join(paras)
            out.append({
                "commemoration": b["commemoration"],
                "newadvent_url": b["url"],
                "title": re.sub(r"^[^:]*(?:Sunday|Saturday)[^:]*:\s*", "", e["name"]),
                "url": e["url"],
                "source": "OCA",
                "excerpt": na_truncate(paras[0], OCA_EXCERPT_CHARS),
                "more": na_truncate(text, OCA_MORE_CHARS),
            })
        return out

    def update(self, d: date, liturgical: dict):
        self.data["days"][d.isoformat()] = self.lives_for(d, liturgical)
        self.dirty = True

    def prune(self, window_start: date, window_end: date):
        for k in list(self.data["days"]):
            if not (window_start.isoformat() <= k <= window_end.isoformat()):
                del self.data["days"][k]
                self.dirty = True

    def save(self):
        if self.dirty:
            ORTHODOX_LIVES_PATH.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def day_file_path(d: date) -> Path:
    return DAYS_DIR / f"{d.isoformat()}.json"


def service_texts_incomplete(service_texts) -> bool:
    """True if no service-text links were found at all, or a link was found
    but its RTF text never actually got fetched (a transient glitch), either
    of which is worth retrying rather than trusting forever."""
    if not service_texts:
        return True
    return any(e.get("rtf_url") and not e.get("text") for e in service_texts)


def build_day_files(window_start: date, window_end: date) -> tuple[str, bool, bool, bool]:
    """Returns (generated_at timestamp, any_liturgical_ok, any_events_ok,
    window_complete). window_complete is False while an incremental backfill
    still has days left to fetch (the 2-hourly health-check run picks it up)."""
    now_iso = datetime.now(CENTRAL).isoformat()
    today = window_start + timedelta(days=WINDOW_BACK_DAYS)
    service_text_retry_cutoff = today + timedelta(days=SERVICE_TEXT_RETRY_DAYS)

    events_by_date = None
    events_ok = False
    try:
        events_by_date = fetch_parish_events(window_start, window_end)
        events_ok = True
    except Exception as exc:
        print(f"[warn] parish events fetch failed: {exc}", file=sys.stderr)

    dates_needing_liturgical = []
    for d in date_range(window_start, window_end):
        existing = load_json(day_file_path(d))
        existing_liturgical = (existing or {}).get("liturgical") or {}
        status_ok = existing_liturgical.get("status") == "ok"
        missing_service_texts = status_ok and service_texts_incomplete(existing_liturgical.get("service_texts"))
        # Only recent/upcoming days are worth re-checking for service texts;
        # long-past days are settled.
        recent = today - timedelta(days=2) <= d <= service_text_retry_cutoff
        if not status_ok or (missing_service_texts and recent):
            dates_needing_liturgical.append(d)

    # Nearest days first, capped per run so a backfill spreads across runs.
    dates_needing_liturgical.sort(key=lambda d: (abs((d - today).days), d))
    deferred = dates_needing_liturgical[MAX_LITURGICAL_FETCHES_PER_RUN:]
    dates_needing_liturgical = dates_needing_liturgical[:MAX_LITURGICAL_FETCHES_PER_RUN]
    if deferred:
        print(f"[info] {len(deferred)} day(s) deferred to a later run (backfill in progress)", file=sys.stderr)

    liturgical_by_date = {}
    any_liturgical_ok = False
    if dates_needing_liturgical:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=USER_AGENT)
            try:
                anchor_id, anchor_date = antiochian_find_today_id(page)
                if abs((anchor_date - today_central()).days) > 2:
                    raise ValueError(f"antiochian.org reports {anchor_date}, too far from expected today")
                id_map = {d: anchor_id + (d - anchor_date).days for d in dates_needing_liturgical}
            except Exception as exc:
                print(f"[warn] antiochian.org unavailable this run: {exc}", file=sys.stderr)
                id_map = {}

            # antiochian.org only publishes so far ahead (e.g. through Dec 31).
            # Once a future day fails there, go straight to orthocal for any
            # later day this run instead of waiting on more page loads.
            antiochian_horizon = None
            for i, d in enumerate(dates_needing_liturgical):
                if i:
                    time.sleep(ANTIOCHIAN_DELAY_SECONDS)
                use_antiochian = d in id_map and not (antiochian_horizon and d >= antiochian_horizon)
                try:
                    if use_antiochian:
                        try:
                            liturgical_by_date[d] = fetch_antiochian_day(page, id_map[d], d)
                        except Exception as exc:
                            print(f"[warn] antiochian.org fetch failed for {d}: {exc}", file=sys.stderr)
                            if d > today and (antiochian_horizon is None or d < antiochian_horizon):
                                antiochian_horizon = d
                            liturgical_by_date[d] = fetch_orthocal_day(d)
                    else:
                        liturgical_by_date[d] = fetch_orthocal_day(d)
                    any_liturgical_ok = True
                except Exception as exc:
                    print(f"[warn] liturgical fetch failed for {d}: {exc}", file=sys.stderr)
            browser.close()

    newadvent = NewAdventMatcher()
    oca = OCALives()
    oca.prune(window_start, window_end)
    window_complete = not deferred

    for d in date_range(window_start, window_end):
        existing = load_json(day_file_path(d))

        if d in liturgical_by_date:
            liturgical = liturgical_by_date[d]
        elif existing and existing.get("liturgical", {}).get("status") == "ok":
            liturgical = existing["liturgical"]
            any_liturgical_ok = True
        elif existing:
            liturgical = existing.get("liturgical") or {"status": "error"}
        else:
            # Never fetched yet (backfill still in progress): don't publish a
            # half-empty day; it'll appear once a run gets to it.
            window_complete = False
            continue

        if liturgical.get("status") == "ok":
            text_blob = " ".join(filter(None, [liturgical.get("summary_title")] + (liturgical.get("feasts") or [])))
            liturgical["great_feast"] = identify_great_feast(text_blob)
            rematched = False
            if liturgical.get("newadvent_version") != NEWADVENT_MATCH_VERSION:
                try:
                    rematched = True
                    bios = newadvent.biographies_for(liturgical)
                    liturgical["newadvent"] = bios
                    liturgical["newadvent_version"] = NEWADVENT_MATCH_VERSION
                except NewAdventBudgetExhausted:
                    window_complete = False  # finish matching on a later run
                except Exception as exc:
                    print(f"[warn] New Advent lookup failed for {d}: {exc}", file=sys.stderr)
            # Orthodox lives (OCA) for those saints, looked up once per day
            # (again if New Advent was re-matched).
            if liturgical.get("newadvent") is not None and (not oca.has(d) or rematched):
                try:
                    oca.update(d, liturgical)
                except OCABudgetExhausted:
                    window_complete = False
                except Exception as exc:
                    print(f"[warn] OCA lives lookup failed for {d}: {exc}", file=sys.stderr)

        if events_ok:
            events = events_by_date.get(d.isoformat(), [])
        else:
            events = (existing or {}).get("events") or []

        day_data = {
            "date": d.isoformat(),
            "liturgical": liturgical,
            "events": events,
        }
        day_file_path(d).write_text(json.dumps(day_data, indent=2))

    newadvent.save()
    oca.save()
    return now_iso, any_liturgical_ok, events_ok, window_complete


def prune_old_day_files(window_start: date, window_end: date):
    for path in DAYS_DIR.glob("*.json"):
        try:
            d = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if d < window_start or d > window_end:
            path.unlink()


def main():
    DAYS_DIR.mkdir(parents=True, exist_ok=True)

    today = today_central()
    window_start = today - timedelta(days=WINDOW_BACK_DAYS)
    window_end = today + timedelta(days=WINDOW_FORWARD_DAYS)

    prior_index = load_json(DATA_DIR / "index.json") or {}
    prior_last_successful = prior_index.get("last_successful", {})
    prior_links = prior_index.get("links", {})

    now_iso, liturgical_ok, events_ok, window_complete = build_day_files(window_start, window_end)
    prune_old_day_files(window_start, window_end)

    try:
        bulletin = fetch_latest_bulletin()
        bulletin_ok = True
    except Exception as exc:
        print(f"[warn] bulletin fetch failed: {exc}", file=sys.stderr)
        bulletin = None
        bulletin_ok = False

    try:
        fasting_calendar_link = fetch_fasting_calendar_link()
        fasting_calendar_ok = True
    except Exception as exc:
        print(f"[warn] fasting calendar link fetch failed: {exc}", file=sys.stderr)
        fasting_calendar_link = None
        fasting_calendar_ok = False

    try:
        fasting_ok = build_fasting_calendar(today)
    except Exception as exc:
        print(f"[warn] fasting calendar build failed: {exc}", file=sys.stderr)
        fasting_ok = False

    links = dict(STATIC_LINKS)
    links["bulletin_pdf"] = (bulletin or {}).get("url") or prior_links.get("bulletin_pdf")
    links["bulletin_posted"] = (bulletin or {}).get("posted") or prior_links.get("bulletin_posted")
    links["fasting_calendar_pdf"] = fasting_calendar_link or prior_links.get("fasting_calendar_pdf")

    prayer_list = prior_index.get("prayer_list")
    prayer_list_ok = False
    if links["bulletin_pdf"]:
        try:
            prayer_list = fetch_prayer_list(links["bulletin_pdf"])
            prayer_list_ok = True
        except Exception as exc:
            print(f"[warn] prayer list extraction failed: {exc}", file=sys.stderr)

    last_successful = dict(prior_last_successful)
    if liturgical_ok:
        last_successful["liturgical"] = now_iso
    if events_ok:
        last_successful["parish_events"] = now_iso
    if bulletin_ok:
        last_successful["bulletin"] = now_iso
    if fasting_calendar_ok:
        last_successful["fasting_calendar"] = now_iso
    if prayer_list_ok:
        last_successful["prayer_list"] = now_iso
    if fasting_ok:
        last_successful["fasting_data"] = now_iso

    available_dates = sorted(p.stem for p in DAYS_DIR.glob("*.json"))

    index = {
        "generated_at": now_iso,
        "today": today.isoformat(),
        "available_dates": available_dates,
        "last_successful": last_successful,
        "links": links,
        "prayer_list": prayer_list,
        "status": {
            "liturgical_ok": liturgical_ok,
            "parish_events_ok": events_ok,
            "bulletin_ok": bulletin_ok,
            "fasting_calendar_ok": fasting_calendar_ok,
            "prayer_list_ok": prayer_list_ok,
            "fasting_data_ok": fasting_ok,
            # False while the ~6-month window is still being backfilled; makes
            # the 2-hourly health-check run keep going until it's complete.
            "window_complete": window_complete,
        },
    }
    (DATA_DIR / "index.json").write_text(json.dumps(index, indent=2))

    print(f"Wrote {len(available_dates)} day files. Today: {today.isoformat()}")
    print(f"liturgical_ok={liturgical_ok} parish_events_ok={events_ok} "
          f"bulletin_ok={bulletin_ok} fasting_calendar_ok={fasting_calendar_ok} "
          f"prayer_list_ok={prayer_list_ok} fasting_data_ok={fasting_ok}")


if __name__ == "__main__":
    main()
