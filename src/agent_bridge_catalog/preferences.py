from __future__ import annotations

import json
from datetime import UTC, datetime

from .db import CatalogSettingRow, Database

AUTO_ADD_NEW_CHATS = "auto_add_new_chats"


class PreferenceStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def auto_add_new_chats(self) -> bool:
        with self.database.session() as session:
            row = session.get(CatalogSettingRow, AUTO_ADD_NEW_CHATS)
            return bool(json.loads(row.value_json)) if row is not None else False

    def set_auto_add_new_chats(self, enabled: bool) -> bool:
        now = datetime.now(UTC)
        with self.database.session() as session:
            row = session.get(CatalogSettingRow, AUTO_ADD_NEW_CHATS)
            if row is None:
                row = CatalogSettingRow(
                    key=AUTO_ADD_NEW_CHATS,
                    value_json=json.dumps(enabled),
                    updated_at=now,
                )
                session.add(row)
            else:
                row.value_json = json.dumps(enabled)
                row.updated_at = now
            session.commit()
        return enabled

    def as_dict(self) -> dict[str, bool]:
        return {AUTO_ADD_NEW_CHATS: self.auto_add_new_chats()}
