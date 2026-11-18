# RSS Reader for Lumen

Read your RSS and Atom feeds in a tab of their own or in a panel beside your
work, with the unread count on the tab. A plugin for the [Lumen](https://github.com/lumenshell/lumen)
terminal.

Choose **RSS Reader** in the palette to open it in a tab, or **RSS Reader
(Panel)** to open it to the right of the pane you're in, where its label
shows the unread count. If it's already open, either one goes back to it;
press `q` first to move it. Your feeds are listed on the left, with
**All** first; the items of the one chosen are on the right, newest first,
with unread ones marked `●`. In a narrow panel, only the items are shown. Enter opens an item in your browser and marks it
read.

| Key | Does |
|---|---|
| ↑ / ↓ (j / k) | Move |
| ← / → (h / l, Tab) | Switch between feeds and items |
| Enter | Open the item in your browser (on a feed: go to its items) |
| a | Mark everything shown as read |
| r | Refresh now |
| q | Close |

## Install

Open **Plugins** in Lumen's palette, find it under Browse and press `i`, or:

```sh
lumen plugins install sn/rss
```

It asks for `panes`: to open its tab or panel, draw it and show the unread
count on it.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `feeds` | none | Feed addresses (RSS or Atom) |
| `refresh-minutes` | 30 | How often to check them (5 to 1440) |

Add feeds with `s` in **Plugins**, or in `config.toml`:

```toml
[plugin."sn/rss"]
feeds = [
  "https://blog.rust-lang.org/feed.xml",
  "https://hnrss.org/frontpage",
]
refresh-minutes = 15
```

Changes apply at once, without restarting the plugin. What you've read is
kept in the plugin's data folder (the newest 5000 items).

Feeds are fetched over the network by the plugin itself, each with a 15
second limit, up to 5 MB, and a feed that fails doesn't stop the others: it's
marked `!` with what went wrong. Only `http` and `https` links are opened.

## Development

Standard library only (Python 3.11+).

```sh
python3 -m unittest discover -s tests
lumen plugins check .
```

## License

MIT
