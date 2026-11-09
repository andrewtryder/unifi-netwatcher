from __future__ import annotations

from abc import ABC, abstractmethod

from app.unifi.models import UniFiCapabilities, UniFiClientDevice


class UniFiAdapter(ABC):
    """Abstract base class for UniFi network controller and API adapters."""

    adapter_name: str = "base"
    capabilities: UniFiCapabilities = UniFiCapabilities()

    @abstractmethod
    def get_clients(self) -> list[UniFiClientDevice]:
        """Fetch active client devices from the configured UniFi site.

        Returns:
            A list of normalized `UniFiClientDevice` instances. An empty list
            indicates that zero clients are currently active on the site.

        Raises:
            UniFiAuthError: Authentication failure (invalid credentials or token).
            UniFiPermissionError: Insufficient authorization or permission.
            UniFiConnectionError: Controller unreachable or network failure.
            UniFiTimeoutError: Request or connect timeout.
            UniFiResponseError: Malformed response or unhandled server error.
            UniFiError: Generic UniFi adapter failure.
        """

    @abstractmethod
    def block_client(self, mac: str) -> bool:
        """Block a client device by its MAC address."""

    @abstractmethod
    def unblock_client(self, mac: str) -> bool:
        """Unblock a previously blocked client device by its MAC address."""

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Test connection and authentication to the UniFi controller.

        Returns:
            Tuple of `(success, message)`.
        """

    @abstractmethod
    def close(self) -> None:
        """Close any open client sessions or connection pools."""
