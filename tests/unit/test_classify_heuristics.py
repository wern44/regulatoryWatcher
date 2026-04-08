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
