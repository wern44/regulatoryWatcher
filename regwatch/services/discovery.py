"""LLM-driven regulation discovery and classification."""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from regwatch.db.models import (
    DoraPillar,
    LifecycleStage,
    Regulation,
    RegulationOverride,
    RegulationType,
)
from regwatch.llm.client import LLMClient

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = (
    "You are a regulatory classification expert for Luxembourg financial entities.\n"
    "Given a regulation or circular, determine:\n"
    "1. is_ict: Is this related to ICT, cybersecurity, digital operational resilience, "
    "IT outsourcing, or similar technology risk topics? (true/false)\n"
    "2. dora_pillar: If is_ict is true, which DORA pillar? "
    "(ICT_RISK_MGMT, INCIDENT_REPORTING, RESILIENCE_TESTING, THIRD_PARTY_RISK, "
    "INFO_SHARING, or null)\n"
    "3. applicable_entity_types: Which entity types does this apply to? "
    '(JSON array of: "AIFM", "CHAPTER15_MANCO", "CREDIT_INSTITUTION", "CASP", '
    '"INVESTMENT_FIRM", "INSURANCE", "PENSION_FUND", or "ALL")\n'
    "4. is_superseded: Has this been replaced by a newer version? (true/false)\n"
    "5. superseded_by: If superseded, the reference number of the replacement (or null)\n"
    "6. confidence: How confident are you in this classification? (0.0 to 1.0)\n\n"
    "Respond with ONLY a JSON object with these 6 fields."
)

_DISCOVER_SYSTEM = (
    "You are a regulatory expert for Luxembourg. Given an entity with authorization "
    "types {auth_types}, list CSSF circulars and EU regulations relevant to "
    "ICT/DORA that may be missing from the following catalog. Only include currently "
    "applicable regulations (not superseded ones). Respond with a JSON array of objects: "
    '{{reference_number, title, issuing_authority, type, is_ict, dora_pillar, url, applicability}}.'
)


class DiscoveryService:
    def __init__(self, session: Session, *, llm: LLMClient) -> None:
        self._session = session
        self._llm = llm

    def classify_catalog(self) -> int:
        """Classify all regulations in the catalog. Returns count of updated regulations."""
        overrides = self._load_overrides()
        regulations = self._session.query(Regulation).all()
        updated = 0

        for reg in regulations:
            ref = reg.reference_number

            # Check for user overrides — user decisions always win
            ict_override = overrides.get((ref, "SET_ICT")) or overrides.get((ref, "UNSET_ICT"))
            if ict_override:
                if ict_override.action == "SET_ICT":
                    reg.is_ict = True
                    reg.needs_review = False
                elif ict_override.action == "UNSET_ICT":
                    reg.is_ict = False
                    reg.needs_review = False
                updated += 1
                continue

            if (ref, "EXCLUDE") in overrides:
                continue

            try:
                result = self._classify_regulation(reg)
            except Exception:  # noqa: BLE001
                logger.warning("LLM classification failed for %s", ref)
                continue

            if result is None:
                continue

            reg.is_ict = result.get("is_ict", False)
            pillar = result.get("dora_pillar")
            if pillar and reg.is_ict:
                try:
                    reg.dora_pillar = DoraPillar(pillar)
                except ValueError:
                    reg.dora_pillar = None
            else:
                reg.dora_pillar = None

            confidence = result.get("confidence", 1.0)
            if isinstance(confidence, (int, float)):
                reg.needs_review = confidence < 0.7
            else:
                reg.needs_review = True

            updated += 1

        self._session.flush()
        return updated

    def discover_missing(self, auth_types: list[str]) -> int:
        """Ask the LLM to suggest missing regulations. Returns count added."""
        existing = self._session.query(Regulation).all()
        overrides = self._load_overrides()

        catalog_text = "\n".join(
            f"- {r.reference_number}: {r.title}" for r in existing
        )

        try:
            reply = self._llm.chat(
                system=_DISCOVER_SYSTEM.format(auth_types=", ".join(auth_types)),
                user=f"Current catalog:\n{catalog_text}",
            )
            data = json.loads(reply.strip())
            if not isinstance(data, list):
                return 0
        except Exception:  # noqa: BLE001
            logger.warning("LLM regulation discovery failed")
            return 0

        added = 0
        existing_refs = {r.reference_number for r in existing}
        for item in data:
            ref = item.get("reference_number", "")
            if not ref or ref in existing_refs:
                continue
            if (ref, "EXCLUDE") in overrides:
                continue

            try:
                reg_type = RegulationType(item.get("type", "CSSF_CIRCULAR"))
            except ValueError:
                reg_type = RegulationType.CSSF_CIRCULAR

            reg = Regulation(
                reference_number=ref,
                type=reg_type,
                title=item.get("title", ref),
                issuing_authority=item.get("issuing_authority", "Unknown"),
                lifecycle_stage=LifecycleStage.IN_FORCE,
                is_ict=item.get("is_ict", False),
                url=item.get("url", ""),
                source_of_truth="DISCOVERED",
                needs_review=True,
            )
            pillar = item.get("dora_pillar")
            if pillar and reg.is_ict:
                try:
                    reg.dora_pillar = DoraPillar(pillar)
                except ValueError:
                    pass
            self._session.add(reg)
            added += 1

        self._session.flush()
        return added

    def _classify_regulation(self, reg: Regulation) -> dict | None:
        reply = self._llm.chat(
            system=_CLASSIFY_SYSTEM,
            user=(
                f"Classify this regulation:\n"
                f"Reference: {reg.reference_number}\n"
                f"Title: {reg.title}\n"
                f"Issuing authority: {reg.issuing_authority}\n"
                f"Type: {reg.type.value}"
            ),
        )
        try:
            return json.loads(reply.strip())
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON for %s", reg.reference_number)
            return None

    def _load_overrides(self) -> dict[tuple[str, str], RegulationOverride]:
        rows = self._session.query(RegulationOverride).all()
        return {(r.reference_number, r.action): r for r in rows}
