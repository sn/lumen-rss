"""RSS Reader: your feeds in a tab of their own, or in a panel beside what
you're doing. Choose RSS Reader in the palette to open it in a tab, or RSS
Reader (Panel) to open it to the right of the pane you're in. Either goes
back to it if it's already open. The tab (or the panel's label) shows how
many items are unread.

Your feeds are on the left, with All first; the items of the one chosen are
on the right, newest first, unread ones marked. Keys: ↑/↓ (or j/k) move,
←/→ (or h/l, Tab) switch between feeds and items, Enter opens an item in
your browser and marks it read, a marks everything shown read, r refreshes,
q closes.

Feeds are the `feeds` setting (a list of addresses), checked every
`refresh-minutes`. What you've read is kept in the plugin's data folder.
"""

import asyncio
import subprocess
import sys
import time
import unicodedata

import feeds
from lumen_plugin import LumenError, Plugin

app = Plugin()
seen = feeds.Seen(app.data_dir)

DIM, BOLD, OFF = "\x1b[2m", "\x1b[1m", "\x1b[0m"
ACCENT, RED, INVERT = "\x1b[34m", "\x1b[31m", "\x1b[7m"


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 0 if unicodedata.combining(c) else 1 for c in s)


def fit(s: str, w: int) -> str:
    """`s` cut to `w` columns with "…", or padded to them."""
    if w <= 0:
        return ""
    if width(s) <= w:
        return s + " " * (w - width(s))
    out, used = "", 0
    for c in s:
        cw = width(c)
        if used + cw > w - 1:
            break
        out, used = out + c, used + cw
    return out + "…" + " " * (w - 1 - used)


def urls() -> list:
    """The feeds setting, as addresses worth fetching."""
    raw = app.settings.get("feeds") or []
    return [u.strip() for u in raw if isinstance(u, str) and u.strip().startswith(("http://", "https://", "file://"))]


def open_link(link: str) -> None:
    # Only web addresses: a feed is someone else's text.
    if not link.startswith(("http://", "https://")):
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, link], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        app.log("couldn't open", link, e)


class Reader:
    def __init__(self, view, panel=False):
        self.view = view
        self.panel = panel  # a split in someone else's tab, not a tab of its own
        self.feeds = {}  # address -> feeds.Feed
        self.errors = {}  # address -> what went wrong
        self.order = urls()
        self.column = "items"
        self.feed = 0  # 0 is All
        self.item = 0
        self.top = 0
        self.refreshing = False
        self.updated = None

    # What's shown.
    def sources(self):
        """The left column: (label, address or None for All)."""
        return [("All", None)] + [(self.feeds[u].title if u in self.feeds else feeds._host(u), u) for u in self.order]

    def items(self):
        _, url = self.sources()[min(self.feed, len(self.order))]
        if url is None:
            return feeds.newest_first([i for u in self.order if u in self.feeds for i in self.feeds[u].items])
        return self.feeds[url].items if url in self.feeds else []

    def unread(self, url=None) -> int:
        chosen = self.order if url is None else [url]
        return sum(not seen.read(i.id) for u in chosen if u in self.feeds for i in self.feeds[u].items)

    def lines(self):
        cols, rows = self.view.cols, self.view.rows
        total = self.unread()
        status = "refreshing…" if self.refreshing else (f"updated {feeds.ago(self.updated)} ago" if self.updated else "")
        status = status.replace("now ago", "just now")
        out = [f"{BOLD} RSS{OFF}{DIM}  {total} unread  ·  {status}{OFF}", ""]
        body = max(1, rows - 4)
        if not self.order:
            out += [
                f"{DIM} No feeds yet. Open Plugins in the palette, choose RSS Reader and press s,{OFF}",
                f"{DIM} or add them to config.toml:{OFF}",
                "",
                '   [plugin."sn/rss"]',
                '   feeds = ["https://example.com/feed.xml"]',
            ]
            return self.footer(out, cols, rows)
        left_w = min(30, max(14, cols // 4)) if cols >= 60 else 0
        right_w = cols - left_w - (3 if left_w else 1)
        left = []
        for n, (label, url) in enumerate(self.sources()):
            count = self.unread(url)
            mark = f"{RED}!{OFF} " if url in self.errors else "  "
            text = fit(label, left_w - 8) + (f"{count:>5}" if count else "     ")
            here = n == self.feed
            if here and self.column == "feeds":
                text = f"{INVERT}{text}{OFF}"
            elif here:
                text = f"{BOLD}{text}{OFF}"
            left.append(mark + text)
        items = self.items()
        self.item = max(0, min(self.item, len(items) - 1))
        if self.item < self.top:
            self.top = self.item
        elif self.item >= self.top + body:
            self.top = self.item - body + 1
        right = []
        _, url = self.sources()[min(self.feed, len(self.order))]
        if url in self.errors:
            right.append(f"{RED} {fit(self.errors[url], right_w - 2)}{OFF}")
        elif not items:
            right.append(f"{DIM} {'Fetching…' if self.refreshing else 'Nothing here yet.'}{OFF}")
        show_source = url is None
        now = time.time()
        for n, it in enumerate(items[self.top:self.top + body], start=self.top):
            new = not seen.read(it.id)
            when = feeds.ago(it.published, now)
            prefix = f"{'●' if new else ' '} {when:>6}  "
            src = fit(it.source, 16) + "  " if show_source and right_w > 70 else ""
            text = fit(prefix + src + (it.title or it.link), right_w - 1)
            if n == self.item and self.column == "items":
                text = f"{INVERT}{text}{OFF}"
            elif not new:
                text = f"{DIM}{text}{OFF}"
            elif n == self.item:
                text = f"{BOLD}{text}{OFF}"
            right.append(" " + text)
        for r in range(body):
            # Each feed row is the mark (2) and the label with its count (left_w - 3).
            l = left[r] if r < len(left) else " " * (left_w - 1)
            line = (l + f" {DIM}│{OFF}" if left_w else "") + (right[r] if r < len(right) else "")
            out.append(line)
        return self.footer(out, cols, rows)

    def footer(self, out, cols, rows):
        hints = [
            "↑↓ move · ←→ feeds/items · enter open · a all read · r refresh · q close",
            "↑↓ · ←→ · enter open · a read · r refresh · q close",
            "enter · r · q",
        ]
        hint = next((h for h in hints if width(h) <= cols - 2), "")
        out = out[: max(1, rows - 1)]
        return out + [""] * max(0, rows - 1 - len(out)) + [f"{DIM} {hint}{OFF}"]

    async def show(self):
        if self.view.closed:
            return
        n = self.unread()
        try:
            await self.view.draw(self.lines())
            name = f"RSS ({n})" if n else "RSS"
            if self.panel:
                # The tab belongs to whatever it was opened beside: label the split.
                await self.view.set_pane_style(label=name)
            else:
                await self.view.set_title(name)
        except LumenError:
            pass

    # What happens.
    async def refresh(self):
        if self.refreshing:
            return
        self.order = urls()
        self.feed = min(self.feed, len(self.order))
        self.refreshing = True
        await self.show()
        try:
            got, errors = await asyncio.to_thread(feeds.fetch_all, self.order)
            self.feeds, self.errors = got, errors
            self.updated = time.time()
            for url, err in errors.items():
                app.log(url, err)
        finally:
            self.refreshing = False
        await self.show()

    async def key(self, name):
        items = self.items()
        if name in ("down", "j"):
            if self.column == "feeds":
                self.feed, self.item, self.top = min(len(self.order), self.feed + 1), 0, 0
            else:
                self.item = min(len(items) - 1, self.item + 1)
        elif name in ("up", "k"):
            if self.column == "feeds":
                self.feed, self.item, self.top = max(0, self.feed - 1), 0, 0
            else:
                self.item = max(0, self.item - 1)
        elif name in ("left", "h"):
            self.column = "feeds"
        elif name in ("right", "l", "tab"):
            self.column = "items"
        elif name == "enter":
            if self.column == "feeds":
                self.column = "items"
            elif items:
                it = items[self.item]
                open_link(it.link)
                seen.mark(it.id)
        elif name == "a":
            seen.mark(*[i.id for i in items])
        elif name == "r":
            await self.refresh()
            return
        else:
            return
        await self.show()


reader = None


async def every_so_often():
    while reader and not reader.view.closed:
        minutes = app.settings.get("refresh-minutes") or 30
        await asyncio.sleep(max(5, int(minutes)) * 60)
        if reader and not reader.view.closed:
            await reader.refresh()


PANEL_SHARE = 0.3  # of the pane it's opened beside


async def open_reader(beside=None):
    """Open the reader, or go back to it. `beside` is the pane to open it to
    the right of; without one it gets a tab."""
    global reader
    if reader and not reader.view.closed:
        await reader.view.focus()
        return
    if beside is not None:
        view = await beside.split("right", size=PANEL_SHARE, plugin_pane=True)
    else:
        view = await app.new_tab(plugin_pane=True)
    r = reader = Reader(view, panel=beside is not None)

    @view.on_key
    async def key(name):
        if name == "q":
            await view.close()
            return
        await r.key(name)

    view.on_resize(r.show)
    asyncio.ensure_future(r.refresh())
    asyncio.ensure_future(every_so_often())


# The id it has always had, so existing key bindings keep working.
@app.command("RSS Reader", id="open_reader")
async def in_tab(pane):
    await open_reader()


@app.command("RSS Reader (Panel)", id="open_panel")
async def in_panel(pane):
    await open_reader(pane)


@app.on("settings-changed")
async def settings_changed(event):
    if reader and not reader.view.closed:
        await reader.refresh()


app.run()
