"""Live probe: verify CSSF filter IDs still match their expected labels.

Run explicitly:
    pytest -m live tests/live/test_cssf_filter_probe.py -v

Prerequisite (one-time): `playwright install chromium`. If the Chromium
binary is missing, this test fails with a clear install hint.

Why live: the CSSF site's checkbox filters use numeric WordPress term
IDs (e.g. 567 = "CSSF circular", 502 = "AIFMs"). These IDs are baked
into config.example.yaml. This probe catches drift: if CSSF renumbers
a term after a site rebuild, the matrix crawl silently stops matching
that cell. The probe fails loudly instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

LISTING_URL = "https://www.cssf.lu/en/regulatory-framework/"
CONFIG_EXAMPLE = Path(__file__).resolve().parents[2] / "config.example.yaml"


def _load_expected_mappings() -> tuple[dict[str, int], dict[str, int]]:
    """Read config.example.yaml and return (entity_label_to_id, content_label_to_id).

    entity_filter_ids in config is keyed by AuthorizationType enum value
    (e.g. "AIFM"); we need the human-readable label CSSF renders
    (e.g. "AIFMs"). The probe test translates using a small hardcoded
    enum-value -> CSSF-label map.
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


def _scrape_checkbox_mapping(page, group_name: str) -> dict[str, int]:
    """Return {label: numeric_id} for all checkboxes in a filter group.

    Markup shape: <input type="checkbox" name="<group_name>" value="NNN">
    with a sibling <span id="<group_name>-NNN">Label</span>.
    """
    mapping: dict[str, int] = {}
    inputs = page.query_selector_all(f'input[type="checkbox"][name="{group_name}"]')
    for inp in inputs:
        value = inp.get_attribute("value")
        if not value:
            continue
        try:
            numeric_id = int(value)
        except ValueError:
            continue
        label_el = page.query_selector(f'span#{group_name}-{numeric_id}')
        if label_el is None:
            continue
        label = (label_el.inner_text() or "").strip()
        if label:
            mapping[label] = numeric_id
    return mapping


@pytest.mark.live
def test_cssf_filter_ids_still_match_labels() -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    entity_expected, content_expected = _load_expected_mappings()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent="RegulatoryWatcher/1.0")
            page = context.new_page()
            page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=30000)

            entity_actual = _scrape_checkbox_mapping(page, "entity_type")
            content_actual = _scrape_checkbox_mapping(page, "content_type")

            browser.close()
    except PlaywrightError as exc:
        # Most common cause: Chromium binary not installed.
        if "Executable doesn't exist" in str(exc):
            pytest.fail(
                "Playwright Chromium not installed. Run: "
                "`playwright install chromium`\n"
                f"Underlying error: {exc}"
            )
        raise

    # Check every expected label is present and maps to the expected id.
    # Do NOT require equality — CSSF exposes many filters we don't track.
    missing = [
        (lbl, exp_id)
        for lbl, exp_id in entity_expected.items()
        if entity_actual.get(lbl) != exp_id
    ]
    assert not missing, (
        f"Entity-type filter IDs drifted or labels changed.\n"
        f"Expected (subset): {entity_expected}\n"
        f"Actual full map:   {entity_actual}\n"
        f"Mismatches:        {missing}"
    )

    missing = [
        (lbl, exp_id)
        for lbl, exp_id in content_expected.items()
        if content_actual.get(lbl) != exp_id
    ]
    assert not missing, (
        f"Publication-type filter IDs drifted or labels changed.\n"
        f"Expected (subset): {content_expected}\n"
        f"Actual full map:   {content_actual}\n"
        f"Mismatches:        {missing}"
    )
