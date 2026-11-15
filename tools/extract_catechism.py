"""Extract the parish Adult Catechism PDF into data/catechism/lessons.json.

Usage: python tools/extract_catechism.py path/to/adult-catechism.pdf data/catechism/lessons.json
Re-run only when the parish issues a new revision of the handout (needs pdfplumber).
"""
import json, re, sys
import pdfplumber

SRC = sys.argv[1]; OUT = sys.argv[2]
ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00ad"), None)
SMALL = {"of","and","the","a","an","in","on","to","for","or","by","with","at","from","as","our"}

def title_case(s):
    out = []
    for i, w in enumerate(s.split()):
        def cap(part):
            m = re.match(r"^([(\[\"“]*)(.*)$", part); pre, core = m.groups()
            low = core.lower()
            if i and low.strip(",.:;)") in SMALL: return pre + low
            return pre + low[:1].upper() + low[1:]
        out.append("-".join(cap(p) for p in w.split("-")))
    return " ".join(out)

def font(c): return c["fontname"].split("+")[-1]
def visible(chars): return [c for c in chars if c["text"].strip() and c["text"].translate(ZW)]

def page_lines(pg):
    lines = pg.extract_text_lines(return_chars=True, keep_blank_chars=False)
    out = []
    for l in lines:
        vis = visible(l["chars"])
        if not vis: continue
        sizes = [round(c["size"], 1) for c in vis]
        out.append({"top": l["top"], "x0": vis[0]["x0"], "x1": vis[-1]["x1"], "chars": l["chars"],
                    "size": max(sizes, key=sizes.count), "maxsize": max(sizes),
                    "bold": all("Bold" in font(c) for c in vis if c["text"].strip() not in "•o"),
                    "text": l["text"].translate(ZW).strip()})
    return out

def text_with_sups(line, sups):
    """Line text with superscript footnote refs inserted as [^n] after the char they follow."""
    text = line["text"]
    if not sups: return re.sub(r"\s+", " ", text).strip()
    vis = [c for c in line["chars"] if c["text"].strip() and c["text"].translate(ZW)]
    out = []; vi = 0; pending = sorted(sups, key=lambda s: s["x0"])
    for ch in text:
        if ch.strip():
            while pending and vi < len(vis) and pending[0]["x0"] <= vis[vi]["x0"]:
                t = pending.pop(0)["text"]
                out.append(t if t.isalpha() else "[^" + t + "]")
            vi += 1
        out.append(ch)
    out += [p["text"] if p["text"].isalpha() else "[^" + p["text"] + "]" for p in pending]
    t = re.sub(r"\s+", " ", "".join(out)).strip()
    t = re.sub(r"\s+(\[\^\d+\])", r"\1 ", t)          # ref belongs to the preceding word
    t = re.sub(r"(\d) (st|nd|rd|th)\b", r"\1\2", t)
    return re.sub(r"\s+", " ", t).strip()

RIGHT_MARGIN = 540
ABBR_END = re.compile(r"\b(p|pp|ch|cf|St|vol|vols|Vol|v|vv|ed|e\.g|i\.e|Ibid|no|No)\.$")
END_RE = re.compile(r"[.?!:;)](\[\^\d+\])?[”\"’')\]]*$")

def main():
    pdf = pdfplumber.open(SRC)
    lessons = []; cur = None; last = None  # last = {"block":..., "line":...}
    footnotes_pending = []
    def new_block(kind, text, level=None):
        nonlocal last
        b = {"type": kind, "text": text}
        if level: b["level"] = level
        cur["blocks"].append(b); return b
    for pn, pg in enumerate(pdf.pages):
        lines = page_lines(pg)
        # superscript refs: small digit-only lines sitting just below a body line
        body = [l for l in lines if l["size"] >= 11.5]
        sup_lines = [l for l in lines if l["maxsize"] < 8.5 and (re.fullmatch(r"st|nd|rd|th", l["text"]) or (re.fullmatch(r"[\d\s]+", l["text"]) and l["x0"] > 80))]
        fn_lines = [l for l in lines if l not in body and l not in sup_lines]
        sup_for = {}
        for s in sup_lines:
            for c in visible(s["chars"]):
                host = max((b for b in body if b["top"] <= c["top"] <= b["top"] + 10), key=lambda b: b["top"], default=None)
                if host is None: print("orphan sup", pn, c["text"], file=sys.stderr); continue
                sup_for.setdefault(id(host), []).append(c)
        # merge adjacent sup digits (e.g. "1","2" separate; "1","0" adjacent -> 10)
        for k, cs in sup_for.items():
            cs.sort(key=lambda c: c["x0"]); merged = []
            # ordinal suffixes join the preceding word; mark with \x00 so text_with_sups inserts them raw
            for c in cs:
                if merged and c["x0"] - merged[-1]["x1"] < 0.8:
                    merged[-1] = {"x0": merged[-1]["x0"], "x1": c["x1"], "text": merged[-1]["text"] + c["text"]}
                else: merged.append({"x0": c["x0"], "x1": c["x1"], "text": c["text"]})
            sup_for[k] = merged
        # footnotes on this page
        if fn_lines:
            markers = [l for l in fn_lines if re.fullmatch(r"\d+", l["text"]) and l["maxsize"] < 9.5]
            texts = [l for l in fn_lines if l not in markers]
            starts = {}
            for m in markers:
                near = min(texts, key=lambda t: abs(t["top"] - m["top"]))
                starts[id(near)] = m["text"]
            for t in texts:
                txt = t["text"]
                if id(t) in starts or not footnotes_pending:
                    footnotes_pending.append({"n": int(starts.get(id(t), 0)), "text": txt})
                else:
                    prev = footnotes_pending[-1]["text"]
                    sep = "" if re.search(r"[A-Za-z]-$", prev) else " "
                    footnotes_pending[-1]["text"] = prev + sep + txt
        for bi, l in enumerate(body):
            txt = text_with_sups(l, sup_for.get(id(l), []))
            if re.fullmatch(r"\d{1,3}", txt): continue  # page number
            if l["size"] >= 13.5:
                if txt.upper().startswith("LESSON"):
                    if cur: lessons.append(cur)
                    m = re.match(r"LESSON\s*(\d+)\s*[:.\-–]?\s*(.*)", txt, re.I)
                    if not m: print("BAD", repr(txt), file=sys.stderr); continue
                    cur = {"number": int(m.group(1)), "id": m.group(1).zfill(2), "raw_title": m.group(2),
                           "synopsis": "", "blocks": [], "footnotes": [], "page_start": pn + 1}
                    last = {"kind": "title"}
                    continue
                if last and last["kind"] == "title":
                    cur["raw_title"] += " " + txt; continue
            if cur is None: continue
            is_bullet = False; level = None
            first = visible(l["chars"])[0]
            if first["text"] == "•": level = 1
            elif first["text"] == "o" and "Courier" in font(first): level = 2
            elif first["text"] in "▪■§": level = 3
            if level:
                is_bullet = True
                txt = re.sub(r"^[•o▪■§]\s*", "", txt)
            pl = last.get("line") if last else None
            prev_ended = bool(pl) and (last.get("label") or (not ABBR_END.search(pl["text"]) and bool(END_RE.search(pl["text"]))))
            # A short unpunctuated line right before a bullet is a label ("Aspects of Holy Tradition").
            nxt = body[bi + 1] if bi + 1 < len(body) else None
            is_label = (not level and nxt is not None and visible(nxt["chars"])[0]["text"] in "•o▪"
                        and not END_RE.search(txt) and l["x1"] < 420 and re.match(r"[A-Z“\[]", txt)
                        and not (pl and re.search(r"\b(the|of|and|a|an|to|in|for|with|by|or|our|St)$", pl["text"]))
                        and (prev_ended or not pl or (last and last["kind"] == "heading") or pl["x1"] < 480))
            if (not is_label and not level and nxt is not None and l["x1"] < 420 and not END_RE.search(txt)
                    and re.match(r"[A-Z]", txt) and (prev_ended or (last and last["kind"] == "heading")
                                                     or (last and last["kind"] == "bullet" and l["x0"] < last.get("start_x0", 0) - 10))
                    and nxt["x0"] < 76 and re.match(r"[A-Z“]", nxt["text"])):
                is_label = True  # a standalone short title line between blocks
            if txt.lower().startswith("synopsis:"):
                cur["synopsis"] = txt.split(":", 1)[1].strip(); last = {"kind": "synopsis", "line": l}; continue
            if l["bold"] and not is_bullet:
                if last and last["kind"] == "heading" and not END_RE.search(last["block"]["text"]) and l["top"] - last["line"]["top"] < 20 and last["line"]["page"] == pn:
                    last["block"]["text"] += " " + txt
                else:
                    b = new_block("heading", txt)
                    last = {"kind": "heading", "block": b}
                last["line"] = dict(l, text=txt, page=pn); continue
            if is_bullet:
                b = new_block("bullet", txt, level); last = {"kind": "bullet", "block": b, "start_x0": l["x0"], "line": dict(l, text=txt, page=pn)}; continue
            # plain line: continuation or new paragraph
            cont = last and last["kind"] in ("bullet", "para", "synopsis") and (not prev_ended or re.match(r"[a-z]", txt) or (last["kind"] == "bullet" and l["x0"] >= 85))
            heads = [b for b in cur["blocks"] if b["type"] == "heading"]
            if cont and heads and "BIBLIOGRAPHY" in heads[-1]["text"].upper() and l["x0"] < 76 and re.match(r"[A-Z][\w’'-]+, [A-Z]", txt):
                cont = False  # each bibliography entry starts "Surname, Given"
            elif heads and "BIBLIOGRAPHY" in heads[-1]["text"].upper() and last and last["kind"] == "para":
                cont = True
            if is_label: cont = False
            if cont and last["kind"] == "bullet" and l["x0"] < last.get("start_x0", 0) - 10 and not re.match(r"[a-z]", txt):
                cont = False  # dedented below the bullet: a new paragraph / label line
            if cont:
                if last["kind"] == "synopsis":
                    prev = cur["synopsis"]
                    cur["synopsis"] = prev + ("" if re.search(r"[A-Za-z]-$", prev) else " ") + txt
                else:
                    prev = last["block"]["text"]
                    last["block"]["text"] = prev + ("" if re.search(r"[A-Za-z]-$", prev) else " ") + txt
                last["line"] = dict(l, text=txt, page=pn)
            else:
                b = new_block("para", txt); last = {"kind": "para", "block": b, "label": is_label, "line": dict(l, text=txt, page=pn)}
        # attach footnotes collected so far to the current lesson
        if cur and footnotes_pending:
            cur["footnotes"].extend(footnotes_pending); footnotes_pending = []
    lessons.append(cur)
    for L in lessons:
        L["title"] = title_case(L.pop("raw_title"))
        for b in L["blocks"]:
            b["text"] = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s+", " ", b["text"])).strip()
            b["text"] = re.sub(r"\b(\d) ?(st|nd|rd|th) ?(?=[A-Z])", r"\1\2 ", b["text"])
            b["text"] = re.sub(r",(?=[A-Z][a-z])", ", ", b["text"])
        for f in L["footnotes"]:
            f["text"] = re.sub(r"\s+", " ", f["text"]).strip()
    json.dump({"source": "Adult Catechism – All Lessons (parish handout, rev 05)", "lessons": lessons},
              open(OUT, "w"), ensure_ascii=False, indent=1)
    for L in lessons:
        print(L["id"], L["title"], "| blocks", len(L["blocks"]), "| fn", len(L["footnotes"]), "| p", L["page_start"])

main()
