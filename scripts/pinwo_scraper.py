#!/usr/bin/env python3
"""
Polite scraper for the Pinwo Berlin Chinese restaurant directory.

Step 1: fetch /zh/locations once and extract every restaurant record
        embedded in the Next.js payload (no clicking needed).
Step 2: fetch each restaurant's detail page and extract its dish list.
Output: SQLite database (restaurants + dishes) and two CSV files.

Guardrails:
  - Checks robots.txt before fetching anything.
  - Honest User-Agent with a contact address.
  - Randomised delay between requests (default 2 to 5 seconds).
  - One request at a time, single session.
  - Retries with exponential backoff on 429/5xx, honours Retry-After.
  - Stops completely after repeated failures instead of hammering.
  - Caches every page on disk, so reruns resume without refetching.

Usage:
  uv run scripts/pinwo_scraper.py --contact you@example.com
  uv run scripts/pinwo_scraper.py --contact you@example.com --limit 5   # test run
"""

import argparse
import csv
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib import robotparser

import requests

BASE = "https://www.pinwo.de"
LIST_URL = f"{BASE}/zh/locations"
DETAIL_URL = BASE + "/zh/restaurants/{slug}"

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\]\)', re.S)


# ---------------------------------------------------------------- fetching

class PoliteFetcher:
    def __init__(self, contact, cache_dir, min_delay, max_delay,
                 max_retries=4, max_consecutive_failures=5):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"pinwo-personal-research-scraper/1.0 (contact: {contact})",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        })
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_delay, self.max_delay = min_delay, max_delay
        self.max_retries = max_retries
        self.max_consecutive_failures = max_consecutive_failures
        self.consecutive_failures = 0
        self.last_request = 0.0
        self.robots = robotparser.RobotFileParser(BASE + "/robots.txt")
        self.robots_loaded = False
        try:
            r = self.session.get(BASE + "/robots.txt", timeout=30)
            if r.status_code == 200:
                self.robots.parse(r.text.splitlines())
                self.robots_loaded = True
            else:
                print(f"[info] no robots.txt (HTTP {r.status_code})")
        except requests.RequestException as e:
            print(f"[warn] could not read robots.txt ({e}); continuing carefully")

    def _wait(self):
        delay = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.monotonic() - self.last_request
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def get(self, url, cache_name):
        cached = self.cache_dir / cache_name
        if cached.exists():
            return cached.read_text(encoding="utf-8")

        if self.robots_loaded and not self.robots.can_fetch(self.session.headers["User-Agent"], url):
            raise PermissionError(f"robots.txt disallows {url}")

        for attempt in range(1, self.max_retries + 1):
            self._wait()
            self.last_request = time.monotonic()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                status, err = None, str(e)
            else:
                if r.status_code == 200:
                    self.consecutive_failures = 0
                    cached.write_text(r.text, encoding="utf-8")
                    return r.text
                if r.status_code == 404:
                    print(f"[skip] 404 {url}")
                    return None
                status, err = r.status_code, f"HTTP {r.status_code}"
                if status == 403:
                    sys.exit(f"[stop] 403 Forbidden at {url}. The site may be "
                             "blocking automated access; stopping.")

            backoff = min(300, 10 * 2 ** (attempt - 1))
            if status == 429 and r.headers.get("Retry-After", "").isdigit():
                backoff = max(backoff, int(r.headers["Retry-After"]))
            print(f"[retry {attempt}/{self.max_retries}] {err} on {url}; "
                  f"waiting {backoff}s")
            time.sleep(backoff)

        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            sys.exit("[stop] too many consecutive failures; stopping to be safe.")
        return None


# ---------------------------------------------------------------- parsing

def rsc_payload(html):
    """Join the Next.js streamed payload chunks into one decoded string."""
    return "".join(json.loads(m.group(1)) for m in PUSH_RE.finditer(html))


def extract_objects(payload, start_marker):
    """Find every JSON object beginning with start_marker and decode it."""
    dec = json.JSONDecoder()
    out, pos = [], 0
    while (i := payload.find(start_marker, pos)) != -1:
        try:
            obj, end = dec.raw_decode(payload, i)
            out.append(obj)
            pos = end
        except json.JSONDecodeError:
            pos = i + 1
    return out


def parse_restaurants(html):
    payload = rsc_payload(html)
    seen = {}
    for obj in extract_objects(payload, '{"restaurantId":'):
        if isinstance(obj, dict) and obj.get("slug"):
            seen.setdefault(obj["slug"], obj)
    return list(seen.values())


def parse_detail(html):
    payload = rsc_payload(html)
    dishes = []
    for obj in extract_objects(payload, '{"items":[{"name":'):
        dishes.extend(obj.get("items", []))
    tel = re.search(r'"href":"tel:([^"]+)"', payload)
    return dishes, (tel.group(1) if tel else None)


# ---------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
  slug TEXT PRIMARY KEY, restaurant_id TEXT, name_zh TEXT, name_en TEXT,
  name_de TEXT, intro_zh TEXT, intro_en TEXT, address TEXT, neighborhood TEXT,
  cuisines_zh TEXT, cuisines_en TEXT, lat REAL, lng REAL, rating REAL,
  google_rating REAL, google_review_count INTEGER, price_range TEXT,
  operating_status TEXT, weekly_hours TEXT, phone TEXT, updated_at TEXT,
  raw_json TEXT
);
CREATE TABLE IF NOT EXISTS dishes (
  slug TEXT, name TEXT, price TEXT, spice_level TEXT, is_primary INTEGER,
  section TEXT, PRIMARY KEY (slug, name)
);
"""


def save_restaurant(db, r):
    g = lambda d, k: (d or {}).get(k)
    db.execute(
        """INSERT OR REPLACE INTO restaurants VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
            (SELECT phone FROM restaurants WHERE slug=?),?,?)""",
        (r["slug"], r.get("restaurantId"),
         g(r.get("name"), "zh"), g(r.get("name"), "en"), g(r.get("name"), "de"),
         g(r.get("shortIntro"), "zh"), g(r.get("shortIntro"), "en"),
         r.get("address"), r.get("neighborhood"),
         json.dumps(g(r.get("cuisineLabels"), "zh"), ensure_ascii=False),
         json.dumps(g(r.get("cuisineLabels"), "en"), ensure_ascii=False),
         g(r.get("location"), "lat"), g(r.get("location"), "lng"),
         r.get("rating"), r.get("googleRating"), r.get("googleReviewCount"),
         r.get("priceRange"), r.get("operatingStatus"),
         json.dumps(r.get("weeklyHours"), ensure_ascii=False),
         r["slug"], r.get("updatedAt"), json.dumps(r, ensure_ascii=False)))


def export_csv(db, out_dir):
    for table in ("restaurants", "dishes"):
        cur = db.execute(f"SELECT * FROM {table}")
        cols = [c[0] for c in cur.description if c[0] != "raw_json"]
        idx = [i for i, c in enumerate(cur.description) if c[0] != "raw_json"]
        with open(out_dir / f"{table}.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for row in cur:
                w.writerow([row[i] for i in idx])


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True,
                    help="email put in the User-Agent so the site can reach you")
    ap.add_argument("--out", default="pinwo_data")
    ap.add_argument("--min-delay", type=float, default=2.0)
    ap.add_argument("--max-delay", type=float, default=5.0)
    ap.add_argument("--limit", type=int, default=0,
                    help="only fetch this many detail pages (0 = all)")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    fetcher = PoliteFetcher(args.contact, out / "cache",
                            args.min_delay, args.max_delay)
    db = sqlite3.connect(out / "pinwo.sqlite")
    db.executescript(SCHEMA)

    html = fetcher.get(LIST_URL, "locations.html")
    if not html:
        sys.exit("[stop] could not fetch the directory page")
    restaurants = parse_restaurants(html)
    if not restaurants:
        sys.exit("[stop] found no restaurant records; the page structure may "
                 "have changed. Check pinwo_data/cache/locations.html")
    print(f"[ok] {len(restaurants)} restaurants in directory")
    for r in restaurants:
        save_restaurant(db, r)
    db.commit()

    todo = restaurants[: args.limit] if args.limit else restaurants
    for n, r in enumerate(todo, 1):
        slug = r["slug"]
        html = fetcher.get(DETAIL_URL.format(slug=slug), f"r_{slug}.html")
        if not html:
            continue
        dishes, phone = parse_detail(html)
        db.execute("UPDATE restaurants SET phone=? WHERE slug=?", (phone, slug))
        for d in dishes:
            db.execute("INSERT OR REPLACE INTO dishes VALUES (?,?,?,?,?,?)",
                       (slug, d.get("name"), d.get("price"), d.get("spiceLevel"),
                        int(bool(d.get("isPrimary"))), d.get("section")))
        db.commit()
        print(f"[{n}/{len(todo)}] {slug}: {len(dishes)} dishes")

    export_csv(db, out)
    total = db.execute("SELECT COUNT(*) FROM dishes").fetchone()[0]
    uniq = db.execute("SELECT COUNT(DISTINCT name) FROM dishes").fetchone()[0]
    print(f"[done] {total} dish rows, {uniq} unique dish names -> {out}/")


if __name__ == "__main__":
    main()
