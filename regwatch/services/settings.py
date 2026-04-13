"""Settings service: DB-backed key-value store for runtime configuration."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from regwatch.db.models import Setting


class SettingsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._session.get(Setting, key)
        return row.value if row is not None else default

    def set(self, key: str, value: str) -> None:
        row = self._session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value, updated_at=datetime.now(UTC))
            self._session.add(row)
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)

    def get_all(self) -> dict[str, str]:
        rows = self._session.query(Setting).all()
        return {r.key: r.value for r in rows}
