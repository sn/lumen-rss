"""Feeds: reading RSS and Atom, fetching several at once, and remembering
what you've read. No Lumen here, so it can be tested on its own."""

import datetime
import email.utils
import html
import json
import os
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

ATOM = "{http://www.w3.org/2005/Atom}"
TIMEOUT = 15  # for each read from the network
DEADLINE = 30  # for the whole feed
MAX_BYTES = 5 * 1024 * 1024
USER_AGENT = "lumen-rss/1.0 (+https://github.com/sn/lumen-rss)"


class FeedError(Exception):
    pass


@dataclass
class Item:
    id: str
    title: str
    link: str
    published: float | None
    source: str


@dataclass
class Feed:
    url: str
    title: str
    items: list = field(default_factory=list)


# Bidi controls make text show as something it isn't.
_BIDI = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean(s: str) -> str:
    """`s` without control characters: a feed is a stranger's text, drawn
    into a terminal, where C1 controls (like U+009B, CSI) could start escape
    sequences. Whitespace is collapsed."""
    s = "".join(c for c in s if c not in _BIDI and (c.isspace() or unicodedata.category(c) != "Cc"))
    return " ".join(s.split())


def _text(el) -> str:
    """An element's text, cleaned ("" if missing)."""
    if el is None:
        return ""
    return clean("".join(el.itertext()))


def _title(el) -> str:
    """A title as shown: entities in HTML titles (and RSS's, which are often
    escaped twice) turned back into characters."""
    return clean(html.unescape(_text(el)))


def _rfc822(s: str):
    try:
        return email.utils.parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def _iso(s: str):
    try:
        d = datetime.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or url


def newest_first(items):
    """Newest first; items without a date last, in the order given."""
    return sorted(items, key=lambda i: (i.published is None, -(i.published or 0)))


def parse(data: bytes, url: str) -> Feed:
    """An RSS 2.0 or Atom feed, its items newest first."""
    # Entity declarations are how XML bombs and external-entity reads start;
    # real feeds don't need them.
    if b"<!ENTITY" in data:
        raise FeedError("feeds that declare their own entities aren't read")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise FeedError(f"not a feed: {e}") from None
    if root.tag == f"{ATOM}feed":
        title = _title(root.find(f"{ATOM}title")) or _host(url)
        items = []
        for e in root.findall(f"{ATOM}entry"):
            links = e.findall(f"{ATOM}link")
            link = clean(next((l.get("href", "") for l in links if l.get("rel", "alternate") == "alternate"), ""))
            date = _text(e.find(f"{ATOM}published")) or _text(e.find(f"{ATOM}updated"))
            name = _title(e.find(f"{ATOM}title"))
            items.append(Item(_text(e.find(f"{ATOM}id")) or link or name, name, link, _iso(date) if date else None, title))
        return Feed(url, title, newest_first(items))
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        raise FeedError("not an RSS or Atom feed")
    title = _title(channel.find("title")) or _host(url)
    items = []
    for e in channel.findall("item"):
        link = _text(e.find("link"))
        name = _title(e.find("title"))
        date = _text(e.find("pubDate"))
        items.append(Item(_text(e.find("guid")) or link or name, name, link, _rfc822(date) if date else None, title))
    return Feed(url, title, newest_first(items))


def read_within(stream, clock=time.monotonic) -> bytes:
    """All of `stream`, unless it takes longer than DEADLINE seconds (a
    server trickling bytes would otherwise hold every feed up) or is bigger
    than MAX_BYTES."""
    start = clock()
    data = bytearray()
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            return bytes(data)
        data += chunk
        if len(data) > MAX_BYTES:
            raise FeedError("bigger than 5 MB")
        if clock() - start > DEADLINE:
            raise FeedError(f"took longer than {DEADLINE} seconds")


def fetch(url: str) -> bytes:
    """A feed's bytes: http(s) or a local file:// path."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return read_within(r)


def fetch_all(urls, get=fetch):
    """Fetch and parse every feed at once. Returns the feeds that worked, by
    address, and what went wrong with the others."""
    def one(url):
        try:
            return url, parse(get(url), url), None
        except urllib.error.HTTPError as e:
            return url, None, f"{_host(url)} answered {e.code} {e.reason}"
        except urllib.error.URLError as e:
            reason = getattr(e.reason, "strerror", None) or str(e.reason)
            return url, None, f"couldn't reach {_host(url)}: {reason}"
        except Exception as e:  # noqa: BLE001 - one feed's trouble is reported, not raised
            return url, None, str(e) or e.__class__.__name__

    got, errors = {}, {}
    if not urls:
        return got, errors
    with ThreadPoolExecutor(max_workers=min(8, len(urls))) as pool:
        for url, feed, err in pool.map(one, urls):
            if feed is not None:
                got[url] = feed
            else:
                errors[url] = err
    return got, errors


class Seen:
    """The ids of items you've read, kept in `seen.json`: the newest `cap`."""

    def __init__(self, folder: str, cap: int = 5000):
        self.path = os.path.join(folder, "seen.json")
        self.cap = cap
        try:
            with open(self.path) as f:
                ids = json.load(f)
            self.ids = [str(i) for i in ids] if isinstance(ids, list) else []
        except (OSError, ValueError):
            self.ids = []
        self._set = set(self.ids)

    def read(self, id: str) -> bool:
        return id in self._set

    def mark(self, *ids: str) -> None:
        new = [i for i in dict.fromkeys(ids) if i not in self._set]
        if not new:
            return
        self.ids = (self.ids + new)[-self.cap:]
        self._set = set(self.ids)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.ids, f)
        os.replace(tmp, self.path)


def ago(ts, now=None) -> str:
    """How long ago, shortly: now, 5m, 3h, 2d, or the date after a month."""
    if ts is None:
        return ""
    now = time.time() if now is None else now
    s = max(0, now - ts)
    if s < 60:
        return "now"
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    if s < 30 * 86400:
        return f"{int(s // 86400)}d"
    return time.strftime("%-d %b", time.localtime(ts))
