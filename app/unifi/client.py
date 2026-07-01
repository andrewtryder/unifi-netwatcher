import json
import logging
from pathlib import Path
import httpx
from typing import List, Dict, Any

from app.config import settings

logger = logging.getLogger(__name__)

class UnifiClient:
    def __init__(self):
        self.base_url = settings.UNIFI_URL.rstrip('/')
        self.site = settings.UNIFI_SITE
        self.username = settings.UNIFI_USERNAME
        self.password = settings.UNIFI_PASSWORD
        self.verify_ssl = settings.UNIFI_VERIFY_SSL
        self.timeout = settings.UNIFI_TIMEOUT_SECONDS
        self.mock_mode = settings.UNIFI_MOCK_MODE
        self.client = httpx.Client(verify=self.verify_ssl, timeout=self.timeout)
        self._logged_in = False

    def _get_mock_data(self) -> List[Dict[str, Any]]:
        mock_file = Path(__file__).parent / "mock_unifi_data.json"
        try:
            with open(mock_file, 'r') as f:
                data = json.load(f)
                return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to load mock data: {e}")
            return []

    def login(self) -> bool:
        if self.mock_mode:
            logger.info("UniFi Client running in MOCK MODE. Simulating login.")
            self._logged_in = True
            return True

        login_url = f"{self.base_url}/api/auth/login"
        payload = {"username": self.username, "password": self.password}
        try:
            r = self.client.post(login_url, json=payload)
            r.raise_for_status()
            self._logged_in = True
            return True
        except Exception as e:
            logger.error(f"Failed to login to UniFi Controller: {e}")
            return False

    def get_clients(self) -> List[Dict[str, Any]]:
        if self.mock_mode:
            logger.info("UniFi Client running in MOCK MODE. Returning mock clients.")
            return self._get_mock_data()

        if not self._logged_in:
            if not self.login():
                return []

        url = f"{self.base_url}/proxy/network/api/s/{self.site}/stat/sta"
        try:
            r = self.client.get(url)
            if r.status_code == 401:
                # Token might have expired, try logging in again
                if self.login():
                     r = self.client.get(url)
            r.raise_for_status()
            data = r.json()
            return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch clients from UniFi Controller: {e}")
            return []
