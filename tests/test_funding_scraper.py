from funding_scraper import parse_listing, parse_funding_title, REGIONS

SAMPLE = '''
<a href="https://www.finsmes.com/2026/06/crest-x" rel="bookmark">Crest Raises $3.1M in Pre-Seed Funding</a>
<a href="https://www.finsmes.com/2026/06/sarvam-y" rel="bookmark">Sarvam Raises $234M in First Close of Series B Funding at $1.5 Billion</a>
<a href="https://www.finsmes.com/2026/05/report-z" rel="bookmark">FinSMEs Intelligence Releases May 2026 Global Report</a>
<a href="https://www.finsmes.com/2026/06/madverse-q" rel="bookmark">Madverse Music Group Closes Funding</a>
'''

def test_parses_funding_rows_and_skips_non_funding():
    rows = parse_listing(SAMPLE, "india")
    by = {r["startup"]: r for r in rows}
    # report (no funding verb) is skipped
    assert not any("Intelligence" in r["startup"] for r in rows)
    assert "Crest" in by and by["Crest"]["amount"] == "$3.1M" and by["Crest"]["round"] == "Pre-Seed"
    assert by["Crest"]["region"] == "India"
    assert by["Sarvam"]["amount"] == "$234M" and by["Sarvam"]["round"] == "Series B"
    # funding event with no amount/round still included (verb present)
    assert "Madverse Music Group" in by and by["Madverse Music Group"]["amount"] is None
    assert by["Crest"]["source_url"].endswith("crest-x") and by["Crest"]["posted_date"] == "2026-06-01"

def test_finsmes_rows_tagged_with_source():
    rows = parse_listing(SAMPLE, "india")
    assert rows and all(r["source"] == "FinSMEs" for r in rows)

def test_regions_cover_expected():
    assert {"usa", "uk", "india"} <= set(REGIONS)

def test_parse_funding_title_clean_headline():
    p = parse_funding_title("Acme Raises $10M in Series A")
    assert p["startup"] == "Acme" and p["amount"] == "$10M" and p["round"] == "Series A"

def test_parse_funding_title_name_after_descriptor():
    # messier news-style headline — name sits mid-sentence after a descriptor
    p = parse_funding_title("Fintech startup Tringbox raises $5M seed round")
    assert p and p["startup"] == "Tringbox" and p["round"] == "Seed"

def test_parse_funding_title_skips_roundups():
    assert parse_funding_title("The 10 biggest funding rounds this week") is None
    assert parse_funding_title("Weekly funding news roundup") is None

def test_parse_funding_title_requires_verb():
    assert parse_funding_title("Acme launches a new product") is None
