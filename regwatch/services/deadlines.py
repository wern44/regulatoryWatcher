"""Deadline tracking across transposition and application dates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from regwatch.db.models import Regulation

DeadlineKind = Literal["TRANSPOSITION", "APPLICATION"]


@dataclass
class DeadlineDTO:
    regulation_id: int
    reference_number: str
    title: str
    kind: DeadlineKind
    due_date: date
    days_until: int
    severity_band: str
    url: str
    done: bool


class DeadlineService:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def severity_band(days_until: int) -> str:
        """Colour band per spec:
        0-30  -> RED
        30-180 -> AMBER
        180-730 -> BLUE
        >730 -> GREY
        <0 -> OVERDUE
        """
        if days_until < 0:
            return "OVERDUE"
        if days_until <= 30:
            return "RED"
        if days_until <= 180:
            return "AMBER"
        if days_until <= 730:
            return "BLUE"
        return "GREY"

    def upcoming(self, window_days: int, show_completed: bool = False) -> list[DeadlineDTO]:
        rows = (
            self._session.query(Regulation)
            .filter(
                or_(
                    Regulation.transposition_deadline.is_not(None),
                    Regulation.application_date.is_not(None),
                )
            )
            .all()
        )
        today = date.today()
        items: list[DeadlineDTO] = []
        for reg in rows:
            for kind, due, done_flag in (
                ("TRANSPOSITION", reg.transposition_deadline, reg.transposition_done),
                ("APPLICATION", reg.application_date, reg.application_done),
            ):
                if due is None:
                    continue
                if done_flag and not show_completed:
                    continue
                days_until = (due - today).days
                if days_until > window_days:
                    continue
                items.append(
                    DeadlineDTO(
                        regulation_id=reg.regulation_id,
                        reference_number=reg.reference_number,
                        title=reg.title,
                        kind=kind,  # type: ignore[arg-type]
                        due_date=due,
                        days_until=days_until,
                        severity_band=self.severity_band(days_until),
                        url=reg.url,
                        done=done_flag,
                    )
                )
        items.sort(key=lambda d: d.days_until)
        return items

    def set_done(self, regulation_id: int, kind: DeadlineKind, done: bool) -> None:
        reg = self._session.get(Regulation, regulation_id)
        if reg is None:
            raise ValueError(f"Regulation {regulation_id} not found")
        if kind == "TRANSPOSITION":
            reg.transposition_done = done
        else:
            reg.application_done = done
