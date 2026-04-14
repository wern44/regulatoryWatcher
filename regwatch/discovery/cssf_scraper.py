"""CSSF website scraper -- pure HTTP, no DB.

Scrapes the public CSSF regulatory framework listing
(https://www.cssf.lu/en/regulatory-framework/) and per-document
detail pages. Returns plain dataclasses; the caller is responsible
for persistence.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.cssf.lu"
_LISTING_PATH = "/en/regulatory-framework/"
_USER_AGENT = "RegulatoryWatcher/1.0"

# Slugs confirmed to yield non-empty listings when filtering by facet.
# The listing page honors `fwp_entity_type` and `fwp_content_type` query params;
# `aifms` was verified to return AIFM-tagged circulars.
_CSSF_ENTITY_SLUGS: dict[str, str] = {
    "aifms": "aifms",
}

# Sanity cap for listing pagination. The real CSSF listing has well under 100
# pages per facet; this only guards against the site ever returning HTTP 200
# on a non-existent page with a non-empty body (which would otherwise loop
# forever since we key the stop condition on ``raw_count == 0``).
_MAX_PAGES_HARD_CEILING = 200


class CssfScraperError(RuntimeError):
    """Base class for scraper errors."""


class CircularNotFoundError(CssfScraperError):
    """Raised when a circular detail page returns HTTP 404."""


@dataclass
class CircularListingRow:
    """A single row on the CSSF regulatory-framework listing."""

    reference_number: str  # e.g. "CSSF 22/806", "CSSF-CPDI 26/50"
    raw_title: str
    description: str
    publication_date: date | None
    detail_url: str


@dataclass
class CircularDetail:
    """Parsed content of a CSSF circular detail page."""

    reference_number: str
    clean_title: str  # title with "(as amended by ...)" parenthetical stripped
    amended_by_refs: list[str] = field(default_factory=list)
    amends_refs: list[str] = field(default_factory=list)
    supersedes_refs: list[str] = field(default_factory=list)
    applicable_entities: list[str] = field(default_factory=list)
    pdf_url_en: str | None = None
    pdf_url_fr: str | None = None
    published_at: date | None = None
    updated_at: date | None = None
    description: str = ""


# Matches references such as:
#   "CSSF 22/806", "CSSF-CPDI 26/50", "Circular CSSF 25/883", "IML 98/143".
# We capture the authority prefix plus number so compound prefixes
# (CSSF-CPDI) and legacy prefixes (IML) round-trip correctly.
_REF_RE = re.compile(
    r"\b(?:CSSF(?:[\s-][A-Z]+)?|IML|BCL)\s*\d{2,4}[/-]\d{1,4}\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Listing page
# ---------------------------------------------------------------------------


def list_circulars(
    entity_slug: str,
    *,
    client: httpx.Client | None = None,
    content_type: str = "circulars-cssf",
    max_pages: int | None = None,
    request_delay_ms: int = 500,
) -> Iterator[CircularListingRow]:
    """Paginate the filtered listing. Stops when a page has no ``li.library-element``.

    The CSSF listing interleaves CSSF circulars with EU regulations (which
    ``_REF_RE`` deliberately rejects). A page with 20 non-CSSF items is still
    a valid page and we must keep walking: pagination only terminates when
    the raw ``<li.library-element>`` count on the page is 0 (i.e. the page
    truly does not exist / has no items).

    Args:
        entity_slug: FacetWP entity_type slug, e.g. ``"aifms"``.
        client: optional shared ``httpx.Client`` (for tests / connection reuse).
        content_type: FacetWP content_type slug; defaults to CSSF circulars.
        max_pages: hard cap on pages fetched; ``None`` means no cap.
        request_delay_ms: sleep between page fetches to be polite.
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=30.0,
        )
    try:
        page = 1
        while True:
            if max_pages is not None and page > max_pages:
                return
            if page > _MAX_PAGES_HARD_CEILING:
                logger.warning(
                    "Hit hard pagination ceiling (%d) at slug=%s",
                    _MAX_PAGES_HARD_CEILING,
                    entity_slug,
                )
                return
            url = _build_listing_url(page)
            resp = client.get(
                url,
                params={
                    "fwp_entity_type": entity_slug,
                    "fwp_content_type": content_type,
                },
            )
            resp.raise_for_status()
            matched, raw_count = _parse_listing_page(resp.text)
            if raw_count == 0:
                # No ``li.library-element`` items at all -> past the last page.
                return
            yield from matched
            page += 1
            if request_delay_ms > 0:
                time.sleep(request_delay_ms / 1000)
    finally:
        if owns_client:
            client.close()


def _build_listing_url(page: int) -> str:
    if page == 1:
        return urljoin(_BASE_URL, _LISTING_PATH)
    return urljoin(_BASE_URL, f"{_LISTING_PATH}page/{page}/")


def _parse_listing_page(html: str) -> tuple[list[CircularListingRow], int]:
    """Return ``(matched_rows, raw_row_count)`` for a listing page.

    ``raw_row_count`` is the number of ``<li.library-element>`` items present
    in the page regardless of whether they match ``_REF_RE``. A ``raw_count``
    of 0 means "no page / past the end" and is the only signal that should
    terminate pagination. ``matched_rows`` is the subset that produced a
    parseable ``CircularListingRow`` (i.e. had a CSSF/IML reference number).

    The listing DOM looks like::

        <li class="library-element">
          <div class="library-element__heading">
            <p class="library-element__type">CSSF circular</p>
            <p class="library-element__dates">
              <span class="date--published">Published on 01.04.2026</span>
            </p>
          </div>
          <div class="library-element__main">
            <h3 class="library-element__title">
              <a href="/en/Document/circular-cssf-26-909/">Circular CSSF 26/909</a>
            </h3>
            <div class="library-element__subtitle"><p>Application of ...</p></div>
          </div>
        </li>
    """
    soup = BeautifulSoup(html, "html.parser")
    raw_items = soup.select("li.library-element")
    matched: list[CircularListingRow] = []
    for item in raw_items:
        row = _row_from_library_element(item)
        if row is not None:
            matched.append(row)
    return matched, len(raw_items)


def _parse_listing_html(html: str) -> Iterator[CircularListingRow]:
    """Back-compat wrapper: yield only the matched rows for a listing page."""
    matched, _ = _parse_listing_page(html)
    yield from matched


def _row_from_library_element(item: Tag) -> CircularListingRow | None:
    title_link = item.select_one(".library-element__title a")
    if title_link is None:
        return None
    raw_title = title_link.get_text(" ", strip=True)
    href_value = title_link.get("href") or ""
    href = href_value if isinstance(href_value, str) else ""
    if not href:
        return None
    detail_url = urljoin(_BASE_URL, href)

    ref_match = _REF_RE.search(raw_title)
    if ref_match is None:
        # Skip rows that aren't clearly a circular/regulation with a ref number
        # (e.g. CSSF Regulation No 26-01 is kept via the wider regex; but
        # anything else without a parsable ID is dropped).
        return None
    reference_number = _normalize_ref(ref_match.group(0))

    subtitle = item.select_one(".library-element__subtitle")
    description = subtitle.get_text(" ", strip=True) if subtitle else ""

    pub_el = item.select_one(".date--published")
    publication_date = _parse_published_short(pub_el.get_text(" ", strip=True)) if pub_el else None

    return CircularListingRow(
        reference_number=reference_number,
        raw_title=raw_title,
        description=description,
        publication_date=publication_date,
        detail_url=detail_url,
    )


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------


def fetch_circular_detail(
    url: str,
    *,
    client: httpx.Client | None = None,
    request_delay_ms: int = 500,
) -> CircularDetail:
    """Fetch and parse a single circular detail page.

    Raises:
        CircularNotFoundError: if the page returns HTTP 404.
        httpx.HTTPError: for other transport/HTTP errors.
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=30.0,
        )
    try:
        resp = client.get(url)
        if resp.status_code == 404:
            raise CircularNotFoundError(url)
        resp.raise_for_status()
        if request_delay_ms > 0:
            time.sleep(request_delay_ms / 1000)
        return _parse_detail_html(resp.text, source_url=url)
    finally:
        if owns_client:
            client.close()


def _parse_detail_html(html: str, *, source_url: str) -> CircularDetail:
    """Parse a CSSF detail page into a ``CircularDetail``.

    Key anchors in the DOM:
      * ``h1.single-news__title`` -- raw title, may contain
        ``(as amended by Circular CSSF NN/NNN[, and/or CSSF MM/MMM])``.
      * ``.content-header-info`` -- text like ``Published on DD Month YYYY``
        and ``Updated on DD Month YYYY``.
      * ``.entities-list li`` -- "Relevant for" entity list.
      * ``li.related-document.no-heading`` -- the main circular's own
        file downloads (EN / FR PDFs + attachments).
      * ``li.related-document:not(.no-heading)`` -- cross-referenced
        documents (amending / amended / superseding).
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- Title and amendments parenthetical -------------------------------
    h1 = soup.select_one("h1.single-news__title") or soup.find("h1")
    raw_title = h1.get_text(" ", strip=True) if h1 else ""
    clean_title, amended_by_refs = _split_amendment_parenthetical(raw_title)

    # Reference number from the cleaned title (falls back to full raw title).
    ref_match = _REF_RE.search(clean_title) or _REF_RE.search(raw_title)
    reference_number = _normalize_ref(ref_match.group(0)) if ref_match else ""

    # --- Dates ------------------------------------------------------------
    published_at, updated_at = _parse_header_dates(soup)

    # --- Applicable entities ---------------------------------------------
    applicable_entities: list[str] = []
    seen_entities: set[str] = set()
    for li in soup.select(".entities-list li"):
        name = li.get_text(" ", strip=True)
        if name and name not in seen_entities:
            applicable_entities.append(name)
            seen_entities.add(name)

    # --- PDFs (English / French) -----------------------------------------
    pdf_url_en, pdf_url_fr = _extract_main_pdfs(soup)

    # --- Related documents: amends / supersedes refs ---------------------
    amends_refs, supersedes_refs = _extract_related_refs(soup, reference_number)

    # --- Description: prefer the dedicated subtitle block, else fall back
    # to the first substantive paragraph of the body content.
    description = ""
    subtitle_el = soup.select_one(".single-news__subtitle")
    if subtitle_el is not None:
        p = subtitle_el.find("p")
        description = (
            p.get_text(" ", strip=True) if p is not None
            else subtitle_el.get_text(" ", strip=True)
        )
    if not description:
        description = _extract_description(soup)

    return CircularDetail(
        reference_number=reference_number,
        clean_title=clean_title,
        amended_by_refs=amended_by_refs,
        amends_refs=amends_refs,
        supersedes_refs=supersedes_refs,
        applicable_entities=applicable_entities,
        pdf_url_en=pdf_url_en,
        pdf_url_fr=pdf_url_fr,
        published_at=published_at,
        updated_at=updated_at,
        description=description,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_ref(raw: str) -> str:
    """Normalize a reference to a canonical form with a single space.

    "circular cssf 22/806" -> "CSSF 22/806".
    Leaves compound prefixes (CSSF-CPDI) and legacy prefixes (IML) intact.
    """
    ref = raw.strip()
    # Strip leading "Circular" if the regex happened to catch it from a longer
    # match elsewhere (shouldn't normally, but be defensive).
    ref = re.sub(r"^[Cc]ircular\s+", "", ref)
    # Collapse internal whitespace.
    ref = re.sub(r"\s+", " ", ref).upper()
    # Normalize numeric separator to "/".
    ref = ref.replace("-AND-", " AND ")
    return ref


def _parse_published_short(text: str) -> date | None:
    """Parse 'Published on DD.MM.YYYY' (listing format)."""
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _parse_long_date(text: str) -> date | None:
    """Parse 'DD Month YYYY' (detail-page format)."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if not m:
        return None
    try:
        return datetime.strptime(
            f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y"
        ).date()
    except ValueError:
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y"
            ).date()
        except ValueError:
            return None


def _parse_header_dates(soup: BeautifulSoup) -> tuple[date | None, date | None]:
    published: date | None = None
    updated: date | None = None
    header = soup.select_one(".content-header-info")
    text = header.get_text(" ", strip=True) if header else soup.get_text(" ", strip=True)
    pub_match = re.search(r"Published on\s+([^\n]+?)(?=\s+Updated on|\s+Email|\s+Share|$)", text)
    if pub_match:
        published = _parse_long_date(pub_match.group(1))
    upd_match = re.search(r"Updated on\s+([^\n]+?)(?=\s+Email|\s+Share|$)", text)
    if upd_match:
        updated = _parse_long_date(upd_match.group(1))
    return published, updated


def _split_amendment_parenthetical(raw_title: str) -> tuple[str, list[str]]:
    """Strip the '(as amended by ...)' parenthetical from the title.

    Returns ``(clean_title, amended_by_refs)``. If there's no such
    parenthetical, ``amended_by_refs`` is empty and ``clean_title == raw_title``.
    """
    m = re.search(r"\(\s*as amended by\s+([^)]+)\)", raw_title, flags=re.IGNORECASE)
    if not m:
        return raw_title.strip(), []
    inner = m.group(1)
    refs = [_normalize_ref(r) for r in _REF_RE.findall(inner)]
    # Deduplicate, preserve order
    dedup: list[str] = []
    seen: set[str] = set()
    for r in refs:
        if r not in seen:
            dedup.append(r)
            seen.add(r)
    clean = (raw_title[: m.start()] + raw_title[m.end() :]).strip()
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean, dedup


def _extract_main_pdfs(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Find the English/French PDFs for the main circular itself.

    The main downloads sit in ``li.related-document.no-heading`` (the
    ``no-heading`` class distinguishes them from cross-referenced docs).
    Within that block, filenames ending ``eng.pdf`` are the English
    translation; the bare ``.pdf`` is the original French.
    """
    main_block = soup.select_one("li.related-document.no-heading")
    if main_block is None:
        return None, None
    pdf_en: str | None = None
    pdf_fr: str | None = None
    for a in main_block.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href_raw = a.get("href") or ""
        href = href_raw if isinstance(href_raw, str) else ""
        if not href.lower().endswith(".pdf"):
            continue
        full = urljoin(_BASE_URL, href)
        if href.lower().endswith("eng.pdf"):
            if pdf_en is None:
                pdf_en = full
        else:
            if pdf_fr is None:
                pdf_fr = full
    return pdf_en, pdf_fr


def _extract_related_refs(
    soup: BeautifulSoup, self_ref: str
) -> tuple[list[str], list[str]]:
    """Collect references to other circulars from the Related documents block.

    Heuristic classification (the CSSF DOM does not tag these explicitly):

      * If the related document's excerpt contains "amending ..." / "amends ...",
        then the *current* document is amended by it -- skip (we already
        captured those from the title parenthetical).
      * If the related document's title is itself "(as amended by ... SELF ...)",
        then ``self`` amends it -> add to ``amends_refs``.
      * Everything else with a CSSF/IML reference number that isn't self gets
        appended to ``amends_refs`` as a weak link (best-effort; the downstream
        service should re-assess using the detail pages of each related doc).

    ``supersedes_refs`` is left empty: the site does not surface an explicit
    "supersedes" relationship in the markup.
    """
    amends: list[str] = []
    supersedes: list[str] = []
    seen: set[str] = set()
    for item in soup.select(
        ".related-documents-container li.related-document:not(.no-heading)"
    ):
        h4 = item.select_one("h4.related-document-title")
        if h4 is None:
            continue
        title_text = h4.get_text(" ", strip=True)
        excerpt_el = item.select_one(".related-document-excerpt")
        excerpt = excerpt_el.get_text(" ", strip=True) if excerpt_el else ""
        refs_in_title = [_normalize_ref(r) for r in _REF_RE.findall(title_text)]
        if not refs_in_title:
            continue
        primary = refs_in_title[0]
        if primary == self_ref:
            continue

        excerpt_lc = excerpt.lower()
        title_lc = title_text.lower()
        # "amending Circular CSSF 22/806" -> this related doc amends us. Skip.
        if "amending" in excerpt_lc or "amend circular" in excerpt_lc:
            continue
        # "(as amended by ... CSSF 22/806 ...)" in the title means we
        # amend them.
        if "amended by" in title_lc and self_ref.lower() in title_lc:
            if primary not in seen:
                amends.append(primary)
                seen.add(primary)
            continue
        # Otherwise add as a best-effort "related" link under amends,
        # unless we've already captured it.
        if primary not in seen:
            amends.append(primary)
            seen.add(primary)

    return amends, supersedes


def _extract_description(soup: BeautifulSoup) -> str:
    """Pull a short description from the first paragraph of the main content.

    Tries a few selectors; falls back to an empty string.
    """
    candidates = [
        ".single-news__content p",
        ".single-document__content p",
        ".entry-content p",
        "article p",
    ]
    for sel in candidates:
        for p in soup.select(sel):
            text = p.get_text(" ", strip=True)
            if text and len(text) > 40:
                return text
    return ""
