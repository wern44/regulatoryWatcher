from datetime import date

from regwatch.pipeline.match.lifecycle import classify_lifecycle


def test_celex_proposal_prefix() -> None:
    assert classify_lifecycle(
        title="Proposal for a Directive amending AIFMD",
        celex_id="52021PC0721",
        url="https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52021PC0721",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "PROPOSAL"


def test_celex_adopted_in_force() -> None:
    assert classify_lifecycle(
        title="Directive 2022/2554 DORA",
        celex_id="32022R2554",
        url="https://example.com",
        application_date=date(2025, 1, 17),
        today=date(2026, 4, 8),
    ) == "IN_FORCE"


def test_celex_adopted_not_in_force() -> None:
    assert classify_lifecycle(
        title="Directive 2024/927 AIFMD II",
        celex_id="32024L0927",
        url="https://example.com",
        application_date=date(2027, 4, 16),
        today=date(2026, 4, 8),
    ) == "ADOPTED_NOT_IN_FORCE"


def test_legilux_draft_bill_uri() -> None:
    assert classify_lifecycle(
        title="Projet de loi 8628",
        celex_id=None,
        url="http://data.legilux.public.lu/eli/etat/projet-de-loi/2025/10/08/a1/jo",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "DRAFT_BILL"


def test_title_heuristic_consultation() -> None:
    assert classify_lifecycle(
        title="Consultation paper on liquidity management tools",
        celex_id=None,
        url="https://www.esma.europa.eu/consultation",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "CONSULTATION"


def test_default_is_in_force() -> None:
    assert classify_lifecycle(
        title="Circular CSSF 25/901 on outsourcing",
        celex_id=None,
        url="https://www.cssf.lu/en/Document/circular-cssf-25-901/",
        application_date=None,
        today=date(2026, 4, 8),
    ) == "IN_FORCE"
