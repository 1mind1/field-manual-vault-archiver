#!/usr/bin/env python3
"""Independent T.REX Field Manual Vault archiver. Python 3.10+, stdlib only.

Incremental, checkpointed catalog discovery; bounded metadata rechecks;
conservative request pacing; estimated storage; offline index; SHA-256
duplicate auditing; and resumable downloads. Not affiliated with T.REX ARMS.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import gzip
import html as html_escape_module
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

BASE = "https://www.trex-arms.com"
VERSION = "5.0.2-rc3"
VAULT_PATH = "/field-manual-vault"
VAULT = BASE + VAULT_PATH
SITE_HOST = "www.trex-arms.com"
FILE_HOST = "files.trex-arms.com"
HEADERS = {
    "User-Agent": "FieldManualVaultArchiver/5.0 (unofficial personal archiver; polite requests)",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Every category is created as its own directory. Only TITLE matches count;
# this avoids the site's broad, full-text searches ("first aid" matches 2,983 manuals).
# Add custom categories, search tokens, and title patterns as needed.
TOPICS = {
    "First Aid": {
        "queries": ["aid", "casualty", "medic", "lifesaver", "trauma", "triage"],
        "title": r"first[ -]aid|self[ -]aid|buddy[ -]aid|casualt|medic|lifesaver|trauma|triage|emergency care|wound",
    },
    "Survival": {
        "queries": ["survival", "surviv", "SERE", "wilderness", "fieldcraft", "cold", "jungle"],
        "title": r"surviv|\bSERE\b|wilderness|fieldcraft|evasion|cold[ -]weather|jungle|arctic|desert survival",
    },
    "Land Navigation": {
        "queries": ["navigation", "map", "compass", "orienteering", "topographic"],
        "title": r"navigat|map reading|compass|orienteer|topograph|terrain association",
    },
    "Marksmanship": {
        "queries": ["marksmanship", "sniper", "shooting", "rifle", "pistol", "carbine", "gunnery"],
        "title": r"marksman|sniper|shoot|\brifle\b|\bpistol\b|\bcarbine\b|small[ -]arms|weapons training|gunnery",
    },
    "Patrolling": {
        "queries": ["patrol", "scouting", "reconnaissance", "ranger", "infantry"],
        "title": r"patrol|scout|reconnaissance|ranger handbook|infantry",
    },
}

# The website's official functional classification. In 'functions' mode, these
# group entries under the site's exact subject headings, not guessed subjects.
FUNCTIONS = {
    "Sustainment": "sustainment",
    "Comms Signal": "comms-signal",
    "Small Unit Tactics": "small-unit-tactics",
    "Engineering": "engineering",
    "Fires": "fires",
    "Medical TCCC": "medical-tccc",
    "Leadership Training": "leadership-training",
    "Doctrine Philosophy": "doctrine-philosophy",
    "Recon Intel": "recon-intel",
    "Aviation": "aviation",
    "Marksmanship Weapons": "marksmanship-weapons",
    "CBRN": "cbrn",
    "Fieldcraft Survival": "fieldcraft-survival",
    "Other": "other",
    "Uncategorized": "uncategorized",
}

# Customized browse hierarchy. Keep the original site Function in the CSV as well.
PRIMARY_NAMES = {
    "Sustainment": "Logistics & Sustainment",
    "Comms Signal": "Communications & Signals",
    "Small Unit Tactics": "Infantry & Small Unit Tactics",
    "Engineering": "Engineering & Construction",
    "Fires": "Artillery & Fire Support",
    "Medical TCCC": "Medical & First Aid",
    "Leadership Training": "Leadership & Training",
    "Doctrine Philosophy": "Doctrine & Military History",
    "Recon Intel": "Reconnaissance & Intelligence",
    "Aviation": "Aviation",
    "Marksmanship Weapons": "Marksmanship & Weapons",
    "CBRN": "CBRN & Protective Measures",
    "Fieldcraft Survival": "Survival & Fieldcraft",
    "Other": "Other & Technical Reference",
    "Uncategorized": "Uncategorized & Review",
}

# Rules are deliberately conservative; anything unclear goes to General Reference.
SUBTOPICS = {
    "Medical TCCC": [
        (r"first[ -]aid|buddy[ -]aid|self[ -]aid|lifesaver", "First Aid"),
        (r"casualty|trauma|wound|medical emergency|tactical combat casualty", "Casualty & Trauma Care"),
        (r"evacuat|patient transport", "Medical Evacuation"),
        (r"hygiene|sanitat|preventive medicine|field health", "Field Hygiene & Prevention"),
        (r"dental|nursing|surgical|hospital|medical logistics", "Clinical & Medical Support"),
    ],
    "Fieldcraft Survival": [
        (r"land navigat|map reading|orienteer|compass|topograph", "Land Navigation & Maps"),
        (r"mountain|climb|rope|alpine", "Mountaineering"),
        (r"cold[ -]weather|arctic|winter|snow", "Cold Weather"),
        (r"jungle|tropic|desert", "Environment-Specific Fieldcraft"),
        (r"surviv|sere|evasion|watermanship", "Survival & Evasion"),
    ],
    "Small Unit Tactics": [
        (r"patrol|ranger|scouting", "Patrolling & Scouting"),
        (r"infantry|rifle squad|rifle platoon", "Infantry"),
        (r"soldier combat skills|warrior ethos|individual soldier", "Soldier Combat Skills"),
        (r"urban|mout", "Urban Operations"),
    ],
    "Comms Signal": [
        (r"radio|antenna|frequency|tactical communications", "Radios & Antennas"),
        (r"signal|message|procedures", "Signals & Procedures"),
        (r"network|computer|information system|cyber", "Networks & Information Systems"),
    ],
    "Marksmanship Weapons": [
        (r"marksman|snip|shoot|qualification|range", "Marksmanship"),
        (r"rifle|pistol|carbine|small arms", "Small Arms"),
        (r"gunnery|machine gun|mortar", "Gunnery & Crew-Served Systems"),
    ],
    "Recon Intel": [
        (r"recon|scout|surveillance", "Reconnaissance & Surveillance"),
        (r"intelligen|analysis|information collection", "Intelligence & Analysis"),
    ],
    "Engineering": [
        (r"construction|bridge|road|building", "Construction & Infrastructure"),
        (r"survey|geospatial|terrain|mapping", "Surveying & Mapping"),
    ],
    "Sustainment": [
        (r"transport|vehicle movement", "Transportation"),
        (r"maintenance|repair|troubleshoot", "Maintenance & Repair"),
        (r"supply|logistic|storage|distribution", "Supply & Logistics"),
        (r"food|water|ration|field feeding", "Food & Water"),
    ],
    "Leadership Training": [
        (r"leadership|leader|command", "Leadership"),
        (r"training|instruction|school|handbook", "Training & Education"),
        (r"fitness|physical readiness|health", "Fitness & Readiness"),
    ],
    "Doctrine Philosophy": [
        (r"history|historical|lessons learned", "History & Lessons Learned"),
        (r"doctrine|principles|philosophy|warfighting", "Doctrine & Principles"),
    ],
    "Aviation": [
        (r"helicopter|rotary", "Helicopters"),
        (r"aircraft|flight|aviator", "Aircraft & Flight"),
        (r"maintenance|repair", "Aviation Maintenance"),
    ],
    "CBRN": [
        (r"decontamin|protect", "Protective Measures"),
        (r"chemical|biological|radiological|nuclear", "CBRN Reference"),
    ],
    "Fires": [(r"artillery|mortar|fires", "Field Artillery & Fire Support")],
}

TAG_PATTERNS = {
    "First Aid": r"first[ -]aid|casualty|lifesaver|triage",
    "Survival": r"surviv|sere|wilderness|evasion|fieldcraft",
    "Land Navigation": r"land navigat|map reading|compass|orienteer|topograph",
    "Marksmanship": r"marksman|sniper|shooting|rifle|pistol",
    "Patrolling": r"patrol|scout|reconnaissance",
    "Communications": r"signal|radio|communications|antenna",
    "Combat Skills": r"combat skills|warrior ethos|soldier skills",
    "Historic": r"historical|history|world war",
}


def category_for(function: str, title: str, collection: str = "") -> tuple[str, str, str]:
    primary = PRIMARY_NAMES.get(function, "Uncategorized & Review")
    # Land navigation is a practical browse category even though the vault
    # assigns most of these publications to Fieldcraft Survival.
    if function == "Fieldcraft Survival" and re.search(
            r"land navigat|map reading|orienteer|compass|topograph", title, re.IGNORECASE):
        primary = "Navigation & Terrain"
    topic = "General Reference"
    for pattern, label in SUBTOPICS.get(function, []):
        if re.search(pattern, title, re.IGNORECASE):
            topic = label
            break
    tags = [name for name, pattern in TAG_PATTERNS.items()
            if re.search(pattern, title, re.IGNORECASE)]
    if collection and "historic" in collection.lower() and "Historic" not in tags:
        tags.append("Historic")
    return primary, topic, "; ".join(tags)


# Small, deliberately sequential requests. Be considerate of site's bandwidth.
PAUSE_BETWEEN_HTML = 0.7
DEFAULT_REQUEST_INTERVAL = 0.55  # Combined interval across all metadata workers.
DEFAULT_WORKERS = 2
LISTING_TTL_SECONDS = 7 * 86400
METADATA_TTL_SECONDS = 30 * 86400
NEGATIVE_TTL_SECONDS = 7 * 86400
SIZE_TTL_SECONDS = 60 * 86400
# Snapshot advertised by the site in November 2026. This is a *reference*
# count, not evidence that the public listing API exposes every edition.
REFERENCE_PUBLISHED_TOTAL = 4215
MAX_HTML_BYTES = 16 * 1024 * 1024
MAX_SITEMAP_BYTES = 24 * 1024 * 1024
MAX_LISTING_PAGES = 180
RETRY_ATTEMPTS = 3
_html_lock = threading.Lock()
_html_next_time = 0.0


class PermanentMissingError(RuntimeError):
    """A source explicitly returned 404/410. Retrying immediately won't help."""


class CatalogCache:
    """SQLite cache shared across category combinations; no network access.

    All database writes happen on the main thread. Worker threads fetch HTML
    only, so the SQLite connection never needs cross-thread access.
    """

    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(path)
        self._pending_writes = 0
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (
              url TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS listings (
              category TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS unavailable (
              url TEXT PRIMARY KEY, http_code INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS local_hashes (
              relative_path TEXT PRIMARY KEY, file_size INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS listing_scan_state (
              scan_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
              updated_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS file_sizes (
              url TEXT PRIMARY KEY, bytes INTEGER NOT NULL,
              source TEXT NOT NULL, updated_at INTEGER NOT NULL);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    def _periodic_commit(self) -> None:
        # Avoid a filesystem journal flush for every imported manual or hash.
        # At most 63 completed records are lost on a power failure, and a
        # normal close/checkpoint commits everything.
        self._pending_writes += 1
        if self._pending_writes >= 64:
            self.db.commit()
            self._pending_writes = 0

    def metadata(self, url: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM metadata WHERE url=?", (url,)).fetchone()
        if row:
            try:
                data = json.loads(row[0])
                if isinstance(data, dict) and data.get("source_url") == url and (data.get("pdf_url") or data.get("epub_url")):
                    return data
            except ValueError:
                pass
        return None

    def put_metadata(self, row: dict, durable: bool = False) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO metadata (url,payload,updated_at) VALUES (?,?,?)",
            (row["source_url"], json.dumps(row, ensure_ascii=False), int(time.time())))
        if durable:
            # Site requests dominate runtime; a tiny commit per downloaded
            # detail record is worth it for crash-safe incremental resumes.
            self.db.commit()
            self._pending_writes = 0
        else:
            self._periodic_commit()

    def all_metadata(self) -> list[dict]:
        output = []
        for (payload,) in self.db.execute("SELECT payload FROM metadata ORDER BY url"):
            try:
                data = json.loads(payload)
                if isinstance(data, dict) and data.get("source_url"):
                    output.append(data)
            except ValueError:
                pass
        return output

    def repair_legacy_metadata(self) -> list[dict[str, str]]:
        """Repair previously cached numeric badges with *local* evidence only.

        For Function, an unambiguous saved official Function listing is evidence
        of a category. Without that evidence, leave Function blank rather than
        invent one. Collection/Status numeric badges are not reliable values;
        discard those labels, NOT the manual or its download URLs.

        Preserve updated_at: a local cleanup is not a successful source recheck.
        The operation is repeatable and leaves PDFs, EPUBs and saved files alone.
        """
        listing_memberships: dict[str, set[str]] = {}
        for key, payload in self.db.execute("SELECT category,payload FROM listings"):
            if not key.startswith("fn:"):
                continue
            function = next((name for name, slug in FUNCTIONS.items()
                             if key == "fn:" + slug), None)
            if function is None:
                continue
            try:
                pairs = json.loads(payload)
            except (ValueError, TypeError):
                continue
            if not isinstance(pairs, list):
                continue
            for pair in pairs:
                if (isinstance(pair, (list, tuple)) and pair and
                        isinstance(pair[0], str) and
                        normalized_detail(pair[0], BASE) == pair[0]):
                    listing_memberships.setdefault(pair[0], set()).add(function)

        repairs: list[dict[str, str]] = []
        for url, payload in list(self.db.execute("SELECT url,payload FROM metadata")):
            try:
                row = json.loads(payload)
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict) or row.get("source_url") != url:
                continue
            membership = listing_memberships.get(url, set())
            known_function = next(iter(membership)) if len(membership) == 1 else ""
            cleaned = repair_cached_metadata(row, known_function)
            if cleaned == row:
                continue
            for field in ("site_function", "collection", "status"):
                if cleaned.get(field) != row.get(field):
                    repairs.append({
                        "source_url": url, "field": field,
                        "previous_value": str(row.get(field) or ""),
                        "new_value": str(cleaned.get(field) or ""),
                    })
            # An UPDATE rather than put_metadata preserves the original
            # last-source-check timestamp used by incremental revalidation.
            self.db.execute("UPDATE metadata SET payload=? WHERE url=?",
                            (json.dumps(cleaned, ensure_ascii=False), url))
        if repairs:
            self.db.commit()
        return repairs

    def import_old_manifests(self, root: Path) -> int:
        """Adopt existing v2/v3 manifests without changing any downloaded files."""
        imported = 0
        for file in sorted(root.glob("trex_manifest_*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    continue
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    url = row.get("source_url", "")
                    if not normalized_detail(url, BASE) or self.metadata(url) is not None:
                        continue
                    if row.get("pdf_url") or row.get("epub_url"):
                        self.put_metadata(row)
                        imported += 1
            except (OSError, ValueError, sqlite3.Error) as exc:
                print(f"  WARNING: Could not import {file.name}: {exc}")
        return imported

    def get_listing(self, category: str, ttl: int = LISTING_TTL_SECONDS) -> list[tuple[str, str]] | None:
        row = self.db.execute("SELECT payload,updated_at FROM listings WHERE category=?", (category,)).fetchone()
        if row and time.time() - row[1] <= ttl:
            try:
                return [(url, label) for url, label in json.loads(row[0])
                        if normalized_detail(url, BASE) == url]
            except (ValueError, TypeError):
                pass
        return None

    def put_listing(self, category: str, pairs: list[tuple[str, str]]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO listings (category,payload,updated_at) VALUES (?,?,?)",
            (category, json.dumps(pairs, ensure_ascii=False), int(time.time())))
        self.db.commit()

    def listing_info(self, category: str) -> tuple[int, int] | None:
        row = self.db.execute("SELECT payload,updated_at FROM listings WHERE category=?", (category,)).fetchone()
        if row:
            try:
                return len(json.loads(row[0])), int(row[1])
            except (ValueError, TypeError):
                return None
        return None

    def get_scan_state(self, scan_key: str, params: dict[str, str]) -> dict | None:
        row = self.db.execute("SELECT payload FROM listing_scan_state WHERE scan_key=?", (scan_key,)).fetchone()
        if not row:
            return None
        try:
            state = json.loads(row[0])
            if (state.get("params") == params and isinstance(state.get("found"), dict)
                    and isinstance(state.get("next_url"), str)
                    and isinstance(state.get("pages"), int)):
                return state
        except (ValueError, TypeError, AttributeError):
            pass
        return None

    def put_scan_state(self, scan_key: str, state: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO listing_scan_state (scan_key,payload,updated_at) VALUES (?,?,?)",
            (scan_key, json.dumps(state, ensure_ascii=False), int(time.time())))
        # A partial scan must survive an interruption, even after one page.
        self.db.commit()

    def clear_scan_state(self, scan_key: str) -> None:
        self.db.execute("DELETE FROM listing_scan_state WHERE scan_key=?", (scan_key,))
        self.db.commit()

    def stale_metadata_urls(self, urls: set[str], age_seconds: int, limit: int) -> list[str]:
        if limit <= 0 or not urls:
            return []
        threshold = int(time.time()) - max(0, age_seconds)
        # Only query URLs in this run's selection; ordering is oldest-first.
        # This avoids a SQL IN clause with >999 placeholders on older SQLite.
        candidates = [(int(checked), url) for url, checked in self.db.execute(
            "SELECT url,updated_at FROM metadata WHERE updated_at<=? ORDER BY updated_at,url",
            (threshold,)) if url in urls]
        return [url for _, url in candidates[:limit]]

    def get_file_size(self, url: str) -> int | None:
        row = self.db.execute("SELECT bytes,updated_at FROM file_sizes WHERE url=?", (url,)).fetchone()
        if row and row[0] > 0 and time.time() - row[1] <= SIZE_TTL_SECONDS:
            return int(row[0])
        return None

    def put_file_size(self, url: str, size_bytes: int, source: str) -> None:
        if size_bytes <= 0:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO file_sizes (url,bytes,source,updated_at) VALUES (?,?,?,?)",
            (url, int(size_bytes), source, int(time.time())))
        self._periodic_commit()

    def negative_code(self, url: str) -> int | None:
        row = self.db.execute("SELECT http_code,updated_at FROM unavailable WHERE url=?", (url,)).fetchone()
        return row[0] if row and time.time() - row[1] <= NEGATIVE_TTL_SECONDS else None

    def put_negative(self, url: str, code: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO unavailable (url,http_code,updated_at) VALUES (?,?,?)",
            (url, code, int(time.time())))
        self.db.commit()

    def clear_negative(self, url: str) -> None:
        self.db.execute("DELETE FROM unavailable WHERE url=?", (url,))
        self.db.commit()

    def get_hash(self, path: Path, root: Path) -> str | None:
        try:
            st = path.stat()
            relative = path.relative_to(root).as_posix()
            row = self.db.execute("SELECT file_size,mtime_ns,sha256 FROM local_hashes WHERE relative_path=?", (relative,)).fetchone()
            if row and row[0] == st.st_size and row[1] == st.st_mtime_ns:
                return row[2]
        except (OSError, ValueError):
            pass
        return None

    def put_hash(self, path: Path, root: Path, digest: str) -> None:
        st = path.stat()
        relative = path.relative_to(root).as_posix()
        self.db.execute(
            "INSERT OR REPLACE INTO local_hashes (relative_path,file_size,mtime_ns,sha256) VALUES (?,?,?,?)",
            (relative, st.st_size, st.st_mtime_ns, digest))
        self._periodic_commit()


class PageParser(HTMLParser):
    """Extract anchors, page title, stripped strings from untrusted HTML."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._current_link: dict | None = None
        self._in_title = False
        self.title = ""
        self.strings: list[str] = []
        self._suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._suppressed += 1
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "a" and self._suppressed == 0:
            href = dict(attrs).get("href")
            if href:
                self._current_link = {"href": href, "pieces": []}

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
        if tag.lower() == "a" and self._current_link is not None:
            self.links.append((self._current_link["href"], " ".join(self._current_link["pieces"]).strip()))
            self._current_link = None
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._suppressed = max(0, self._suppressed - 1)

    def handle_data(self, data: str) -> None:
        if self._suppressed:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title += cleaned + " "
        else:
            self.strings.append(cleaned)
        if self._current_link is not None:
            self._current_link["pieces"].append(cleaned)


def parse_page(raw: bytes) -> PageParser:
    p = PageParser()
    p.feed(raw.decode("utf-8", errors="replace"))
    return p


def request(url: str, extra_headers: dict | None = None, limit: int | None = None):
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = urlopen(Request(url, headers=headers), timeout=55)
            if limit is not None:
                data = resp.read(limit + 1)
                resp.close()
                if len(data) > limit:
                    raise RuntimeError("Response too large; refusing to parse")
                return data
            return resp
        except HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == RETRY_ATTEMPTS:
                raise
            retry_after = exc.headers.get("Retry-After", "")
            seconds = min(60, int(retry_after)) if retry_after.isdigit() else min(30, 3 * attempt)
            print(f"   HTTP {exc.code}; pausing {seconds}s before retry {attempt + 1}/{RETRY_ATTEMPTS}")
            time.sleep(seconds)
        except (URLError, TimeoutError, ConnectionError) as exc:
            if attempt == RETRY_ATTEMPTS:
                raise
            print(f"   Network error: {exc}; retrying")
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def html(url: str) -> PageParser:
    # Limit total request starts across ALL metadata workers, rather than
    # sleeping separately in each worker (which could create traffic bursts).
    pace_request()
    raw = request(url, limit=MAX_HTML_BYTES)
    if not (b"<html" in raw[:2048].lower() or b"<!doctype" in raw[:2048].lower()):
        raise RuntimeError(f"Unexpected response, not HTML: {url}")
    page = parse_page(raw)
    if len(page.links) < 3:
        raise RuntimeError(f"Page has too few links; possible challenge or blocked page: {url}")
    return page


def pace_request() -> None:
    """Global interval shared by HTML GETs and optional file HEAD probes."""
    global _html_next_time
    with _html_lock:
        waiting = _html_next_time - time.monotonic()
        if waiting > 0:
            time.sleep(waiting)
        _html_next_time = time.monotonic() + PAUSE_BETWEEN_HTML


def normalized_detail(href: str, current_url: str) -> str | None:
    parsed = urlparse(urljoin(current_url, href))
    if parsed.hostname not in {SITE_HOST, "trex-arms.com"}:
        return None
    if parsed.path.rstrip("/") == VAULT_PATH:
        return None
    m = re.fullmatch(r"/field-manual-vault/([A-Za-z0-9][A-Za-z0-9_-]*)/?", parsed.path)
    if not m:
        return None
    if m.group(1).lower() in {"faq", "citation-map", "citations", "about", "search"}:
        return None
    return BASE + VAULT_PATH + "/" + m.group(1)


def listing_page_links(url: str, page: PageParser) -> tuple[list[tuple[str, str]], str | None]:
    manuals: dict[str, str] = {}
    next_url = None
    try:
        current_page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
    except ValueError:
        current_page = 1
    for href, label in page.links:
        u = normalized_detail(href, url)
        if u and u not in manuals:
            manuals[u] = label
        if label.strip().lower() in {"next", "next page", "next →", "next »"}:
            target = urljoin(url, href)
            p = urlparse(target)
            if p.hostname != SITE_HOST or p.path.rstrip("/") != VAULT_PATH:
                continue
            try:
                target_page = int(parse_qs(p.query).get("page", ["1"])[0])
            except ValueError:
                continue
            if target_page > current_page:
                next_url = target
    return list(manuals.items()), next_url


def safe_listing_page(url: str, params: dict[str, str]) -> bool:
    """Validate checkpoint URLs; corrupted local state cannot trigger arbitrary fetches."""
    p = urlparse(url)
    if (p.scheme != "https" or p.hostname != SITE_HOST or p.path.rstrip("/") != VAULT_PATH
            or p.username or p.password or p.port not in (None, 443)):
        return False
    query = parse_qs(p.query)
    if set(query) - set(params) - {"page"}:
        return False
    if any(query.get(key) != [value] for key, value in params.items()):
        return False
    if "page" in query:
        try:
            if len(query["page"]) != 1 or int(query["page"][0]) < 1:
                return False
        except ValueError:
            return False
    return True


def crawl_listing(params: dict[str, str], description: str,
                  cache: CatalogCache | None = None,
                  checkpoint_key: str = "") -> list[tuple[str, str]]:
    """Resume from the last *completed page* of each listing after Ctrl+C.

    Only a successfully finished listing becomes the seven-day cache. A
    partially scanned category never replaces a good saved listing.
    """
    first = VAULT + "?" + urlencode(params)
    current = first
    urls: dict[str, str] = {}
    visited: set[str] = set()
    page_number = 0
    if cache is not None and checkpoint_key:
        state = cache.get_scan_state(checkpoint_key, params)
        if state is not None:
            saved_url = state["next_url"]
            if safe_listing_page(saved_url, params):
                current = saved_url
                urls = {u: label for u, label in state["found"].items()
                        if normalized_detail(u, BASE) == u and isinstance(label, str)}
                visited = {u for u in state.get("visited", [])
                           if isinstance(u, str) and safe_listing_page(u, params)}
                page_number = max(0, state["pages"])
                print(f"   Resuming {description}: {page_number} pages and {len(urls)} links saved")
    for i in range(page_number, MAX_LISTING_PAGES):
        if current in visited:
            raise RuntimeError(f"Pagination loop in {description}: {current}")
        if not safe_listing_page(current, params):
            raise RuntimeError(f"Invalid next-page URL in {description}: {current}")
        visited.add(current)
        page = html(current)
        new, nxt = listing_page_links(current, page)
        for link, text in new:
            urls.setdefault(link, text)
        if i == page_number or (i + 1) % 5 == 0 or not nxt:
            print(f"   {description}: page {i + 1}, {len(urls)} unique links", flush=True)
        if not nxt:
            if cache is not None and checkpoint_key:
                cache.clear_scan_state(checkpoint_key)
            return list(urls.items())
        if cache is not None and checkpoint_key:
            cache.put_scan_state(checkpoint_key, {
                "params": params, "next_url": nxt, "found": urls,
                "pages": i + 1, "visited": sorted(visited),
            })
        current = nxt
    raise RuntimeError(f"More than {MAX_LISTING_PAGES} listing pages: {description}")


def crawl_topics(selected: list[str]) -> dict[str, str]:
    discovered: dict[str, str] = {}
    for category in selected:
        rules = TOPICS[category]
        regex = re.compile(rules["title"], re.IGNORECASE)
        for token in rules["queries"]:
            print(f"\nSearching titles for {category}: {token}")
            try:
                pairs = crawl_listing({"q": "title:" + token}, category + "/" + token)
                for u, title in pairs:
                    # A manual may occur across topics; first matching category wins.
                    if regex.search(title) and u not in discovered:
                        discovered[u] = category
            except Exception as exc:
                print(f"   WARNING: search failed; continuing: {exc}")
        print(f"   Total found across topics so far: {len(discovered)}", flush=True)
    return discovered


def crawl_functions(selected: list[str] | None = None,
                    cache: CatalogCache | None = None, refresh: bool = False,
                    stats: dict | None = None) -> dict[str, str]:
    """Discover only specified original site Functions, or all if omitted."""
    discovered: dict[str, str] = {}
    stats = stats if stats is not None else {}
    stats.setdefault("listings_cached", 0)
    stats.setdefault("listings_scanned", 0)
    stats.setdefault("listings_failed", [])
    categories = list(selected if selected is not None else FUNCTIONS)
    for position, category in enumerate(categories, 1):
        slug = FUNCTIONS[category]
        cache_key = "fn:" + slug
        pairs = None if refresh or cache is None else cache.get_listing(cache_key)
        if pairs is not None:
            stats["listings_cached"] += 1
            print(f"\n[Listing {position}/{len(categories)}] Cached: {category} ({len(pairs)} links)")
            for url, _ in pairs:
                if normalized_detail(url, BASE) == url:
                    discovered.setdefault(url, category)
            continue
        print(f"\n[Listing {position}/{len(categories)}] Scanning: {category}")
        try:
            if cache is not None:
                pairs = crawl_listing({"fn": slug}, category, cache=cache, checkpoint_key=cache_key)
            else:
                pairs = crawl_listing({"fn": slug}, category)
            if cache is not None:
                cache.put_listing(cache_key, pairs)
            stats["listings_scanned"] += 1
            for url, _ in pairs:
                if normalized_detail(url, BASE) == url:
                    discovered.setdefault(url, category)
        except Exception as exc:
            print(f"   WARNING: function listing failed: {exc}")
            stats["listings_failed"].append({"category": category, "error": str(exc)[:300]})
            # An expired listing is better than dropping previously known manuals.
            if cache is not None:
                old = cache.get_listing(cache_key, ttl=10**12)
                if old is not None:
                    print(f"   Retaining {len(old)} previously known links (stale)")
                    for url, _ in old:
                        discovered.setdefault(url, category)
    return discovered


def crawl_organized(cache: CatalogCache | None = None, refresh: bool = False,
                    stats: dict | None = None) -> dict[str, str]:
    """Best-effort union of public sitemaps + short filtered listings.

    Counts in the site's UI are not a guarantee of enumerability: report actual
    URL count rather than claiming all 4,215 have been discovered.
    """
    found: dict[str, str] = {u: "" for u in crawl_sitemap()}
    print(f"Sitemap candidates: {len(found)}")
    # Large, unfiltered listings can stop paging too early. Use function,
    # collection and cross-filters to retrieve more distinct detail pages.
    filters: list[tuple[str, dict[str, str]]] = []
    for function, slug in FUNCTIONS.items():
        filters.append(("function " + function, {"fn": slug}))
    collections = ("current", "recent-archive", "lessons-learned", "historic")
    for collection in collections:
        filters.append(("collection " + collection, {"coll": collection}))
        for function, slug in FUNCTIONS.items():
            filters.append((f"{collection}/{function}", {"coll": collection, "fn": slug}))
    for branch in ("army", "usmc", "navy", "joint", "af"):
        filters.append(("branch " + branch, {"branch": branch}))
    for label, params in filters:
        key = "full:" + hashlib.sha256(urlencode(sorted(params.items())).encode()).hexdigest()[:16]
        try:
            pairs = None if refresh or cache is None else cache.get_listing(key)
            if pairs is None:
                pairs = crawl_listing(params, label, cache=cache, checkpoint_key=key) if cache else crawl_listing(params, label)
                if cache is not None:
                    cache.put_listing(key, pairs)
            for u, _title in pairs:
                found.setdefault(u, "")
        except Exception as exc:
            print(f"   WARNING: skipped incomplete filter {label}: {str(exc)[:150]}")
            if stats is not None:
                stats.setdefault("listings_failed", []).append({"category": label, "error": str(exc)[:300]})
            if cache is not None:
                for u, _title in cache.get_listing(key, ttl=10**12) or []:
                    found.setdefault(u, "")
    print(f"Total unique public detail URLs: {len(found)} (site advertises 4,215 publications)")
    return found


def crawl_sitemap() -> list[str]:
    """Best-effort complete edition coverage, if site publishes detail URLs in its XML sitemap."""
    print("Looking for manual detail URLs in public sitemaps...")
    sitemap_queue = [BASE + "/sitemap.xml", BASE + "/sitemap_index.xml"]
    seen: set[str] = set()
    found: set[str] = set()
    while sitemap_queue and len(seen) < 120:
        url = sitemap_queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            raw = request(url, limit=MAX_SITEMAP_BYTES)
            if url.endswith(".gz"):
                raw = gzip.decompress(raw)
            root = ET.fromstring(raw)
            for el in root.iter():
                if not el.tag.endswith("}loc") and el.tag != "loc":
                    continue
                loc = (el.text or "").strip()
                if not loc:
                    continue
                p = urlparse(loc)
                if p.hostname not in {SITE_HOST, "trex-arms.com"}:
                    continue
                if normalized_detail(loc, BASE):
                    found.add(normalized_detail(loc, BASE))
                elif ("sitemap" in p.path) and (p.path.endswith(".xml") or p.path.endswith(".xml.gz")):
                    sitemap_queue.append(loc)
        except Exception as exc:
            print(f"   Could not read {url}: {str(exc)[:150]}")
    print(f"   Found {len(found)} individual manual detail URLs in sitemaps")
    return sorted(found)


def detail_metadata(page: PageParser) -> tuple[str, str, str, str, str, str, str, str, str]:
    page_title = page.title.strip()
    # Typical title: 'FM 4-25.11: First Aid | T.REX ARMS Field Manual Vault'
    page_title = re.sub(r"\s*\|\s*T\.REX.*$", "", page_title, flags=re.IGNORECASE)
    m = re.match(r"^(.*?):\s*(.+)$", page_title)
    if not m:
        raise RuntimeError(f"Cannot identify manual title from page title: {page_title[:120]}")
    designation, title = m.group(1).strip(), m.group(2).strip()
    texts = page.strings
    def get_field(name: str) -> str:
        # Prefer the metadata strip, usually 'Designation FM 4-25.11',
        # 'Edition Dec 2002', 'Function Medical TCCC'.
        for i, line in enumerate(texts):
            if line.strip().lower() == name.lower() and i + 1 < len(texts):
                return texts[i + 1].strip()
            mm = re.fullmatch(re.escape(name) + r"\s*:\s*(.+)", line, re.IGNORECASE)
            if mm:
                return mm.group(1).strip()
        return ""
    edition = get_field("Edition")
    subject = get_field("Function")
    collection = get_field("Collection")
    # Some detail pages render a stray numeric badge immediately after a
    # metadata label. Those are not Function / Collection classifications.
    # The selected listing function can safely supply a missing Function.
    if subject.isdecimal():
        subject = ""
    if collection.isdecimal():
        collection = ""
    status = get_field("Status")
    if status.isdecimal():
        status = ""  # Stray count badge, not an actual publication status.
    branch = get_field("Branch")
    links = {".pdf": "", ".epub": ""}
    for href, _ in page.links:
        url = urljoin(BASE, href)
        p = urlparse(url)
        if p.hostname != FILE_HOST:
            continue
        if p.path.lower().startswith("/pdf/") and p.path.lower().endswith(".pdf"):
            links[".pdf"] = url
        elif p.path.lower().startswith("/epub/") and p.path.lower().endswith(".epub"):
            links[".epub"] = url
    if not any(links.values()):
        raise RuntimeError("No direct PDF or EPUB download links found")
    return designation, title, edition, subject, collection, status, branch, links[".pdf"], links[".epub"]


def safe_name(value: str, limit: int = 105) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1F]', " - ", value)
    value = re.sub(r"\s+", " ", value).strip(" .") or "Unnamed"
    if len(value) > limit:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:6]
        value = value[:max(1, limit - 9)].rstrip(" .") + " [" + digest + "]"
    if value.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "LPT1", "LPT2"}:
        value += "-manual"
    return value


def save_json_atomic(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def build_manifest(discovered: dict[str, str], progress_path: Path, organized: bool = False,
                   cache: CatalogCache | None = None, workers: int = DEFAULT_WORKERS,
                   refresh: bool = False, recheck_limit: int = 0,
                   recheck_age: int = METADATA_TTL_SECONDS,
                   stats: dict | None = None) -> list[dict]:
    """Assemble manifest with shared metadata cache and bounded concurrency.

    Cache is keyed by *manual URL*, not selection hash, so switching from
    Medical to Survival to a combined selection does not reread old pages.
    Each successful result is committed before the next request completes.
    """
    start = time.monotonic()
    stats = stats if stats is not None else {}
    entries_by_url: dict[str, dict] = {}
    if progress_path.is_file():
        try:
            for row in json.loads(progress_path.read_text(encoding="utf-8")):
                if isinstance(row, dict) and row.get("source_url") in discovered:
                    url = row["source_url"]
                    # Interrupted checkpoints may predate the cache cleanup.
                    current = cache.metadata(url) if cache is not None else None
                    corrected = align_manifest_metadata(row, current, organized)
                    entries_by_url[url] = _entry_with_category(
                        corrected, discovered[url], organized) if current is None else corrected
            print(f"Resuming checkpoint: {len(entries_by_url)} parsed manuals")
        except (OSError, ValueError, TypeError):
            entries_by_url = {}
    cached_count = 0
    pending: list[tuple[str, str]] = []
    for url, selected_category in sorted(discovered.items()):
        if normalized_detail(url, BASE) != url:
            print(f"  WARNING: skipped invalid manual detail URL {url!r}")
            continue
        if url in entries_by_url:
            continue
        cached = None if refresh or cache is None else cache.metadata(url)
        if cached is not None:
            repaired = repair_cached_metadata(cached, selected_category)
            if repaired != cached and cache is not None:
                cache.put_metadata(repaired)
            entries_by_url[url] = _entry_with_category(repaired, selected_category, organized)
            cached_count += 1
        else:
            pending.append((url, selected_category))
    checkpoint_count = len(entries_by_url) - cached_count
    recheck = [] if refresh or cache is None else cache.stale_metadata_urls(
        set(discovered), recheck_age, recheck_limit)
    recheck = [(url, discovered[url]) for url in recheck
               if url in entries_by_url and url not in {u for u, _ in pending}]
    stats.update({
        "metadata_cache_hits": cached_count,
        "metadata_checkpoint_hits": checkpoint_count,
        "metadata_new_fetched": 0,
        "metadata_rechecked": 0,
        "metadata_changed": 0,
        "metadata_failed": 0,
        "metadata_failed_urls": [],
    })
    print(f"Metadata: {cached_count} cache hits, {checkpoint_count} checkpoint hits, "
          f"{len(pending)} new requests, {len(recheck)} bounded rechecks")
    if entries_by_url:
        save_json_atomic(progress_path, list(entries_by_url.values()))
    failures: list[tuple[str, str]] = []
    completed = 0
    tasks_to_run = [(url, cat, False) for url, cat in pending] + [
        (url, cat, True) for url, cat in recheck]
    if tasks_to_run:
        # Bounded scheduling: never queue thousands of requests in the executor.
        # Ctrl+C cancels outstanding work, preserving already completed entries.
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trex-meta")
        iterator = iter(tasks_to_run)
        active = {}
        def schedule_one() -> bool:
            try:
                url, category, is_recheck = next(iterator)
            except StopIteration:
                return False
            active[pool.submit(_fetch_manual_entry, url)] = (url, category, is_recheck)
            return True
        for _ in range(workers):
            schedule_one()
        interrupted = False
        try:
            while active:
                done, _ = wait(set(active), return_when=FIRST_COMPLETED)
                for future in done:
                    url, category, is_recheck = active.pop(future)
                    old_entry = cache.metadata(url) if cache is not None else entries_by_url.get(url)
                    try:
                        raw_entry = repair_cached_metadata(future.result(), category)
                        if is_recheck:
                            stats["metadata_rechecked"] += 1
                            relevant = ("designation", "title", "edition", "site_function", "collection",
                                        "status", "branch", "pdf_url", "epub_url")
                            if old_entry is not None and any(old_entry.get(k) != raw_entry.get(k) for k in relevant):
                                stats["metadata_changed"] += 1
                                print(f"   UPDATED: {raw_entry['designation']} – {raw_entry['title']}")
                        else:
                            stats["metadata_new_fetched"] += 1
                        if cache is not None:
                            cache.put_metadata(raw_entry, durable=True)
                        entries_by_url[url] = _entry_with_category(raw_entry, category, organized)
                    except Exception as exc:
                        print(f"  WARNING: metadata unavailable {url}: {str(exc)[:175]}", flush=True)
                        failures.append((url, str(exc)))
                        stats["metadata_failed"] += 1
                        stats["metadata_failed_urls"].append(url)
                        # A failed recheck must NEVER erase good cached metadata.
                    completed += 1
                    if completed % 10 == 0 or completed == len(tasks_to_run):
                        print(f"Metadata progress: {completed}/{len(tasks_to_run)} requests; "
                              f"{stats['metadata_new_fetched']} new, "
                              f"{stats['metadata_rechecked']} rechecked, "
                              f"{stats['metadata_changed']} changed, {len(failures)} failed; "
                              f"{time.monotonic() - start:.0f}s elapsed", flush=True)
                        save_json_atomic(progress_path, list(entries_by_url.values()))
                    schedule_one()
        except KeyboardInterrupt:
            interrupted = True
            save_json_atomic(progress_path, list(entries_by_url.values()))
            print("Interrupted: metadata checkpoint preserved.")
            raise
        finally:
            pool.shutdown(wait=not interrupted, cancel_futures=interrupted)
    entries = [entries_by_url[url] for url in sorted(entries_by_url)]
    # Sorting first makes collision resolution deterministic across parallel runs.
    used: dict[tuple[str, str, str, str], str] = {}
    for ent in entries:
        key = (ent["category"].lower(), ent.get("subtopic", "").lower(),
               ent["folder"].lower(), ent["file_stem"].lower())
        previous = used.get(key)
        if previous is not None and previous != ent["source_url"]:
            tail = hashlib.sha256(ent["source_url"].encode()).hexdigest()[:7]
            ent["file_stem"] = safe_name(ent["file_stem"][:100] + " [" + tail + "]", 113)
            key = (ent["category"].lower(), ent.get("subtopic", "").lower(),
                   ent["folder"].lower(), ent["file_stem"].lower())
        used[key] = ent["source_url"]
    save_json_atomic(progress_path, entries)
    error_path = progress_path.parent / "trex_metadata_errors.csv"
    if failures:
        with error_path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["manual_page", "error"])
            writer.writerows(failures)
        print(f"  Metadata failures: {len(failures)}; see {error_path.name}")
    else:
        # Global error CSV represents the most recent metadata build. A
        # successful retry must not leave a stale warning in the vault root.
        error_path.unlink(missing_ok=True)
    print(f"Metadata assembled: {len(entries)} entries, {time.monotonic()-start:.1f}s", flush=True)
    return entries


def _fetch_manual_entry(source: str) -> dict:
    page = html(source)
    desig, title, edition, function, collection, status, branch, pdf, epub = detail_metadata(page)
    return {
        "category": "", "subtopic": "", "tags": "", "site_function": function,
        "collection": collection, "status": status, "branch": branch,
        "designation": desig, "title": title, "edition": edition,
        "folder": safe_name(f"{desig} - {title}", 99),
        "file_stem": safe_name(f"{desig} - {title}" +
                               (f" ({edition})" if edition else " (undated)"), 113),
        "source_url": source, "pdf_url": pdf, "epub_url": epub,
    }


def repair_cached_metadata(raw: dict, selected_function: str) -> dict:
    """Fix observed malformed numeric metadata without altering source URLs.

    The listing Function is used only when the scraped Function was empty or
    numeric; an unrecognized *text* Function is preserved for future review.
    A numeric Status or Collection becomes unknown, never a guessed value.
    This also repairs metadata imported from earlier release manifests.
    """
    cleaned = dict(raw)
    function = str(cleaned.get("site_function") or "").strip()
    collection = str(cleaned.get("collection") or "").strip()
    if not function or function.isdecimal():
        cleaned["site_function"] = selected_function if selected_function in FUNCTIONS else ""
    if collection.isdecimal():
        cleaned["collection"] = ""
    if str(cleaned.get("status") or "").strip().isdecimal():
        cleaned["status"] = ""
    return cleaned


def align_manifest_metadata(row: dict, cached: dict | None, organized: bool) -> dict:
    """Update only affected metadata in old selection JSON, without moving files.

    Saved JSON manifests may be reused for seven days, and can contain badges
    which have since been repaired in SQLite. Do not let them reintroduce bad
    labels into new catalogs or HTML indexes.
    """
    cleaned = dict(row)
    corrected = cached or repair_cached_metadata(cleaned, "")
    previous_function = cleaned.get("site_function", "")
    for field in ("site_function", "collection", "status"):
        if field in corrected:
            cleaned[field] = corrected[field]
    if cleaned.get("site_function") != previous_function:
        if organized:
            cleaned = _entry_with_category(cleaned, cleaned.get("site_function", ""), True)
        elif cleaned.get("category") in FUNCTIONS or previous_function == cleaned.get("category"):
            cleaned["category"] = cleaned.get("site_function") or "Other"
    return cleaned


def _entry_with_category(raw: dict, discovered_category: str, organized: bool) -> dict:
    ent = dict(raw)
    function = ent.get("site_function", "")
    if function not in FUNCTIONS and discovered_category in FUNCTIONS:
        function = discovered_category
        ent["site_function"] = function
    if organized:
        ent["category"], ent["subtopic"], ent["tags"] = category_for(
            function, ent.get("title", ""), ent.get("collection", ""))
    else:
        ent["category"] = discovered_category or (function if function in FUNCTIONS else "Other")
        ent["subtopic"], ent["tags"] = "", ""
    return ent


def destination_file(root: Path, entry: dict, ext: str, formats_name: str) -> Path:
    category = safe_name(entry["category"], 45)
    organized = bool(entry.get("subtopic"))
    container = root / "Organized Library" if organized else root
    base = container / category
    if organized:
        base = base / safe_name(entry["subtopic"], 45)
    room_for_folder = 240 - len(str(base)) - 48
    if room_for_folder < 30:
        raise OSError("Destination path is too deep; choose a shorter root directory")
    folder = safe_name(entry["folder"], max(30, min(72, room_for_folder)))
    parent = base / folder
    if formats_name:
        parent = parent / safe_name(formats_name, 28)
    remaining = 240 - len(str(parent)) - len(ext) - 1
    if remaining < 24:
        raise OSError("Destination path too deep for Windows; choose a shorter root")
    stem = safe_name(entry["file_stem"], min(95, remaining))
    return parent / (stem + ext)


def valid_file(path: Path, ext: str) -> bool:
    if not path.is_file() or path.stat().st_size < 100:
        return False
    try:
        if ext == ".pdf":
            with path.open("rb") as f:
                return b"%PDF" in f.read(16)
        if ext == ".epub":
            return zipfile.is_zipfile(path)
    except OSError:
        return False
    return False


def valid_public_download_url(url: str, ext: str) -> bool:
    """Fail closed on malformed/edited URLs in local manifest JSON files."""
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname == FILE_HOST
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and parsed.path.lower().startswith("/" + ext.lstrip(".") + "/")
            and parsed.path.lower().endswith(ext))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def inventory_existing(root: Path, inventory_csv: Path, duplicates_csv: Path,
                       cache: CatalogCache | None = None) -> dict[tuple[str, str], Path]:
    """Read-only inventory: no files are deleted, moved, or renamed."""
    from collections import defaultdict
    lookup: dict[tuple[str, str], Path] = {}
    duplicates: dict[tuple[str, str], list[Path]] = defaultdict(list)
    files = []
    reused_hashes = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in {".pdf", ".epub"}:
            continue
        if not valid_file(path, ext):
            continue
        try:
            digest = cache.get_hash(path, root) if cache is not None else None
            if digest is None:
                digest = sha256_file(path)
                if cache is not None:
                    cache.put_hash(path, root, digest)
            else:
                reused_hashes += 1
            rel = path.relative_to(root)
            files.append((str(rel), ext, path.stat().st_size, digest))
            key = (ext, digest)
            lookup.setdefault(key, path)
            duplicates[key].append(path)
        except (OSError, ValueError) as exc:
            print(f"  WARNING: could not index {path}: {exc}")
    with inventory_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["relative_path", "format", "bytes", "sha256"])
        writer.writerows(files)
    with duplicates_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["format", "sha256", "copy_count", "bytes", "relative_path"])
        for (ext, digest), paths in sorted(duplicates.items()):
            if len(paths) > 1:
                for path in paths:
                    writer.writerow([ext, digest, len(paths), path.stat().st_size, str(path.relative_to(root))])
    print(f"Existing audit: {len(files)} PDF/EPUB files ({reused_hashes} hashes reused), "
          f"{sum(1 for g in duplicates.values() if len(g)>1)} exact duplicate groups.")
    print(f"  Inventory: {inventory_csv.name}")
    print(f"  Duplicate report: {duplicates_csv.name}")
    return lookup


class BudgetReached(Exception):
    """Download cap was reached. Partial files remain resumable."""


class DownloadBudget:
    """Cap new network bytes WRITTEN during this invocation (not preexisting data)."""
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.used_bytes = 0

    def consume(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("Negative byte amount")
        if self.max_bytes and self.used_bytes + amount > self.max_bytes:
            raise BudgetReached(
                f"Per-run download cap reached: {self.used_bytes / 1e9:.3f} / "
                f"{self.max_bytes / 1e9:.3f} GB; current .part file can resume later."
            )
        self.used_bytes += amount


def remote_content_length(url: str, ext: str) -> int | None:
    """One polite HEAD request; no PDF/EPUB bodies are transferred.

    Not every origin implements HEAD or reports a meaningful Content-Length.
    In that case we explicitly report the size as unknown.
    """
    if not valid_public_download_url(url, ext):
        return None
    pace_request()
    try:
        req = Request(url, headers=HEADERS, method="HEAD")
        with urlopen(req, timeout=12) as response:
            value = response.headers.get("Content-Length", "")
            if value.isdigit() and 0 < int(value) < 100_000_000_000:
                return int(value)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None
    return None


def _even_sample(values: list, n: int) -> list:
    if n <= 0:
        return []
    if len(values) <= n:
        return values
    if n == 1:
        return [values[len(values) // 2]]
    return [values[int(round(i * (len(values)-1) / (n-1)))] for i in range(n)]


def estimate_storage(entries: list[dict], root: Path, fmt: str, formats_folder: str,
                     cache: CatalogCache, head_samples: int = 0) -> dict:
    """Preflight: never transfer a document merely to learn its size.

    Cached exact lengths and an optional bounded HEAD sample are used for a
    rough projection. Already downloaded bytes and resumable partial bytes
    are excluded from *new* estimated network/storage needs.
    """
    items = []
    known_sizes: dict[str, list[int]] = {".pdf": [], ".epub": []}
    unsized = []
    for entry in entries:
        for ext, field in ((".pdf", "pdf_url"), (".epub", "epub_url")):
            if fmt != "both" and fmt != ext[1:]:
                continue
            url = entry.get(field) or ""
            if not url or not valid_public_download_url(url, ext):
                continue
            try:
                target = destination_file(root, entry, ext, formats_folder)
                present = valid_file(target, ext)
                partial = target.with_name(target.name + ".part")
                already_partial = partial.stat().st_size if not present and partial.exists() else 0
            except (OSError, ValueError):
                continue
            if present:
                cache.put_file_size(url, target.stat().st_size, "local-file")
                items.append({"url": url, "ext": ext, "present": True,
                              "total": target.stat().st_size, "partial": 0})
                # Existing files offer a local sample without HEAD requests.
                known_sizes[ext].append(target.stat().st_size)
            else:
                size = cache.get_file_size(url)
                row = {"url": url, "ext": ext, "present": False,
                       "total": size, "partial": already_partial}
                items.append(row)
                if size is None:
                    unsized.append(row)
                else:
                    known_sizes[ext].append(size)

    # Allocate a few HEADs to each file type, spreading them over the input
    # rather than probing the first N books (which may not be representative).
    sampled = 0
    consecutive_head_failures = 0
    pdf_unknown = [r for r in unsized if r["ext"] == ".pdf"]
    epub_unknown = [r for r in unsized if r["ext"] == ".epub"]
    if pdf_unknown and epub_unknown:
        pdf_n = head_samples // 2 + head_samples % 2
    else:
        pdf_n = head_samples if pdf_unknown else 0
    targets = _even_sample(pdf_unknown, pdf_n) + _even_sample(
        epub_unknown, max(0, head_samples - pdf_n))
    if targets:
        print(f"Storage preflight: sampling up to {len(targets)} file HEAD requests ", end="", flush=True)
    for row in targets:
        if consecutive_head_failures >= 2:
            print(" [skipping further probes: server may not support HEAD]", end="")
            break
        size = remote_content_length(row["url"], row["ext"])
        sampled += 1
        consecutive_head_failures = 0 if size else consecutive_head_failures + 1
        if size is not None:
            row["total"] = size
            known_sizes[row["ext"]].append(size)
            cache.put_file_size(row["url"], size, "HEAD")
        print("." if size else "?", end="", flush=True)
    if targets:
        print(" ('.' size found; '?' unknown)")

    # These are estimates, not a guarantee: manuals can range from tiny to
    # multi-hundred-MB scans. Never report a made-up exact total.
    typical = {ext: statistics.median(sizes) if sizes else None
               for ext, sizes in known_sizes.items()}
    known_remaining = 0
    projected_remaining = 0
    unknown_count = 0
    already_count = 0
    unknown_without_basis = 0
    for row in items:
        if row["present"]:
            already_count += 1
            continue
        if row["total"] is not None:
            remaining = max(0, row["total"] - row["partial"])
            known_remaining += remaining
            projected_remaining += remaining
        else:
            unknown_count += 1
            if typical[row["ext"]] is None:
                unknown_without_basis += 1
            else:
                projected_remaining += max(0, int(typical[row["ext"]]) - row["partial"])
    free_bytes = shutil.disk_usage(root).free
    result = {
        "method": "cached sizes + optional bounded HTTP HEAD sample; unknowns use per-format median",
        "selected_files": len(items), "already_downloaded_files": already_count,
        "files_still_needed": len(items) - already_count,
        "known_remaining_bytes": known_remaining,
        "projected_remaining_bytes": projected_remaining if unknown_without_basis == 0 else None,
        "files_with_unknown_size": unknown_count,
        "unknown_files_without_basis": unknown_without_basis,
        "sample_attempts": sampled,
        "disk_free_bytes": free_bytes,
        "pdf_sample_median_bytes": typical[".pdf"],
        "epub_sample_median_bytes": typical[".epub"],
    }
    save_json_atomic(root / "Field_Manual_Storage_Estimate.json", result)
    print("\nSTORAGE PREFLIGHT (estimates only; no document bodies downloaded)")
    print(f"  Selected {len(items)} format-files | already present {already_count} | "
          f"still needed {len(items)-already_count}")
    print(f"  New bytes with known lengths: {known_remaining / 1e9:.2f} GB")
    if result["projected_remaining_bytes"] is not None:
        print(f"  Rough total remaining: {projected_remaining / 1e9:.2f} GB "
              f"({unknown_count} file lengths estimated, not verified)")
        if projected_remaining > free_bytes:
            print("  WARNING: Projection exceeds free disk space; choose fewer manuals.")
    else:
        print(f"  Total estimate unavailable: {unknown_without_basis} file sizes have no basis.")
    print(f"  Free disk space: {free_bytes / 1e9:.2f} GB; "
          "per-run download cap is separate from the storage estimate")
    return result


def write_coverage_report(root: Path, cache: CatalogCache, mode: str,
                          selected: list[str], discovered: dict[str, str] | None,
                          entries: list[dict], scan_stats: dict | None = None,
                          metadata_stats: dict | None = None) -> dict:
    """Document observed coverage and *known* gaps; no completeness claims.

    The difference from the historical advertised count is not an enumerable
    missing-manual list. Incomplete listings and failed metadata URLs *are*
    actionable evidence and are recorded separately.
    """
    all_cached = cache.all_metadata()
    cached_urls = {e["source_url"] for e in all_cached}
    parsed_urls = {e["source_url"] for e in entries}
    discovered_urls = set(discovered) if discovered is not None else parsed_urls
    scanned_all_functions = (mode in {"organized", "full", "functions"}
                             or mode == "selective" and set(selected) == set(FUNCTIONS))
    scan_stats = scan_stats or {}
    metadata_stats = metadata_stats or {}
    known_failures = sorted(discovered_urls - parsed_urls)
    failed_listings = scan_stats.get("listings_failed", [])
    from collections import Counter
    suspect_metadata = []
    for e in all_cached:
        issues = []
        function = str(e.get("site_function") or "")
        collection = str(e.get("collection") or "")
        if function not in FUNCTIONS:
            issues.append(f"unrecognized Function: {function!r}")
        if collection.isdecimal():
            issues.append(f"numeric Collection: {collection!r}")
        status = str(e.get("status") or "")
        if status.isdecimal():
            issues.append(f"numeric Status: {status!r}")
        elif status and status not in {"Active", "Superseded"}:
            # Preserve unfamiliar textual statuses but make them reviewable.
            # This is the observed vocabulary, not a claim that it is exhaustive.
            issues.append(f"unrecognized Status: {status!r}")
        if issues:
            suspect_metadata.append({"source_url": e["source_url"], "issues": issues})
    counts = dict(sorted(Counter(
        e.get("site_function") if e.get("site_function") in FUNCTIONS else "Unknown"
        for e in all_cached).items()))
    report = {
        "source": VAULT,
        "reference_published_total": REFERENCE_PUBLISHED_TOTAL,
        "reference_note": "Historical site figure, not reverified against a live counter for this run",
        "scope": ("offline cache review (no site scan)" if mode == "offline" else
                  "all known function filters" if scanned_all_functions else
                  "selected function filters / cached subset"),
        "site_wide_comparison_valid": bool(scanned_all_functions and not failed_listings),
        "functions_selected": selected,
        "run_urls_discovered": len(discovered_urls),
        "run_metadata_parsed": len(parsed_urls),
        "unique_metadata_cached_across_runs": len(cached_urls),
        "known_unparsed_urls": known_failures,
        "failed_listing_filters": failed_listings,
        "listing_cache_hits": scan_stats.get("listings_cached", 0),
        "listing_scans_completed": scan_stats.get("listings_scanned", 0),
        "metadata_stats": metadata_stats,
        "catalog_function_counts": counts,
        "suspect_cached_metadata_count": len(suspect_metadata),
        "suspect_cached_metadata": suspect_metadata[:100],
        "incomplete_disclaimer": "Cannot name URLs never exposed by the site's public listings."
                                  " A count below the advertised total is not proof of missing files.",
    }
    if scanned_all_functions:
        report["difference_from_reference_count"] = max(0, REFERENCE_PUBLISHED_TOTAL - len(cached_urls))
        report["reference_coverage_ratio"] = round(len(cached_urls) / REFERENCE_PUBLISHED_TOTAL, 4)
    save_json_atomic(root / "Field_Manual_Coverage_Report.json", report)
    text = [
        "FIELD MANUAL VAULT - CATALOG COVERAGE REPORT",
        "============================================",
        f"Scope: {report['scope']}",
        f"URLs seen this run: {len(discovered_urls)}; parsed this run: {len(parsed_urls)}",
        f"Cached across all runs: {len(cached_urls)} unique manual URLs",
        f"Unparsed detail URLs: {len(known_failures)}; failed filters: {len(failed_listings)}",
        f"Unrecognized or malformed cached metadata records: {len(suspect_metadata)}",
        f"Site's historical advertised count (unverified live): {REFERENCE_PUBLISHED_TOTAL}",
    ]
    if scanned_all_functions:
        text += [f"Numerical gap from advertised count: {report['difference_from_reference_count']}",
                 "Warning: this is only a numerical comparison, NOT a verified missing-manual list."]
    else:
        text.append("Offline cache review: no site scan was performed." if mode == "offline"
                    else "Selected-category scan: do NOT compare this count with the whole-site total.")
    for item in suspect_metadata[:25]:
        text.append(f"METADATA TO REVIEW: {item['source_url']}: {'; '.join(item['issues'])}")
    for error in failed_listings:
        text.append(f"FAILED FILTER: {error['category']}: {error['error']}")
    for url in known_failures[:100]:
        text.append(f"UNPARSED URL: {url}")
    text.append("\nManuals never exposed by site filters cannot be individually identified here.")
    (root / "Field_Manual_Coverage_Report.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    if mode == "offline":
        print(f"Offline cache review: {len(cached_urls)} cached manuals; "
              f"{len(suspect_metadata)} suspicious metadata records; no site requests")
    else:
        print(f"Catalog coverage: {len(parsed_urls)} parsed in this selection; "
              f"{len(cached_urls)} unique cached overall; "
              f"{len(known_failures)} known unparsed URLs; {len(failed_listings)} failed filters")
    if scanned_all_functions:
        print("  Compared with the historical 4,215 figure; see Coverage_Report for limitations")
    elif mode != "offline":
        print("  Selected categories only; no claim of whole-site completeness")
    return report


def download_with_resume(url: str, target: Path, ext: str,
                         hash_index: dict | None = None,
                         budget: DownloadBudget | None = None) -> str:
    """Return 'downloaded', 'resumed' or 'skipped'; write only valid complete files."""
    if not valid_public_download_url(url, ext):
        raise ValueError(f"Unsafe/malformed {ext} URL in catalog; expected the vault file host")
    if valid_file(target, ext):
        return "skipped"
    if target.exists():
        # An unrecognized existing file might be a user's hand-edited file.
        # Preserve it rather than silently overwriting/deleting it.
        index = 1
        while True:
            backup = target.with_name(target.name + f".invalid-{index}")
            if not backup.exists():
                target.rename(backup)
                print(f"      Kept unrecognized existing file as {backup.name}")
                break
            index += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        current_bytes = partial.stat().st_size if partial.exists() else 0
        extra = {"Range": f"bytes={current_bytes}-"} if current_bytes else {}
        try:
            with request(url, extra_headers=extra) as r:
                code = r.status
                if code == 206 and current_bytes:
                    content_range = r.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {current_bytes}-"):
                        raise RuntimeError("Unexpected server Content-Range; will retry")
                    mode = "ab"
                else:
                    mode = "wb"
                    current_bytes = 0
                content_length = r.headers.get("Content-Length", "")
                expected = current_bytes + int(content_length) if content_length.isdigit() else 0
                with partial.open(mode) as f:
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        if budget is not None:
                            budget.consume(len(chunk))
                        f.write(chunk)
                if expected and partial.stat().st_size != expected:
                    raise IOError(f"Incomplete download: {partial.stat().st_size} / {expected} bytes")
            if not valid_file(partial, ext):
                # Avoid preserving a small HTML error page as a resumable binary.
                if partial.exists() and partial.stat().st_size < 250_000:
                    partial.unlink()
                raise RuntimeError("Downloaded content is not a valid PDF/EPUB")
            if hash_index is not None:
                key = (ext, sha256_file(partial))
                existing = hash_index.get(key)
                if existing and existing.exists() and existing.resolve() != target.resolve():
                    # NTFS hard links let both paths point to one physical blob.
                    # Never delete or move the user's existing folders/files.
                    try:
                        os.link(existing, target)
                        partial.unlink()
                        return "linked"
                    except OSError:
                        print("      Hard link unavailable: falling back to an ordinary file")
                hash_index[key] = target
            os.replace(partial, target)
            return "resumed" if current_bytes else "downloaded"
        except HTTPError as exc:
            if exc.code in (404, 410):
                # A nonexistent resource must not be retried three times.
                raise PermanentMissingError(f"HTTP {exc.code}: file unavailable at {url}") from exc
            if exc.code == 416 and valid_file(partial, ext):
                os.replace(partial, target)
                return "resumed"
            if exc.code == 416 and partial.exists():
                partial.unlink()  # resource changed, restart from byte 0
            error = exc
        except (URLError, TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
            error = exc
        if attempt < RETRY_ATTEMPTS:
            print(f"      Retrying {ext}: {str(error)[:110]}")
            time.sleep(min(12, 2 * attempt))
        else:
            raise RuntimeError(f"Download failed after {RETRY_ATTEMPTS} tries: {error}")
    raise AssertionError("unreachable")


def write_csv(entries: list[dict], path: Path) -> None:
    cols = ["category", "subtopic", "tags", "site_function", "collection", "status", "branch", "designation", "title", "edition", "folder", "file_stem", "source_url", "pdf_url", "epub_url"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)


def write_offline_index(entries: list[dict], root: Path, formats_name: str) -> None:
    esc = html_escape_module.escape
    rows = []
    for ent in sorted(entries, key=lambda r: (r.get("category", ""), r.get("subtopic", ""), r.get("designation", ""), r.get("edition", ""))):
        links = []
        for ext, label in ((".pdf", "PDF"), (".epub", "EPUB")):
            try:
                target = destination_file(root, ent, ext, formats_name)
                if target.is_file():
                    rel = target.relative_to(root).as_posix()
                    from urllib.parse import quote
                    links.append(f'<a href="{esc(quote(rel, safe="/"))}">{label}</a>')
            except OSError:
                pass
        if not links:
            links.append(f'<a href="{esc(ent["source_url"])}" target="_blank">Vault page</a>')
        rows.append(f'<tr data-cat="{esc(ent.get("category", ""))}"><td>{esc(ent.get("category", ""))}</td>'
                    f'<td>{esc(ent.get("subtopic", ""))}</td>'
                    f'<td><b>{esc(ent.get("designation", ""))}</b> {esc(ent.get("title", ""))}</td>'
                    f'<td>{esc(ent.get("edition", ""))}</td><td>{esc(ent.get("tags", ""))}</td>'
                    f'<td>{" · ".join(links)}</td></tr>')
    html_page = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Field Manual Vault – Local Catalog</title><style>body{font:15px system-ui,Segoe UI,Arial;margin:2rem auto;max-width:1500px;padding:0 1rem;color:#19232b}h1{margin-bottom:.3rem}input,select{padding:.65rem;margin:1rem .5rem 1rem 0;font:inherit}input{width:min(480px,90%)}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:.6rem;text-align:left;vertical-align:top}th{background:#f0f2f3;position:sticky;top:0}a{color:#13614f}small{color:#555}.hidden{display:none}</style><h1>Field Manual Vault – offline catalog</h1><small>One primary category per manual. Multiple tags without extra file copies. Source catalog may be incomplete.</small><p><input id="q" placeholder="Search title, designation, topic, tags…"><select id="cat"><option value="">All categories</option></select> <span id="count"></span></p><table><thead><tr><th>Category</th><th>Subtopic</th><th>Publication</th><th>Edition</th><th>Tags</th><th>Open</th></tr></thead><tbody>""" + "".join(rows) + """</tbody></table><script>const rows=[...document.querySelectorAll('tbody tr')];const q=document.getElementById('q'),cat=document.getElementById('cat'),count=document.getElementById('count');[...new Set(rows.map(r=>r.dataset.cat))].sort().forEach(v=>{let o=document.createElement('option');o.value=v;o.textContent=v;cat.appendChild(o)});function filter(){let needle=q.value.toLowerCase().trim(),n=0;rows.forEach(r=>{let hit=(!needle||r.textContent.toLowerCase().includes(needle))&&(!cat.value||r.dataset.cat===cat.value);r.classList.toggle('hidden',!hit);n+=hit?1:0});count.textContent=n+' manuals'}q.addEventListener('input',filter);cat.addEventListener('change',filter);filter()</script></html>"""
    (root / "Field_Manual_Catalog.html").write_text(html_page, encoding="utf-8")


def combine_selective_catalogs(root: Path, active: list[dict],
                               cache: CatalogCache | None = None) -> list[dict]:
    """Merge per-category manifests, normalizing stale metadata from SQLite."""
    merged: dict[str, dict] = {}
    for file in sorted(root.glob("trex_manifest_selective_*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
            for entry in data:
                if isinstance(entry, dict) and entry.get("source_url"):
                    cached = cache.metadata(entry["source_url"]) if cache else None
                    merged[entry["source_url"]] = align_manifest_metadata(entry, cached, True)
        except (OSError, ValueError):
            print(f"  WARNING: Could not merge saved catalog {file.name}")
    for entry in active:
        if entry.get("source_url"):
            merged[entry["source_url"]] = entry
    return list(merged.values())


def log_error(path: Path, source: str, ext: str, error: str) -> None:
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["manual_page", "format", "error"])
        writer.writerow([source, ext, error])


def resolve_site_functions(spec: str) -> list[str]:
    """Accept exact site Function labels or slugs; never silently default to all."""
    lookup = {}
    for name, slug in FUNCTIONS.items():
        lookup[name.casefold()] = name
        lookup[slug.casefold()] = name
    selected = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        name = lookup.get(token.casefold())
        if not name:
            raise ValueError(f"Unknown site Function {token!r}. Available: {', '.join(FUNCTIONS)}")
        if name not in selected:
            selected.append(name)
    if not selected:
        raise ValueError("Choose at least one site Function to avoid an accidental full download")
    return selected


def ask_selective(browse_only: bool = False) -> tuple[list[str], str, str, int, float]:
    """Interactive selection before network requests, designed for Windows launcher."""
    names = list(FUNCTIONS)
    print("\nPICK WHICH SITE CATEGORIES TO UPDATE" if browse_only
          else "\nPICK WHICH SITE CATEGORIES TO ARCHIVE")
    print("Each category is crawled separately; other categories will not be scanned.")
    for i, name in enumerate(names, 1):
        print(f" {i:2}. {name:25}  {PRIMARY_NAMES.get(name, name)}")
    while True:
        raw = input("Categories, comma-separated numbers (e.g., 6,13): ").strip()
        try:
            pieces = [part.strip() for part in raw.split(",") if part.strip()]
            indexes = [int(part) for part in pieces]
            if not indexes or any(index < 1 or index > len(names) for index in indexes):
                raise ValueError("Enter one or more valid category numbers")
            selected = list(dict.fromkeys(names[index - 1] for index in indexes))
            break
        except ValueError as exc:
            print(f"  Please try again: {exc}")
    if browse_only:
        # Catalog updates neither download files nor need a format/GB budget.
        return selected, "", "both", 0, 0.0
    title = input("Optional title/designation keyword (Enter for all in selected categories): ").strip()
    while True:
        fmt = input("Formats: B=both, P=PDF only, E=EPUB only (default B): ").strip().lower()
        choice = {"": "both", "b": "both", "p": "pdf", "e": "epub"}.get(fmt)
        if choice is not None:
            break
        print("  Enter B, P or E")
    while True:
        raw = input("Maximum manuals (Enter for no count limit): ").strip()
        try:
            count = int(raw) if raw else 0
            if count < 0:
                raise ValueError()
            break
        except ValueError:
            print("  Enter a whole number 0 or higher")
    while True:
        raw = input("Maximum NEW data downloaded THIS RUN, GB (default 5; 0=unlimited): ").strip()
        try:
            gb = float(raw) if raw else 5.0
            if gb < 0 or not (gb < float('inf')):
                raise ValueError()
            break
        except ValueError:
            print("  Enter a non-negative number, e.g., 1.5, 5 or 10")
    return selected, title, choice, count, gb


def main() -> int:
    ap = argparse.ArgumentParser(description="Bulk-download the T.REX ARMS public Field Manual Vault")
    ap.add_argument("--version", action="version", version=VERSION)
    ap.add_argument("--dest", type=Path, default=Path.home() / "Documents" / "T.REX Field Manuals",
                    help="Root folder on this computer (existing folders are preserved)")
    ap.add_argument("--mode", choices=["organized", "topics", "functions", "full", "selective"], default="organized",
                    help="selective=only chosen site Functions in organized folders; organized=full site; others are legacy")
    ap.add_argument("--site-functions", default="", help="For selective mode: comma-separated site Functions or slugs")
    ap.add_argument("--interactive", action="store_true", help="Prompt for selective categories, formats, limits, and budget")
    ap.add_argument("--title-contains", default="", help="Selective mode: case-insensitive keyword in manual title or designation")
    ap.add_argument("--format", choices=["pdf", "epub", "both"], default="both", help="Which files to download")
    ap.add_argument("--max-new-gb", type=float, default=0, help="Cap NEW data written this run, in decimal GB (0 means no cap)")
    ap.add_argument("--categories", default="", help="For topics mode: comma-separated subset (e.g. First Aid,Survival)")
    ap.add_argument("--formats-folder", default="", help="Optional subfolder. Blank keeps PDF and EPUB beside one another")
    ap.add_argument("--dry-run", action="store_true", help="Build and inspect a manifest; do not download files")
    ap.add_argument("--refresh", action="store_true", help="Re-scan the website instead of using saved manifest")
    ap.add_argument("--update-only", action="store_true",
                    help="Refresh selected function listings; fetch NEW metadata and recheck a bounded old batch; never download binaries")
    ap.add_argument("--recheck-existing", type=int, default=25,
                    help="With --update-only, recheck up to N stale known detail pages (default 25)")
    ap.add_argument("--metadata-ttl-days", type=int, default=30,
                    help="Minimum age before old metadata is eligible for recheck (default 30 days; 0=any age)")
    ap.add_argument("--estimate-samples", type=int, default=0,
                    help="Before download, do up to N file HEAD probes for a rough size estimate (0=offline only; Windows launcher uses 6)")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help="Metadata fetch workers, 1-4 (default 2; global request pacing always applies)")
    ap.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL,
                    help="Seconds between HTML request starts, combined across workers (minimum 0.5)")
    ap.add_argument("--index-only", action="store_true",
                    help="Rebuild HTML catalog from existing shared cache with NO network access")
    ap.add_argument("--limit", type=int, default=0, help="Download only first N manuals (for testing)")
    ap.add_argument("--audit-only", action="store_true", help="Only inventory and report exact duplicate local files; no internet")
    args = ap.parse_args()
    if args.limit < 0:
        ap.error("--limit must be nonnegative")
    if args.max_new_gb < 0 or not args.max_new_gb < float("inf"):
        ap.error("--max-new-gb must be a finite non-negative number")
    if args.recheck_existing < 0 or args.recheck_existing > 500:
        ap.error("--recheck-existing must be from 0 to 500")
    if args.metadata_ttl_days < 0 or args.metadata_ttl_days > 3650:
        ap.error("--metadata-ttl-days must be 0 to 3650")
    if args.estimate_samples < 0 or args.estimate_samples > 40:
        ap.error("--estimate-samples must be between 0 and 40")
    if args.update_only and args.mode not in {"selective", "functions"}:
        ap.error("--update-only requires --mode selective or --mode functions")
    if not 1 <= args.workers <= 4:
        ap.error("--workers must be between 1 and 4")
    if not 0.5 <= args.request_interval <= 30:
        ap.error("--request-interval must be from 0.5 to 30 seconds")
    global PAUSE_BETWEEN_HTML
    PAUSE_BETWEEN_HTML = args.request_interval
    if args.interactive and args.mode != "selective":
        ap.error("--interactive is only for --mode selective")
    if (args.site_functions or args.title_contains) and args.mode != "selective":
        ap.error("--site-functions and --title-contains require --mode selective")
    if args.mode == "selective":
        if args.interactive:
            try:
                chosen_functions, args.title_contains, args.format, args.limit, args.max_new_gb = ask_selective(
                    browse_only=True) if args.update_only else ask_selective()
            except (KeyboardInterrupt, EOFError):
                print("\nCancelled before any downloads.")
                return 130
        else:
            try:
                chosen_functions = resolve_site_functions(args.site_functions)
            except ValueError as exc:
                ap.error(str(exc))
    else:
        chosen_functions = []
    selected = list(TOPICS)
    if args.categories:
        selected = [x.strip() for x in args.categories.split(",") if x.strip()]
        unknown = [x for x in selected if x not in TOPICS]
        if unknown:
            ap.error(f"Unknown topics: {unknown}; choices: {list(TOPICS)}")
        if args.mode != "topics":
            ap.error("--categories is only valid with --mode topics")
    root = args.dest.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    cache = CatalogCache(root / "trex_cache.sqlite3")
    try:
        return _main_with_cache(args, root, cache, chosen_functions, selected)
    finally:
        cache.close()


def _main_with_cache(args, root: Path, cache: CatalogCache,
                     chosen_functions: list[str], selected: list[str]) -> int:
    imported = cache.import_old_manifests(root)
    if imported:
        print(f"Imported {imported} manual records from existing v2/v3 manifests (no network)")
    repairs = cache.repair_legacy_metadata()
    if repairs:
        print(f"Metadata quality: repaired {len(repairs)} numeric field values "
              f"in {len({r['source_url'] for r in repairs})} cached manuals (offline)")
        # Keep an inspectable local log of values removed and inferred categories.
        # This file stays in the user's library, not in the GitHub package.
        audit = root / "Field_Manual_Metadata_Repairs.csv"
        append = audit.exists()
        with audit.open("a", newline="", encoding="utf-8-sig" if not append else "utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["source_url", "field", "previous_value", "new_value"])
            if not append:
                writer.writeheader()
            writer.writerows(repairs)
    if args.index_only:
        entries = [_entry_with_category(row, row.get("site_function", ""), True)
                   for row in cache.all_metadata()]
        write_offline_index(entries, root, args.formats_folder)
        # The prior report may show warnings already fixed in SQLite. Replace
        # it with an explicitly offline-scoped cache report, not a fake scan.
        write_coverage_report(root, cache, "offline", [], {}, [])
        print(f"Offline catalog rebuilt from {len(entries)} cached manuals; no network requests")
        return 0
    if args.audit_only:
        inventory_existing(root, root / "Field_Manual_Existing_Files.csv",
                           root / "Field_Manual_Exact_Duplicates.csv", cache=cache)
        print("Audit only: no manuals were downloaded, moved, or deleted.")
        return 0
    cache_key = args.mode
    if args.mode == "topics":
        cache_key += "_" + hashlib.sha256(",".join(selected).encode()).hexdigest()[:8]
    if args.mode == "selective":
        cache_key += "_" + hashlib.sha256(",".join(sorted(chosen_functions)).encode()).hexdigest()[:10]
    json_path = root / f"trex_manifest_{cache_key}.json"
    csv_path = root / f"trex_manifest_{cache_key}.csv"
    discovery_path = root / f"trex_discovery_{cache_key}.json"
    progress_path = root / f"trex_metadata_progress_{cache_key}.json"
    incomplete_path = root / f"trex_incomplete_{cache_key}.json"
    print("T.REX Field Manual Vault -- LOCAL archiver")
    print(f"Destination: {root}")
    print(f"Mode: {args.mode} | Dry run: {args.dry_run}")
    if chosen_functions:
        print("Site Functions: " + ", ".join(chosen_functions))
    print(f"Metadata workers: {args.workers}; shared HTML request interval: "
          f"{args.request_interval}s")
    print("Binary downloads are sequential; valid existing files are skipped. Ctrl+C stops safely.\n")
    scan_stats: dict = {}
    metadata_stats: dict = {}
    discovered: dict[str, str] | None = None
    # V3/V4 manifests remain readable. A recent manifest loads instantly;
    # selective mode automatically checks expired listings after seven days.
    fresh_saved = (json_path.is_file() and not args.refresh and not args.update_only
                   and not incomplete_path.exists()
                   and (args.mode not in {"selective", "functions"}
                        or time.time() - json_path.stat().st_mtime <= LISTING_TTL_SECONDS))
    if fresh_saved:
        print(f"Reusing recent saved catalog: {json_path.name} (no web requests for metadata)")
        entries = json.loads(json_path.read_text(encoding="utf-8"))
        # Older manifests need the same local corrections as SQLite, even
        # when an otherwise-fresh selection skips network discovery entirely.
        repaired_entries = [align_manifest_metadata(e, cache.metadata(e["source_url"]),
                           args.mode in {"organized", "selective"}) for e in entries]
        if repaired_entries != entries:
            entries = repaired_entries
            save_json_atomic(json_path, entries)
            print("Repaired cached selection manifest without re-downloading metadata")
    else:
        if args.refresh:
            # Do not delete a partially completed *metadata* refresh. It may
            # contain hours of successful results from an interrupted run.
            discovery_path.unlink(missing_ok=True)
        # A finished discovery snapshot is a convenience, not a reason to
        # reuse stale URLs forever. Interrupted listing scans use SQLite
        # page checkpoints regardless of this selection-specific JSON.
        reuse_discovery = (discovery_path.is_file() and not args.refresh and not args.update_only
                           and not incomplete_path.exists()
                           and time.time() - discovery_path.stat().st_mtime <= LISTING_TTL_SECONDS)
        if reuse_discovery:
            found = json.loads(discovery_path.read_text(encoding="utf-8"))
            print(f"Resuming discovery from saved catalog: {discovery_path.name}")
        else:
            if args.mode == "topics":
                found = crawl_topics(selected)
            elif args.mode == "functions":
                found = crawl_functions(cache=cache, refresh=(args.refresh or args.update_only),
                                        stats=scan_stats)
            elif args.mode == "selective":
                found = crawl_functions(chosen_functions, cache=cache,
                                        refresh=(args.refresh or args.update_only), stats=scan_stats)
            else:
                found = crawl_organized(cache=cache, refresh=args.refresh, stats=scan_stats)
            if found:
                save_json_atomic(discovery_path, found)
        # Never drop previously indexed editions just because a changed site
        # filter stopped exposing them. Keep them as unverified historical
        # catalog entries; this is not proof that the download still works.
        if json_path.is_file() and args.mode in {"selective", "functions"}:
            try:
                previous = json.loads(json_path.read_text(encoding="utf-8"))
                retained = 0
                for row in previous:
                    url = row.get("source_url", "") if isinstance(row, dict) else ""
                    if normalized_detail(url, BASE) == url and url not in found:
                        found[url] = row.get("site_function", "")
                        retained += 1
                if retained:
                    print(f"Retained {retained} older catalog URLs not exposed by latest listing")
            except (OSError, ValueError, TypeError) as exc:
                print(f"  WARNING: saved catalog could not be merged: {exc}")
        # Old V5 discovery JSON can still contain non-manual site pages.
        # Remove them before scheduling metadata or reporting parse failures.
        excluded = len(found) - sum(normalized_detail(url, BASE) == url for url in found)
        found = {url: function for url, function in found.items()
                 if normalized_detail(url, BASE) == url}
        if excluded:
            print(f"Excluded {excluded} non-manual catalog link(s) from saved discovery")
        discovered = found
        print(f"\nDiscovered {len(found)} distinct manual detail pages.")
        if not found:
            print("ERROR: No manuals discovered; possible site layout change, network block or site outage.")
            return 2
        entries = build_manifest(found, progress_path,
                                 organized=(args.mode in {"organized", "selective"}),
                                 cache=cache, workers=args.workers, refresh=args.refresh,
                                 recheck_limit=(args.recheck_existing if args.update_only else 0),
                                 recheck_age=args.metadata_ttl_days * 86400,
                                 stats=metadata_stats)
        if not entries:
            print("ERROR: No valid manual download links could be parsed.")
            return 2
        save_json_atomic(json_path, entries)
        progress_path.unlink(missing_ok=True)
        print(f"Saved manifest cache: {json_path.name}")
        if scan_stats.get("listings_failed") or metadata_stats.get("metadata_failed"):
            save_json_atomic(incomplete_path, {
                "listing_failures": scan_stats.get("listings_failed", []),
                "metadata_failed_urls": metadata_stats.get("metadata_failed_urls", []),
                "note": "The next run must retry discovery even when the manifest is recent",
            })
            print(f"Partial catalog warning: {incomplete_path.name} will force retry next run")
        else:
            incomplete_path.unlink(missing_ok=True)
    write_csv(entries, csv_path)
    index_entries = combine_selective_catalogs(root, entries, cache) if args.mode == "selective" else entries
    write_offline_index(index_entries, root, args.formats_folder)
    print(f"Indexed {len(entries)} manuals; readable CSV: {csv_path.name}")
    write_coverage_report(root, cache, args.mode, chosen_functions,
                          discovered, entries, scan_stats, metadata_stats)
    print("Category counts:")
    from collections import Counter
    for k, v in sorted(Counter(e["category"] for e in entries).items()):
        print(f"  {k:24} {v:5}")
    chosen = entries
    if args.mode == "selective":
        allowed = set(chosen_functions)
        chosen = [e for e in chosen if e.get("site_function") in allowed]
        if args.title_contains:
            needle = args.title_contains.casefold()
            chosen = [e for e in chosen if needle in
                      (e.get("title", "") + " " + e.get("designation", "")).casefold()]
    if args.limit:
        chosen = chosen[:args.limit]
    if args.update_only:
        print("\nINCREMENTAL CATALOG UPDATE COMPLETE: only metadata was fetched; no PDFs/EPUBs downloaded.")
        print("Existing manuals and folders were not changed. Run selective download to fetch newly listed titles.")
        return 0 if not scan_stats.get("listings_failed") and not metadata_stats.get("metadata_failed") else 1
    print(f"SELECTED: {len(chosen)} manuals; format={args.format}; cap="
          f"{'unlimited' if not args.max_new_gb else str(args.max_new_gb) + ' GB new data per run'}")
    if not chosen:
        print("No manuals matched. No files downloaded; try another category or keyword.")
        return 0
    # The CLI remains network-free by default for size estimation (0 HEADs).
    # Windows passes a small explicit sample (6) before user confirmation.
    storage = estimate_storage(chosen, root, args.format, args.formats_folder,
                               cache, 0 if args.dry_run else args.estimate_samples)
    if args.mode == "selective" and args.interactive and not args.dry_run:
        print("\nFirst selected manuals:")
        for e in chosen[:12]:
            print(f"  [{e['category']}] {e['designation']} - {e['title']}")
        print("No original files will be moved, renamed or deleted.")
        try:
            if input("Type YES to download this selection: ").strip() != "YES":
                print("Cancelled. Your catalog was saved; no files downloaded.")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled. No files downloaded.")
            return 130
    if args.dry_run:
        print("\nDry run finished: zero PDF/EPUB files downloaded.")
        return 0

    failures = 0
    counts = {"downloaded": 0, "resumed": 0, "skipped": 0, "linked": 0}
    hash_index = (inventory_existing(root, root / "Field_Manual_Existing_Files.csv",
                                     root / "Field_Manual_Exact_Duplicates.csv", cache=cache)
                  if args.mode in {"organized", "selective"} else None)
    budget = DownloadBudget(int(args.max_new_gb * 1_000_000_000))
    errors_path = root / "trex_download_errors.csv"
    stopped_at_cap = False
    try:
        for i, entry in enumerate(chosen, 1):
            print(f"\n[{i}/{len(chosen)}] [{entry['category']}] {entry['designation']} - {entry['title']}", flush=True)
            for ext, url_field in ((".pdf", "pdf_url"), (".epub", "epub_url")):
                if args.format != "both" and ext != "." + args.format:
                    continue
                url = entry.get(url_field)
                if not url:
                    print(f"   {ext}: not supplied by website")
                    continue
                target = destination_file(root, entry, ext, args.formats_folder)
                if valid_file(target, ext):
                    counts["skipped"] += 1
                    cache.put_file_size(url, target.stat().st_size, "local-file")
                    print(f"   {ext}: already downloaded; skipped")
                    continue
                negative = None if args.refresh else cache.negative_code(url)
                if negative is not None:
                    print(f"   {ext}: previously returned HTTP {negative}; "
                          "skipping for 7 days (use --refresh to check again)")
                    continue
                try:
                    status = download_with_resume(url, target, ext, hash_index, budget=budget)
                    cache.clear_negative(url)
                    counts[status] += 1
                    cache.put_file_size(url, target.stat().st_size, "download")
                    print(f"   {ext}: {status} ({target.stat().st_size / (1024*1024):.1f} MiB)", flush=True)
                except BudgetReached as exc:
                    print(f"   STOP: {exc}")
                    stopped_at_cap = True
                    break
                except PermanentMissingError as exc:
                    failures += 1
                    code = int(re.search(r"HTTP (404|410)", str(exc)).group(1))
                    cache.put_negative(url, code)
                    print(f"   {ext}: unavailable (HTTP {code}); no pointless retries")
                    log_error(errors_path, entry["source_url"], ext, str(exc))
                except Exception as exc:
                    failures += 1
                    print(f"   {ext}: ERROR {exc}", flush=True)
                    log_error(errors_path, entry["source_url"], ext, str(exc))
            if stopped_at_cap:
                break
            if i % 10 == 0 or i == len(chosen):
                done = sum(counts.values())
                budget_label = (f" / {budget.max_bytes / 1e9:.2f} GB cap"
                                if budget.max_bytes else "")
                print(f"Progress: {i}/{len(chosen)} manuals visited; {done} files handled; "
                      f"{budget.used_bytes / 1e9:.3f} GB new{budget_label}; "
                      f"{failures} errors", flush=True)
    except KeyboardInterrupt:
        print("\nStopped by user. Re-run this program to resume using the saved catalog and partial files.")
        return 130
    write_offline_index(index_entries, root, args.formats_folder)
    print("\nPAUSED AT PER-RUN DOWNLOAD CAP" if stopped_at_cap else "\nCOMPLETE")
    print(f"Network data written this run: {budget.used_bytes / 1e9:.3f} GB")
    print(f"New downloads: {counts['downloaded']} | Resumed: {counts['resumed']} | "
          f"Skipped: {counts['skipped']} | Hard-linked (no extra bytes): {counts['linked']} | Failures: {failures}")
    print(f"Archive: {root}")
    if stopped_at_cap:
        print("Increase the per-run cap or rerun later; incomplete .part downloads are resumable.")
    if failures:
        print(f"Review {errors_path.name} and re-run to retry failed downloads.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
