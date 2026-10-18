"""Help-desk grounding for the optional model conversation (§16, issue #1382).

In help-desk mode the host adds two things to the system message: excerpts
of the caller handbook, picked per request by a standard-library keyword
score, and node facts. Both are bounded by fixed byte caps, so a request's
reservation stays predictable. The model calls no tools: the host supplies
the facts, including `stats` through `run_query` when the bot holds that
grant.

The handbook ships as package data (`handbook.json`), generated from
`docs/NetBBS-User-Handbook.md` by `scripts/build_helpdesk_handbook.py`; a
test fails when the copy is stale. Nothing here reads `docs/` at runtime.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from importlib import resources
import json
import math
import re

from netbbs.bots.registry import BotError
from netbbs.rendering import sanitize_text

HANDBOOK_SOURCE = "docs/NetBBS-User-Handbook.md"
CORPUS_FILE = "handbook.json"
# Excerpts and facts together stay a few kilobytes: the ledger reserves
# input bytes as tokens, and small local models have small context windows.
EXCERPT_BYTES = 4000
FACTS_BYTES = 1500
MAX_EXCERPTS = 3
# A weaker match is only noise beside a strong one.
RELATIVE_FLOOR = 0.35
RESOURCE_DESCRIPTION_CHARS = 60
TITLE_WEIGHT = 1.5
CHAPTER_WEIGHT = .5

RULES = (
    "Answer only from the node facts and handbook excerpts below. Name a menu, key or command only if it "
    "appears in them; never invent one. If they do not answer the question, say you do not know and suggest "
    "[?] Help on the main menu or asking the SysOp. Keys in [brackets] are pressed on their own."
)
# The most grounding one request carries, for the SysOp's budget help: the
# ledger reserves these bytes as tokens.
GROUNDING_BYTES = EXCERPT_BYTES + FACTS_BYTES + len(RULES) + 64


# -- the shipped corpus ------------------------------------------------------

_HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")
_ALERT = re.compile(r"^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_KBD = re.compile(r"<kbd>(.*?)</kbd>")
_HTML_BLOCK = re.compile(r"</?(details|summary)>")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _inline(text: str) -> str:
    text = _KBD.sub(lambda m: "[" + m.group(1) + "]", text)
    text = _HTML_BLOCK.sub("", text)
    text = _LINK.sub(r"\1", text)
    return text.replace("**", "").replace("`", "")


def _plain_lines(lines: list[str]) -> str:
    out = []
    contents = False
    for line in lines:
        line = line.rstrip()
        # A chapter's "In this chapter:" list only repeats the headings.
        contents = (contents or line.startswith("In this chapter:")) and bool(line)
        if contents or line.startswith("```"):
            continue
        alert = _ALERT.match(line)
        if alert:
            out.append(alert.group(1).capitalize() + ":")
            continue
        if line.startswith(">"):
            line = line[1:].lstrip()
        if line.lstrip().startswith("|"):
            if _TABLE_RULE.match(line.strip()):
                continue
            cells = [_inline(c).strip() for c in line.strip().strip("|").split("|")]
            cells = [c for c in cells if c]
            line = ": ".join(cells) if len(cells) == 2 else "; ".join(cells)
        else:
            line = _inline(line)
        if line.strip() or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def build_corpus(markdown: str) -> list[dict]:
    """The handbook as heading-bounded plain-text sections. Each carries its
    own heading (`title`), the `##` chapter it belongs to and its text."""
    sections = []
    chapter = title = None
    body: list[str] = []

    def flush():
        text = _plain_lines(body)
        if title is not None and text:
            sections.append({"chapter": chapter, "title": title, "text": text})

    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            flush()
            body = []
            level, title = len(heading.group(1)), _inline(heading.group(2))
            if level <= 2:
                chapter = title
            continue
        body.append(line)
    flush()
    return sections


def corpus_document(markdown: str) -> str:
    """The exact text of the shipped `handbook.json`."""
    return json.dumps({"source": HANDBOOK_SOURCE, "sections": build_corpus(markdown)},
                      ensure_ascii=False, indent=1) + "\n"


@lru_cache(maxsize=1)
def load_corpus() -> tuple[dict, ...]:
    raw = resources.files("netbbs.bots").joinpath(CORPUS_FILE).read_text(encoding="utf-8")
    return tuple(json.loads(raw)["sections"])


# -- retrieval -----------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be but by can do does for from get go got had has have how i if in into is it its me "
    "my of on or so that the then there this to up was what when where which who why will with you your "
    "please baud bot".split()
)


# British spellings and one-word forms callers type for the handbook's words.
_SPELLING = {"colour": "color", "colours": "colors", "login": "log", "logon": "log", "logout": "log",
             "logoff": "log", "signup": "register", "favourite": "favorite"}


def _stem(word: str) -> str:
    word = _SPELLING.get(word, word)
    for suffix, keep in (("ing", 5), ("ies", 4), ("ed", 4)):
        if len(word) > keep and word.endswith(suffix):
            word = word[:-len(suffix)] + ("y" if suffix == "ies" else "")
            # logging -> logg -> log, stopped -> stopp -> stop
            if len(word) > 2 and word[-1] == word[-2] and word[-1] not in "aeiouls":
                word = word[:-1]
            return word
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def terms(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP]


class _Index:
    def __init__(self, sections):
        self.sections = sections
        self.docs, self.headings = [], []
        for s in sections:
            # A heading names what a section is about; it weighs more than
            # a passing mention in another section's text.
            counts = Counter(terms(s["text"]))
            self.docs.append(counts)
            title = set(terms(s["title"]))
            self.headings.append((title, set(terms(s["chapter"] or "")) - title))
        self.lengths = [sum(c.values()) for c in self.docs]
        self.average = sum(self.lengths) / max(1, len(self.lengths))
        df = Counter(word for i, c in enumerate(self.docs) for word in set(c) | self.headings[i][0])
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - f + .5) / (f + .5)) for w, f in df.items()}

    def scores(self, query):
        wanted = set(terms(query))
        k, b = 1.2, .75
        result = []
        for counts, length, (title, chapter) in zip(self.docs, self.lengths, self.headings):
            score = 0.0
            for word in wanted:
                if word not in self.idf:
                    continue
                tf = counts.get(word, 0)
                score += self.idf[word] * tf * (k + 1) / (tf + k * (1 - b + b * length / self.average))
                # A heading names what a section is about: it counts more
                # than any number of passing mentions in the text.
                if word in title:
                    score += TITLE_WEIGHT * self.idf[word]
                elif word in chapter:
                    score += CHAPTER_WEIGHT * self.idf[word]
            result.append(score)
        return result


@lru_cache(maxsize=1)
def _shipped_index() -> _Index:
    return _Index(load_corpus())


def excerpt_text(section) -> str:
    return f"[{section['title']}]\n{section['text']}"


def retrieve(prompt: str, sections=None, *, limit=MAX_EXCERPTS, cap=EXCERPT_BYTES) -> list[dict]:
    """The best two or three sections for `prompt`, best first, whose
    excerpts fit in `cap` bytes together. A best section larger than the
    cap is cut to it; no match at all gives an empty list."""
    if sections is None:
        sections, index = load_corpus(), _shipped_index()
    else:
        sections = tuple(sections)
        index = _Index(sections)
    ranked = sorted(((score, -i) for i, score in enumerate(index.scores(prompt)) if score > 0), reverse=True)
    if not ranked:
        return []
    floor = ranked[0][0] * RELATIVE_FLOOR
    picked, used = [], 0
    for score, negative in ranked[:limit]:
        if score < floor:
            break
        section = sections[-negative]
        size = len(excerpt_text(section).encode("utf-8")) + 2
        if used + size > cap:
            if picked:
                continue
            text = excerpt_text(section).encode("utf-8")[:cap - 2].decode("utf-8", "ignore")
            section = {**section, "text": text.split("\n", 1)[1] if "\n" in text else ""}
            size = cap
        picked.append(section)
        used += size
    return picked


def source_tag(title: str, reply_chars: int) -> str:
    """The host's citation and hint (§16, issue #1382, Decision 3). A
    heading too long for a short reply cap gives way to a generic tag."""
    tag = f"(Handbook: {title}. May be wrong; ? for help)"
    if reply_chars - len(tag) - 1 < 24:
        tag = "(From the handbook. May be wrong; ? for help)"
    return tag


def with_source(text: str, title: str | None, reply_chars: int) -> tuple[str, str]:
    """(the line shown, the answer as cut to make room for the tag). With
    no excerpt sent there is nothing to cite, and the answer stands alone."""
    if title is None:
        return text[:reply_chars], text[:reply_chars]
    tag = source_tag(title, reply_chars)
    answer = text[:reply_chars - len(tag) - 1].rstrip()
    return answer + " " + tag, answer


# -- node facts ----------------------------------------------------------------

def _clean(text) -> str:
    return " ".join(sanitize_text(str(text or "")).split())


def _sysops(db) -> list[str]:
    from netbbs.auth.users import is_usable_sysop, list_users
    return sorted((u.username for u in list_users(db) if is_usable_sysop(u)), key=str.lower)


def _features(db, *, browser_links: bool) -> str:
    from netbbs.doors.registry import list_doors
    from netbbs.ftn.networks import list_networks
    from netbbs.mrc.settings import load_mrc_settings
    flags = (("MRC inter-BBS chat", load_mrc_settings(db).enabled),
             ("browser links for file transfers", browser_links),
             ("door games", bool(list_doors(db))),
             ("FTN networks and netmail", any(n.enabled for n in list_networks(db))))
    have = [name for name, on in flags if on]
    lack = [name for name, on in flags if not on]
    parts = []
    if have:
        parts.append("This node has " + ", ".join(have) + ".")
    if lack:
        parts.append("It has no " + ", ".join(lack) + "; handbook text about them does not apply here.")
    return " ".join(parts)


def resources_for(db, caller, *, private: bool):
    """Which boards and file areas the grounding may name (Decision 4).

    A public answer is read by the whole channel, so it names only
    resources `public_sources` allows. A private answer to a local caller
    names what that caller can see; a Link caller has no local account to
    scope by and always gets the public set. Hidden Communities stay out
    for everyone but a SysOp."""
    from netbbs.auth.users import SYSOP_LEVEL, get_user_by_id
    from netbbs.bots.sources import public_sources
    from netbbs.communities import list_communities
    from netbbs.permissions import meets_level
    user = get_user_by_id(db, caller["id"]) if private and isinstance(caller["id"], int) else None
    if user is not None:
        from netbbs.net.board_flow import visible_boards
        from netbbs.net.file_flow import visible_areas
        boards = visible_boards(db, user, community_id=None, community_scoped=False)
        areas = visible_areas(db, user)
    else:
        boards, areas = public_sources(db, kind="boards"), public_sources(db, kind="areas")
    if user is None or not meets_level(user, SYSOP_LEVEL):
        hidden = {c.id for c in list_communities(db) if c.hidden}
        boards = [b for b in boards if b.community_id not in hidden]
        areas = [a for a in areas if a.community_id not in hidden]
    return boards, areas


def _resource_line(label: str, items, room: int) -> str:
    if not items:
        return f"{label}: none."
    line = label + ":"
    for count, item in enumerate(items):
        name = _clean(item.name)
        description = _clean(item.description)[:RESOURCE_DESCRIPTION_CHARS]
        entry = f" {name} ({description})" if description else f" {name}"
        # Room is kept for the "and N more" a later item may need.
        tail = "." if count == len(items) - 1 else "; and 999 more."
        if len((line + entry + tail).encode("utf-8")) > room:
            return line.rstrip(";") + f" and {len(items) - count} more."
        line += entry + ";"
    return line.rstrip(";") + "."


def node_facts(db, bot_id, channel_name, caller, *, private, online=None, uptime_seconds=None,
               browser_links=False) -> str:
    from netbbs.config import get_node_display_name
    from netbbs.link.node_page import get_node_tagline
    lines = [f"Node name: {_clean(get_node_display_name(db))}."]
    tagline = get_node_tagline(db)
    if tagline:
        lines.append(f"Tagline: {_clean(tagline)}")
    sysops = _sysops(db)
    if sysops:
        lines.append("Run by SysOp " + ", ".join(_clean(s) for s in sysops) + ". Write to them with E-mail To: sysop.")
    lines.append(_features(db, browser_links=browser_links))
    if online is not None and uptime_seconds is not None:
        from netbbs.bots.queries import run_query
        try:
            stats = run_query(db, bot_id, channel_name, "stats", None, caller=caller, online=online,
                              uptime_seconds=uptime_seconds)
            lines.append("Right now: " + stats["text"])
        except BotError:
            pass  # Not granted, or the caller check refused it: no stats.
    head = "\n".join(lines)
    boards, areas = resources_for(db, caller, private=private)
    room = max(0, FACTS_BYTES - len(head.encode("utf-8")) - 2) // 2
    facts = "\n".join([head, _resource_line("Message boards", boards, room),
                       _resource_line("File areas", areas, room)])
    return facts.encode("utf-8")[:FACTS_BYTES].decode("utf-8", "ignore")


def grounding(db, bot_id, channel_name, caller, prompt, *, private, online=None, uptime_seconds=None,
              browser_links=False):
    """(system-message text, heading of the best excerpt or None)."""
    facts = node_facts(db, bot_id, channel_name, caller, private=private, online=online,
                       uptime_seconds=uptime_seconds, browser_links=browser_links)
    excerpts = retrieve(prompt)
    parts = [RULES, "Node facts:\n" + facts]
    if excerpts:
        parts.append("Handbook excerpts:\n" + "\n\n".join(excerpt_text(s) for s in excerpts))
    else:
        parts.append("Handbook excerpts: none matched this question.")
    return "\n\n".join(parts), (excerpts[0]["title"] if excerpts else None)
