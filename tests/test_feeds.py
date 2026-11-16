"""Tests for feed parsing and read-tracking, without a running Lumen.

    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feeds  # noqa: E402

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Lumen News</title>
  <item><title>Older</title><link>https://example.com/1</link><guid>id-1</guid>
        <pubDate>Mon, 16 Nov 2026 08:00:00 +0000</pubDate></item>
  <item><title>Newer</title><link>https://example.com/2</link>
        <pubDate>Mon, 16 Nov 2026 09:30:00 +0000</pubDate></item>
  <item><title>  Undated
   one </title><link>https://example.com/3</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Blog</title>
  <entry><title>First post</title><id>tag:blog,2026:1</id>
    <link rel="alternate" href="https://blog.example/1"/>
    <updated>2026-11-15T10:00:00Z</updated></entry>
  <entry><title type="html">Second &amp;amp; last</title><id>tag:blog,2026:2</id>
    <link rel="self" href="https://blog.example/feed"/>
    <link href="https://blog.example/2"/>
    <published>2026-11-16T07:00:00+02:00</published></entry>
</feed>"""


class ParseTests(unittest.TestCase):
    def test_rss2(self):
        f = feeds.parse(RSS, "https://example.com/feed")
        self.assertEqual(f.title, "Lumen News")
        self.assertEqual([i.title for i in f.items], ["Newer", "Older", "Undated one"])
        older = f.items[1]
        self.assertEqual((older.id, older.link, older.source), ("id-1", "https://example.com/1", "Lumen News"))
        self.assertEqual(f.items[0].id, "https://example.com/2", "no guid: the link is its id")
        self.assertEqual(f.items[0].published - older.published, 90 * 60)

    def test_atom(self):
        f = feeds.parse(ATOM, "https://blog.example/feed")
        self.assertEqual(f.title, "Atom Blog")
        # 07:00 at +02:00 on the 16th is 05:00Z: newer than 10:00Z on the 15th.
        self.assertEqual([i.title for i in f.items], ["Second & last", "First post"])
        self.assertEqual(f.items[0].link, "https://blog.example/2", "the alternate link, not self")
        self.assertEqual(f.items[1].id, "tag:blog,2026:1")
        self.assertEqual(f.items[0].published - f.items[1].published, 19 * 3600)

    def test_missing_dates_sort_last(self):
        f = feeds.parse(RSS, "u")
        self.assertIsNone(f.items[-1].published)
        merged = feeds.newest_first(f.items + feeds.parse(ATOM, "v").items)
        self.assertEqual(merged[-1].title, "Undated one")
        self.assertEqual(merged[0].title, "Newer")

    def test_a_feed_without_a_title_is_named_after_its_address(self):
        f = feeds.parse(b"<rss><channel><item><title>x</title></item></channel></rss>", "https://a.example/rss.xml")
        self.assertEqual(f.title, "a.example")

    def test_feeds_declaring_entities_are_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><rss><channel><title>&b;</title></channel></rss>'
        with self.assertRaisesRegex(feeds.FeedError, "entities"):
            feeds.parse(bomb, "u")
        xxe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss><channel><title>&x;</title></channel></rss>'
        with self.assertRaisesRegex(feeds.FeedError, "entities"):
            feeds.parse(xxe, "u")

    def test_control_characters_are_dropped(self):
        # U+009B is CSI: drawn into a terminal it could start an escape
        # sequence. U+202E turns text right to left.
        evil = "<rss><channel><title>Fe\u009b31med</title><item><title>a\u202eb\u0085c</title><link>https://x/\u009b1</link></item></channel></rss>"
        f = feeds.parse(evil.encode(), "u")
        self.assertEqual((f.title, f.items[0].title, f.items[0].link), ("Fe31med", "ab c", "https://x/1"), "NEL is a line break: a space")
        atom = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>t</title><link href="https://y/\u009b2"/></entry></feed>'
        self.assertEqual(feeds.parse(atom.encode(), "u").items[0].link, "https://y/2")

    def test_not_a_feed(self):
        with self.assertRaises(feeds.FeedError):
            feeds.parse(b"<html><body>hi</body></html>", "u")
        with self.assertRaises(feeds.FeedError):
            feeds.parse(b"not xml at all", "u")


class FetchAllTests(unittest.TestCase):
    def test_one_bad_feed_does_not_stop_the_rest(self):
        def fetch(url):
            if url == "down":
                raise OSError("timed out")
            if url == "html":
                return b"<html></html>"
            return RSS if url == "rss" else ATOM

        got, errors = feeds.fetch_all(["rss", "down", "html", "atom"], fetch)
        self.assertEqual(sorted(got), ["atom", "rss"])
        self.assertEqual(sorted(errors), ["down", "html"])
        self.assertIn("timed out", errors["down"])
        self.assertEqual(len(got["rss"].items), 3)

    def test_a_feed_that_trickles_in_is_given_up_on(self):
        class Trickle:
            def read(self, n):
                return b"x"  # forever, a byte at a time

        clock = iter(range(0, 10_000, 10))  # each read takes 10 seconds
        with self.assertRaisesRegex(feeds.FeedError, "30 seconds"):
            feeds.read_within(Trickle(), lambda: next(clock))

    def test_a_feed_is_read_whole(self):
        class Body:
            chunks = [b"<rss>", b"</rss>", b""]

            def read(self, n):
                return self.chunks.pop(0)

        self.assertEqual(feeds.read_within(Body(), lambda: 0), b"<rss></rss>")

    def test_network_errors_read_plainly(self):
        import urllib.error

        def fetch(url):
            raise urllib.error.URLError(OSError(-2, "Name or service not known"))

        _, errors = feeds.fetch_all(["https://nowhere.example/feed"], fetch)
        self.assertEqual(errors["https://nowhere.example/feed"], "couldn't reach nowhere.example: Name or service not known")


class SeenTests(unittest.TestCase):
    def test_seen_survives_restart_and_is_capped(self):
        d = tempfile.mkdtemp()
        s = feeds.Seen(d, cap=3)
        for i in range(5):
            s.mark(f"id-{i}")
        self.assertFalse(s.read("id-0"), "the oldest are forgotten past the cap")
        again = feeds.Seen(d, cap=3)
        self.assertTrue(again.read("id-4") and again.read("id-2"))
        self.assertEqual(json.load(open(os.path.join(d, "seen.json"))), ["id-2", "id-3", "id-4"])

    def test_marking_twice_keeps_one(self):
        s = feeds.Seen(tempfile.mkdtemp())
        s.mark("a")
        s.mark("a")
        self.assertEqual(s.ids, ["a"])

    def test_a_broken_file_starts_empty(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "seen.json"), "w").write("{nope")
        self.assertEqual(feeds.Seen(d).ids, [])


class AgoTests(unittest.TestCase):
    def test_ago(self):
        now = 1_800_000_000
        self.assertEqual(feeds.ago(now - 30, now), "now")
        self.assertEqual(feeds.ago(now - 5 * 60, now), "5m")
        self.assertEqual(feeds.ago(now - 3 * 3600, now), "3h")
        self.assertEqual(feeds.ago(now - 2 * 86400, now), "2d")
        self.assertEqual(feeds.ago(None, now), "")
        self.assertRegex(feeds.ago(now - 60 * 86400, now), r"^\d{1,2} [A-Z][a-z]{2}$")


if __name__ == "__main__":
    unittest.main()
