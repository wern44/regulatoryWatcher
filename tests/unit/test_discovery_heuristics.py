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
    ],
)
def test_is_ict_by_heuristic(title, description, expected):
    assert is_ict_by_heuristic(title=title, description=description) is expected


@pytest.mark.parametrize(
    "title,description,expected",
    [
        # Regressions from live run — these must NOT match anymore
        ("FATF statements concerning high-risk jurisdictions", "", False),
        ("Adoption of the EBA Guidelines on internal controls", "", False),
        ("Restrictive measures against Iran", "", False),
        ("Conflict of interest disclosures", "", False),
        ("Districts within the CSSF remit", "", False),
        # True positives must still pass
        ("ICT risk management", "", True),
        ("IT governance framework", "", True),
        ("Regulation on cybersecurity", "", True),
        ("DORA transposition", "", True),
        ("Cloud services guidelines", "", True),
        ("NIS2 transposition", "", True),
        # Phrases still substring-match
        ("Outsourcing arrangements", "", True),
        ("Third-party risk management", "", True),
        ("Operational resilience framework", "", True),
        ("", "Rules on business continuity.", True),
    ],
)
def test_is_ict_by_heuristic_word_boundary(title, description, expected):
    assert is_ict_by_heuristic(title=title, description=description) is expected


def test_keyword_buckets_are_lowercase_frozensets():
    from regwatch.discovery.heuristics import _PHRASE_KEYWORDS, _WORD_BOUNDARY_KEYWORDS
    for bucket in (_WORD_BOUNDARY_KEYWORDS, _PHRASE_KEYWORDS):
        assert isinstance(bucket, frozenset)
        for kw in bucket:
            assert kw == kw.lower()
            assert kw.strip() == kw
