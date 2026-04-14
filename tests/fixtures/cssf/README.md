# CSSF scraper fixtures

HTML files captured from `https://www.cssf.lu` that drive
`tests/unit/test_cssf_scraper.py` and `tests/integration/test_cssf_discovery_service.py`.

When to refresh: **only if the unit or integration scraper tests start failing** against
the current fixtures (the scraper's DOM selectors have drifted). The live probe at
`tests/live/test_cssf_live_probe.py` is the signal.

## How to refresh

```python
import httpx
from pathlib import Path

fixtures = Path("tests/fixtures/cssf")

client = httpx.Client(
    headers={"User-Agent": "RegulatoryWatcher/1.0 (fixture capture)"},
    follow_redirects=True, timeout=30.0,
)

# Listing page — filtered to AIFMs
r = client.get(
    "https://www.cssf.lu/en/regulatory-framework/",
    params={"fwp_entity_type": "aifms", "fwp_content_type": "circulars-cssf"},
)
r.raise_for_status()
(fixtures / "listing_aifms_page1.html").write_bytes(r.content)

# Detail page — a stable circular with an amendment relationship
r = client.get("https://www.cssf.lu/en/Document/circular-cssf-22-806/")
r.raise_for_status()
(fixtures / "detail_22_806.html").write_bytes(r.content)
```

After refresh, re-run the scraper tests — the parsers may need updating if the
DOM changed. Update `cssf_scraper.py` selectors alongside the fixtures in a
single commit so both move together.

## What's in these fixtures

- `listing_aifms_page1.html` — first page of `?fwp_entity_type=aifms&fwp_content_type=circulars-cssf`. 20 `li.library-element` rows.
- `detail_22_806.html` — detail page for Circular CSSF 22/806 (outsourcing). Chosen because it has a stable `(as amended by CSSF 25/883)` parenthetical and a rich "Related documents" section, exercising both amendment parsers.
