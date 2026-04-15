"""Live probe: verify CSSF filter IDs still match their expected labels.

Run explicitly:
    pytest -m live tests/live/test_cssf_filter_probe.py -v

The CSSF listing page renders filter checkboxes with numeric WordPress
term IDs (e.g. value="567" labelled "CSSF circular"). These IDs are
baked into config.example.yaml. This probe catches drift: if CSSF
renumbers a term after a site rebuild, the matrix crawl silently
stops matching that cell. The probe fails loudly instead.

The CSSF regulatory-framework page is fully server-side rendered, so
no headless browser is needed — plain httpx + BeautifulSoup suffices.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from bs4 import BeautifulSoup

LISTING_URL = "https://www.cssf.lu/en/regulatory-framework/"
CONFIG_EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.yaml"


def _load_expected_mappings() -> tuple[dict[str, int], dict[str, int]]:
    """Read config.example.yaml and return (entity_label_to_id, content_label_to_id).

    entity_filter_ids in config is keyed by AuthorizationType enum value
    (e.g. "AIFM"); we need the human-readable label CSSF renders
    (e.g. "AIFMs"). A small hardcoded enum-value -> CSSF-label map
    bridges the gap.
    """
    data = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
    cssf = data["cssf_discovery"]

    auth_to_cssf_label = {
        "AIFM": "AIFMs",
        "CHAPTER15_MANCO": "Management companies - Chapter 15",
    }
    entity_expected: dict[str, int] = {}
    for auth_value, filter_id in cssf["entity_filter_ids"].items():
        label = auth_to_cssf_label.get(auth_value, auth_value)
        entity_expected[label] = int(filter_id)

    content_expected: dict[str, int] = {}
    for pub in cssf["publication_types"]:
        content_expected[pub["label"]] = int(pub["filter_id"])

    return entity_expected, content_expected


def _scrape_checkbox_mapping(soup: BeautifulSoup, group_name: str) -> dict[str, int]:
    """Return {label: numeric_id} for all checkboxes in a filter group.

    Markup shape:
        <input type="checkbox" name="<group_name>" value="NNN">
        <span id="<group_name>-NNN">Label</span>
    """
    mapping: dict[str, int] = {}
    for inp in soup.select(f'input[type="checkbox"][name="{group_name}"]'):
        value = inp.get("value") or ""
        try:
            numeric_id = int(value)
        except ValueError:
            continue
        label_el = soup.select_one(f"span#{group_name}-{numeric_id}")
        if label_el is None:
            continue
        label = label_el.get_text(strip=True)
        if label:
            mapping[label] = numeric_id
    return mapping


@pytest.mark.live
def test_cssf_filter_ids_still_match_labels() -> None:
    entity_expected, content_expected = _load_expected_mappings()

    with httpx.Client(
        headers={"User-Agent": "RegulatoryWatcher/1.0"},
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        resp = client.get(LISTING_URL)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    entity_actual = _scrape_checkbox_mapping(soup, "entity_type")
    content_actual = _scrape_checkbox_mapping(soup, "content_type")

    # Check every expected label is present and maps to the expected id.
    # Do NOT require equality on the actual map — CSSF exposes many
    # filters we don't track.
    entity_missing = [
        (lbl, exp_id)
        for lbl, exp_id in entity_expected.items()
        if entity_actual.get(lbl) != exp_id
    ]
    assert not entity_missing, (
        f"Entity-type filter IDs drifted or labels changed.\n"
        f"Expected (subset): {entity_expected}\n"
        f"Actual full map:   {entity_actual}\n"
        f"Mismatches:        {entity_missing}"
    )

    content_missing = [
        (lbl, exp_id)
        for lbl, exp_id in content_expected.items()
        if content_actual.get(lbl) != exp_id
    ]
    assert not content_missing, (
        f"Publication-type filter IDs drifted or labels changed.\n"
        f"Expected (subset): {content_expected}\n"
        f"Actual full map:   {content_actual}\n"
        f"Mismatches:        {content_missing}"
    )
