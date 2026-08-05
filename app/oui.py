import asyncio
import logging
import re

import httpx
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models import OuiEntry

logger = logging.getLogger(__name__)

OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"
OUI_MIN_ENTRIES = 1_000
OUI_MAX_ENTRIES = 100_000
OUI_UPSERT_BATCH_SIZE = 750


def is_start(first_line: str, second_line: str) -> bool:
    if first_line is None or second_line is None:
        return False
    return len(first_line.strip()) == 0 and bool(
        re.search(r"([0-9A-F]{2}[-]){2}([0-9A-F]{2})", second_line)
    )


def parse_oui_data(text: str) -> dict[str, str]:
    lines = text.split("\n")
    result = {}
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and is_start(lines[i], lines[i + 1]):
            if i + 2 >= len(lines):
                break
            oui = lines[i + 2][:6].strip().upper()
            owner = re.sub(r"\((hex|base 16)\)", "", lines[i + 1])[10:].strip()

            i += 3
            while i < len(lines) and (i + 1 >= len(lines) or not is_start(lines[i], lines[i + 1])):
                i += 1

            owner = re.sub(r"[ \t]+", " ", owner)
            if len(oui) == 6:
                mac_prefix = f"{oui[0:2]}:{oui[2:4]}:{oui[4:6]}".lower()
                result[mac_prefix] = owner
        else:
            i += 1
    return result


def upsert_oui_entries(db: Session, entries: dict[str, str]) -> None:
    """Sync batch upsert of OUI rows (run via asyncio.to_thread from async callers)."""
    rows = [{"mac_prefix": mac_prefix, "vendor": vendor} for mac_prefix, vendor in entries.items()]
    for start in range(0, len(rows), OUI_UPSERT_BATCH_SIZE):
        batch = rows[start : start + OUI_UPSERT_BATCH_SIZE]
        stmt = insert(OuiEntry).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["mac_prefix"], set_=dict(vendor=stmt.excluded.vendor)
        )
        db.execute(stmt)
    db.commit()


async def update_oui_data(db: Session):
    logger.info(f"Downloading OUI data from {OUI_URL}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(OUI_URL)
            response.raise_for_status()
            text = response.text

            if not re.search(r"^(OUI|[#]|[A-Fa-f0-9])", text):
                raise ValueError("Downloaded file does not look like a oui-data.txt file")

            logger.info("Parsing OUI data...")
            entries = parse_oui_data(text)
            count = len(entries)
            if count < OUI_MIN_ENTRIES or count > OUI_MAX_ENTRIES:
                raise ValueError(
                    f"OUI entry count {count} outside expected range "
                    f"[{OUI_MIN_ENTRIES}, {OUI_MAX_ENTRIES}]"
                )
            logger.info("Parsed %s OUI entries. Updating database...", count)

            # Sessions are not thread-safe; open a fresh session on the same bind.
            bind = db.get_bind()

            def _write() -> None:
                with Session(bind) as thread_db:
                    upsert_oui_entries(thread_db, entries)

            await asyncio.to_thread(_write)
            logger.info("OUI data update complete.")

    except Exception as e:
        logger.error(f"Failed to update OUI data: {e}")
        db.rollback()
