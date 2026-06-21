from funding_scraper import parse_listing, REGIONS

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

def test_regions_cover_expected():
    assert {"usa", "uk", "india"} <= set(REGIONS)
