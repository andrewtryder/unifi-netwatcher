from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.db import Base
from app.models import OuiEntry
from app.oui import OUI_MIN_ENTRIES, parse_oui_data, update_oui_data, upsert_oui_entries
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sample_oui_text = """
28-6F-B9   (hex)		Nokia Shanghai Bell Co., Ltd.
286FB9     (base 16)		Nokia Shanghai Bell Co., Ltd.
				No.388 Ning Qiao Road,Jin Qiao Pudong Shanghai
				Shanghai     201206
				CN

38-E2-CA   (hex)		Katun Corporation
38E2CA     (base 16)		Katun Corporation
				7760 France Ave SSuite 340
				Bloomington  MN  55438
				US
"""


def test_parse_oui_data():
    entries = parse_oui_data(sample_oui_text)
    assert len(entries) == 2
    assert entries.get("28:6f:b9") == "Nokia Shanghai Bell Co., Ltd."
    assert entries.get("38:e2:ca") == "Katun Corporation"


def test_upsert_oui_entries_batches():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    entries = {f"aa:bb:{i:02x}": f"Vendor {i}" for i in range(5)}
    with patch("app.oui.OUI_UPSERT_BATCH_SIZE", 2):
        upsert_oui_entries(db, entries)

    assert db.query(OuiEntry).count() == 5
    assert db.query(OuiEntry).filter_by(mac_prefix="aa:bb:01").first().vendor == "Vendor 1"
    db.close()


@pytest.mark.asyncio
async def test_update_oui_data(monkeypatch):
    monkeypatch.setattr("app.oui.OUI_MIN_ENTRIES", 1)
    monkeypatch.setattr("app.oui.OUI_MAX_ENTRIES", 10)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    class MockResponse:
        def __init__(self, text, status_code):
            self.text = text
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code != 200:
                raise httpx.HTTPStatusError("Error", request=MagicMock(), response=self)

    async def mock_get(*args, **kwargs):
        return MockResponse("OUI\n" + sample_oui_text, 200)

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        await update_oui_data(db)

    entries = db.query(OuiEntry).all()
    assert len(entries) == 2

    nokia = db.query(OuiEntry).filter_by(mac_prefix="28:6f:b9").first()
    assert nokia.vendor == "Nokia Shanghai Bell Co., Ltd."
    db.close()


@pytest.mark.asyncio
async def test_update_oui_data_rejects_small_payload(monkeypatch):
    monkeypatch.setattr("app.oui.OUI_MIN_ENTRIES", 100)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    class MockResponse:
        text = "OUI\n" + sample_oui_text
        status_code = 200

        def raise_for_status(self):
            return None

    async def mock_get(*args, **kwargs):
        return MockResponse()

    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        await update_oui_data(db)

    assert db.query(OuiEntry).count() == 0
    db.close()
    assert OUI_MIN_ENTRIES == 1_000
