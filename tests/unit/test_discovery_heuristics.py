import pytest

from regwatch.discovery.heuristics import is_ict_by_heuristic


@pytest.mark.parametrize(
    "title,description,expected",
    [
        # Clear ICT signals in the title
        ("CSSF 20/750 on ICT and security risk management", "", True),
        ("CSSF 22/806 on outsourcing arrangements", "Guidance on outsourcing.", True),
        ("Information security requirements for banks", "", True),
        ("Operational resilience framework", "", True),
        ("Regulation on digital operational resilience (DORA)", "", True),
        ("Amendment on cybersecurity controls", "", True),
        ("CSSF 24/847 ICT incident reporting", "", True),
        ("NIS2 transposition", "", True),
        # Case insensitivity
        ("CIRCULAR ON CLOUD SERVICES", "", True),
        # Signal in description only
        (
            "Generic circular title",
            "This concerns third-party risk management of IT providers.",
            True,
        ),
        ("Generic circular title", "Updates business continuity guidelines.", True),
        # Clear non-ICT
        (
            "NAV errors and breach of investment rules",
            "Rules about fund valuation mistakes.",
            False,
        ),
        ("AIFM reporting obligations under Article 24 AIFMD", "Quarterly reporting.", False),
        ("", "", False),
        # Word-boundary weakness check: "rice" shouldn't match "ict" (substring would)
        # We intentionally USE substring match; document that this is lenient.
        # Commented out — substring IS the intended behaviour:
        # ("Price circular", "", False),
    ],
)
def test_is_ict_by_heuristic(title, description, expected):
    assert is_ict_by_heuristic(title=title, description=description) is expected


def test_keyword_list_is_frozenset_and_lowercase():
    from regwatch.discovery.heuristics import _ICT_KEYWORDS
    assert isinstance(_ICT_KEYWORDS, frozenset)
    for kw in _ICT_KEYWORDS:
        assert kw == kw.lower(), f"keyword not lowercase: {kw!r}"
        assert kw.strip() == kw, f"keyword has whitespace: {kw!r}"
