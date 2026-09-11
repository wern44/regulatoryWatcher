import pytest

from regwatch.pipeline.match.classify import is_ict_document, severity_for


def test_ict_keywords_trigger_flag() -> None:
    assert is_ict_document("DORA incident reporting requirements") is True
    assert is_ict_document("Third-party ICT risk management") is True
    assert is_ict_document("Cyber resilience testing rules") is True


def test_non_ict_documents() -> None:
    assert is_ict_document("Remuneration policies for UCITS") is False
    assert is_ict_document("NAV errors and breaches") is False


def test_severity_critical_for_amendment_with_ict() -> None:
    assert severity_for(
        title="Amending regulation on ICT risk management",
        is_ict=True,
        references_in_force=True,
    ) == "CRITICAL"


def test_severity_material_for_amendment_without_ict() -> None:
    assert severity_for(
        title="Amending regulation on remuneration",
        is_ict=False,
        references_in_force=True,
    ) == "MATERIAL"


def test_severity_informational_default() -> None:
    assert severity_for(
        title="FAQ update",
        is_ict=False,
        references_in_force=False,
    ) == "INFORMATIONAL"


@pytest.mark.parametrize(
    "text",
    [
        "Guidelines on conflicts of interest",
        "Restrictions on marketing to retail investors",
        "Cooperation between jurisdictions",
        "Strictly for professional investors; predictability of flows",
    ],
)
def test_ict_keyword_does_not_match_inside_words(text: str) -> None:
    """313 of 651 events were flagged ICT, largely because "ict" matched
    inside "conflicts", "restrictions" and "jurisdictions"."""
    assert is_ict_document(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "ICT risk management framework",
        "ICT-related incident reporting",
        "Implementation of DORA",
        "Cybersecurity expectations",
        "Cyber resilience testing (TLPT)",
        "Outsourcing arrangements",
    ],
)
def test_ict_keywords_still_match_whole_words(text: str) -> None:
    assert is_ict_document(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Loi portant mise en œuvre du règlement sur la résilience opérationnelle numérique",
        "Gestion du risque lié aux TIC",
        "Exigences en matière de cybersécurité",
        "Accords de sous-traitance",
    ],
)
def test_french_ict_keywords_match(text: str) -> None:
    """Legilux texts are French; without French keywords every one of them
    went to the LLM fallback, which over-flags."""
    assert is_ict_document(text) is True


def test_llm_fallback_asks_whether_ict_is_the_main_subject() -> None:
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.chat.return_value = "false"

    assert is_ict_document("Règlement sur les coussins de fonds propres", llm=llm) is False
    prompt = llm.chat.call_args.kwargs["user"]
    assert "MAIN SUBJECT" in prompt
    assert "only mentioned in passing" in prompt
