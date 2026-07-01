import re
import httpx
import logging
from typing import Dict
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert

from app.models import OuiEntry

logger = logging.getLogger(__name__)

OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"

def is_start(first_line: str, second_line: str) -> bool:
    if first_line is None or second_line is None:
        return False
    return len(first_line.strip()) == 0 and bool(re.search(r'([0-9A-F]{2}[-]){2}([0-9A-F]{2})', second_line))

def parse_oui_data(text: str) -> Dict[str, str]:
    lines = text.split('\n')
    result = {}
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and is_start(lines[i], lines[i + 1]):
            if i + 2 >= len(lines):
                break
            oui = lines[i + 2][:6].strip().upper()
            owner = re.sub(r'\((hex|base 16)\)', '', lines[i + 1])[10:].strip()

            i += 3
            while i < len(lines) and (i + 1 >= len(lines) or not is_start(lines[i], lines[i + 1])):
                # Only keep company name, don't keep address
                # if lines[i] and lines[i].strip():
                #     owner += f"\n{lines[i].strip()}"
                i += 1

            owner = re.sub(r'[ \t]+', ' ', owner)
            if len(oui) == 6:
                mac_prefix = f"{oui[0:2]}:{oui[2:4]}:{oui[4:6]}".lower()
                result[mac_prefix] = owner
        else:
            i += 1
    return result

async def update_oui_data(db: Session):
    logger.info(f"Downloading OUI data from {OUI_URL}")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(OUI_URL)
            response.raise_for_status()
            text = response.text

            if not re.search(r'^(OUI|[#]|[A-Fa-f0-9])', text):
                raise ValueError("Downloaded file does not look like a oui-data.txt file")

            logger.info("Parsing OUI data...")
            entries = parse_oui_data(text)
            logger.info(f"Parsed {len(entries)} OUI entries. Updating database...")

            # Using SQLite ON CONFLICT DO UPDATE
            stmt = insert(OuiEntry).values([
                {"mac_prefix": mac_prefix, "vendor": vendor}
                for mac_prefix, vendor in entries.items()
            ])

            stmt = stmt.on_conflict_do_update(
                index_elements=['mac_prefix'],
                set_=dict(vendor=stmt.excluded.vendor)
            )

            # Execute in batches if it's too large, but 30k entries should be fine in one go for SQLite
            db.execute(stmt)
            db.commit()
            logger.info("OUI data update complete.")

    except Exception as e:
        logger.error(f"Failed to update OUI data: {e}")
        db.rollback()
