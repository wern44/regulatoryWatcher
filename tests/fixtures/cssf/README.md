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

### Publication-type matrix fixtures (filter matrix)

Six additional listing fixtures, one per non-CSSF-circular publication type, plus a sample
detail page for each type that has at least one AIFM-tagged document.

**How these work**: the CSSF regulatory-framework page accepts `?entity_type=<id>&content_type=<id>`
as server-side filter query parameters (numeric WordPress term IDs, confirmed via `data-href`
attributes in the rendered DOM). No JavaScript rendering is required.

| Listing fixture | Rows | Filter |
|---|---|---|
| `listing_aifms_cssf-regulation.html` | 4 | entity=502 + content=575 |
| `listing_aifms_law.html` | 13 | entity=502 + content=585 |
| `listing_aifms_grand-ducal-regulation.html` | 4 | entity=502 + content=553 |
| `listing_aifms_ministerial-regulation.html` | 20 (page 1) | entity=502 + content=591 |
| `listing_aifms_annex-to-a-cssf-circular.html` | 2 | entity=502 + content=5843 |
| `listing_aifms_professional-standard.html` | 0 | entity=502 + content=1377 |

The `professional-standard` listing has 0 rows — verified live: no AIFM-tagged professional
standards exist on the CSSF site. No detail fixture is captured for this type.

### How to refresh the publication-type matrix fixtures

Run the one-shot helper script (no Playwright required — plain httpx):

```bash
python scripts/capture_cssf_fixtures.py
```

The script reads `config.example.yaml` for the publication-type list and filter IDs,
fetches each `AIFM × publication-type` combination, and overwrites the fixture files in place.
