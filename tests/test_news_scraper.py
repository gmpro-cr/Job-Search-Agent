from news_scraper import _parse_feed

# A Google-News-style RSS feed: titles carry a " - Publisher" suffix and a
# <source> element. One roundup and one non-funding item must be dropped.
GN_FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Fintech startup Tringbox raises $5M seed round - Inc42</title>
    <link>https://news.google.com/articles/a1</link>
    <pubDate>Wed, 18 Jun 2026 09:00:00 GMT</pubDate>
    <source url="https://inc42.com">Inc42</source>
  </item>
  <item>
    <title>Acme secures $40M Series B - The Economic Times</title>
    <link>https://news.google.com/articles/a2</link>
    <pubDate>Tue, 17 Jun 2026 11:30:00 GMT</pubDate>
    <source url="https://et.com">The Economic Times</source>
  </item>
  <item>
    <title>The 5 biggest funding rounds this week - YourStory</title>
    <link>https://news.google.com/articles/a3</link>
    <pubDate>Mon, 16 Jun 2026 08:00:00 GMT</pubDate>
    <source url="https://yourstory.com">YourStory</source>
  </item>
  <item>
    <title>Acme launches a new dashboard feature - TechCrunch</title>
    <link>https://news.google.com/articles/a4</link>
    <pubDate>Sun, 15 Jun 2026 08:00:00 GMT</pubDate>
    <source url="https://techcrunch.com">TechCrunch</source>
  </item>
</channel></rss>"""


def test_parse_feed_keeps_funding_drops_noise():
    rows = _parse_feed(GN_FEED, "India", "Google News", strip_publisher=True)
    by = {r["startup"]: r for r in rows}
    # only the two real single-company funding items survive
    assert set(by) == {"Tringbox", "Acme"}
    assert by["Tringbox"]["amount"] == "$5M" and by["Tringbox"]["round"] == "Seed"
    assert by["Acme"]["amount"] == "$40M" and by["Acme"]["round"] == "Series B"


def test_parse_feed_uses_publisher_and_region():
    rows = _parse_feed(GN_FEED, "India", "Google News", strip_publisher=True)
    by = {r["startup"]: r for r in rows}
    # the <source> element supplies the publisher, and it's stripped from title
    assert by["Tringbox"]["source"] == "Inc42"
    assert " - Inc42" not in by["Tringbox"]["title"]
    assert by["Acme"]["region"] == "India"
    assert by["Acme"]["posted_date"] == "2026-06-17"


def test_parse_feed_default_source_when_not_stripping():
    # TechCrunch-style: no publisher suffix, keep the default source name
    feed = GN_FEED.replace(b" - Inc42", b"").replace(b" - The Economic Times", b"")
    rows = _parse_feed(feed, "Global", "TechCrunch", strip_publisher=False)
    assert rows and all(r["source"] == "TechCrunch" for r in rows)
