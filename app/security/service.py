"""Security policy service: passwords, CIDR, trusted hosts, settings load/update."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import secrets
from dataclasses import dataclass

from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.security.models import (
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    LOCKOUT_CONFIRMATION_PHRASE,
    MAX_CIDR_ENTRIES,
    MAX_HOST_ENTRIES,
    MIN_PASSWORD_LENGTH,
    SECURITY_SETTINGS_ID,
    USERNAME_PATTERN,
    SecuritySettings,
)

logger = logging.getLogger(__name__)

_password_hash = PasswordHash.recommended()
_policy_cache: SecurityPolicy | None = None
_USERNAME_RE = re.compile(USERNAME_PATTERN)

# Hostnames always allowed when host restriction is enabled (in addition to
# private/loopback IP-literal Host values and the configured allowlist).
BUILTIN_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class SecurityPolicy:
    authentication_enabled: bool
    authentication_username: str
    authentication_password_hash: str
    default_credentials_active: bool
    cidr_restriction_enabled: bool
    allowed_cidrs: tuple[str, ...]
    host_restriction_enabled: bool
    allowed_hosts: tuple[str, ...]


@dataclass
class AuthUpdateResult:
    ok: bool
    message: str
    errors: list[str] | None = None


@dataclass
class CidrUpdateResult:
    ok: bool
    message: str
    errors: list[str] | None = None
    normalized_cidrs: list[str] | None = None


@dataclass
class HostsUpdateResult:
    ok: bool
    message: str
    errors: list[str] | None = None
    normalized_hosts: list[str] | None = None


def invalidate_security_cache() -> None:
    global _policy_cache
    _policy_cache = None


def get_cached_security_policy() -> SecurityPolicy | None:
    """Return the in-memory policy without opening a DB session."""
    return _policy_cache


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except Exception:
        return False


def validate_username(username: str) -> list[str]:
    errors: list[str] = []
    if not username:
        errors.append("Username is required.")
        return errors
    if ":" in username:
        errors.append("Username must not contain a colon.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in username):
        errors.append("Username must not contain control characters.")
    if not _USERNAME_RE.fullmatch(username):
        errors.append(
            "Username must be 1–64 characters of ASCII letters, digits, dot, underscore, or hyphen."
        )
    return errors


def _row_to_policy(row: SecuritySettings) -> SecurityPolicy:
    return SecurityPolicy(
        authentication_enabled=bool(row.authentication_enabled),
        authentication_username=row.authentication_username or "",
        authentication_password_hash=row.authentication_password_hash or "",
        default_credentials_active=bool(row.default_credentials_active),
        cidr_restriction_enabled=bool(row.cidr_restriction_enabled),
        allowed_cidrs=tuple(get_allowed_cidrs_list(row)),
        host_restriction_enabled=bool(getattr(row, "host_restriction_enabled", True)),
        allowed_hosts=tuple(get_allowed_hosts_list(row)),
    )


def ensure_security_settings(db: Session) -> SecuritySettings:
    """Idempotently create the singleton security row with hashed default password."""
    global _policy_cache
    existing = (
        db.query(SecuritySettings).filter(SecuritySettings.id == SECURITY_SETTINGS_ID).first()
    )
    if existing:
        _policy_cache = _row_to_policy(existing)
        return existing

    row = SecuritySettings(
        id=SECURITY_SETTINGS_ID,
        authentication_enabled=True,
        authentication_username=DEFAULT_USERNAME,
        authentication_password_hash=hash_password(DEFAULT_PASSWORD),
        default_credentials_active=True,
        cidr_restriction_enabled=False,
        allowed_cidrs="[]",
        host_restriction_enabled=True,
        allowed_hosts="[]",
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError:
        db.rollback()
        row = db.query(SecuritySettings).filter(SecuritySettings.id == SECURITY_SETTINGS_ID).first()
        if not row:
            raise
    _policy_cache = _row_to_policy(row)
    return row


def get_security_settings(db: Session) -> SecuritySettings:
    return ensure_security_settings(db)


def get_security_policy(db: Session, *, use_cache: bool = True) -> SecurityPolicy:
    global _policy_cache
    if use_cache and _policy_cache is not None:
        return _policy_cache
    row = ensure_security_settings(db)
    _policy_cache = _row_to_policy(row)
    return _policy_cache


def get_allowed_cidrs_list(settings: SecuritySettings) -> list[str]:
    try:
        data = json.loads(settings.allowed_cidrs or "[]")
        if isinstance(data, list):
            return [str(x) for x in data]
    except TypeError, ValueError, json.JSONDecodeError:
        pass
    return []


def get_allowed_hosts_list(settings: SecuritySettings) -> list[str]:
    try:
        data = json.loads(getattr(settings, "allowed_hosts", None) or "[]")
        if isinstance(data, list):
            return [str(x).strip().lower() for x in data if str(x).strip()]
    except TypeError, ValueError, json.JSONDecodeError:
        pass
    return []


def parse_and_normalize_cidrs(text: str) -> tuple[list[str], list[str]]:
    """Parse multiline CIDR text. Returns (normalized, errors)."""
    errors: list[str] = []
    normalized: list[str] = []
    seen: set[str] = set()

    lines = text.splitlines() if text else []
    non_blank = 0
    for i, raw in enumerate(lines, start=1):
        value = raw.strip()
        if not value:
            continue
        non_blank += 1
        if non_blank > MAX_CIDR_ENTRIES:
            errors.append(f"At most {MAX_CIDR_ENTRIES} CIDR entries are allowed.")
            break
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            errors.append(f"Line {i}: invalid CIDR '{value}'")
            continue
        canonical = str(network)
        if canonical in seen:
            continue
        seen.add(canonical)
        normalized.append(canonical)

    return normalized, errors


def normalize_request_host(host_header: str | None) -> str:
    """Strip port from a Host header value; lowercase the result."""
    if not host_header:
        return ""
    value = host_header.strip().lower()
    if not value:
        return ""
    if value.startswith("["):
        # IPv6 literal: [::1] or [::1]:8080
        end = value.find("]")
        if end == -1:
            return value
        return value[1:end]
    if value.count(":") == 1:
        # hostname:port or ipv4:port
        return value.rsplit(":", 1)[0]
    return value


def parse_and_normalize_hosts(text: str) -> tuple[list[str], list[str]]:
    """Parse multiline hostname allowlist. Returns (normalized, errors)."""
    errors: list[str] = []
    normalized: list[str] = []
    seen: set[str] = set()

    lines = text.splitlines() if text else []
    non_blank = 0
    for i, raw in enumerate(lines, start=1):
        value = raw.strip().lower()
        if not value:
            continue
        non_blank += 1
        if non_blank > MAX_HOST_ENTRIES:
            errors.append(f"At most {MAX_HOST_ENTRIES} host entries are allowed.")
            break
        if "/" in value or " " in value:
            errors.append(f"Line {i}: invalid host '{raw.strip()}'")
            continue
        host = normalize_request_host(value)
        if not host:
            errors.append(f"Line {i}: invalid host '{raw.strip()}'")
            continue
        # Reject wildcards and empty labels; allow DNS names and IP literals.
        if "*" in host or host.startswith(".") or host.endswith("."):
            errors.append(f"Line {i}: invalid host '{raw.strip()}'")
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # DNS name: basic label check
            if not re.fullmatch(
                r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*",
                host,
            ):
                errors.append(f"Line {i}: invalid host '{raw.strip()}'")
                continue
        if host in seen:
            continue
        seen.add(host)
        normalized.append(host)

    return normalized, errors


def _is_private_or_loopback_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(addr.is_private or addr.is_loopback)


def host_allowed(
    host: str,
    *,
    host_restriction_enabled: bool,
    allowed_hosts: list[str] | tuple[str, ...],
) -> bool:
    """Return whether a normalized Host value is permitted."""
    if not host_restriction_enabled:
        return True
    if not host:
        return False
    normalized = normalize_request_host(host)
    if not normalized:
        return False
    if normalized in BUILTIN_ALLOWED_HOSTS:
        return True
    if _is_private_or_loopback_ip(normalized):
        return True
    allow = {h.lower() for h in allowed_hosts}
    return normalized in allow


def client_ip_allowed(client_ip: str, cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if addr in network:
            return True
    return False


def authenticate(policy: SecuritySettings | SecurityPolicy, username: str, password: str) -> bool:
    """Constant-time username compare + password verify. Generic failure."""
    expected_user = policy.authentication_username or ""
    user_ok = secrets.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
    pass_ok = verify_password(password, policy.authentication_password_hash or "")
    return bool(user_ok and pass_ok)


def preview_cidrs(
    text: str,
    *,
    client_ip: str,
    cidr_enabled: bool,
) -> dict:
    normalized, errors = parse_and_normalize_cidrs(text)
    valid = len(errors) == 0
    if cidr_enabled and valid and not normalized:
        errors.append("CIDR filtering cannot be enabled with an empty allowlist.")
        valid = False

    will_remain = True
    if cidr_enabled and normalized:
        will_remain = client_ip_allowed(client_ip, normalized)
    elif cidr_enabled and not normalized:
        will_remain = False

    return {
        "valid": valid,
        "normalized_cidrs": normalized,
        "effective_client_ip": client_ip,
        "client_will_remain_allowed": will_remain,
        "errors": errors,
    }


def preview_hosts(
    text: str,
    *,
    request_host: str,
    host_enabled: bool,
) -> dict:
    normalized, errors = parse_and_normalize_hosts(text)
    valid = len(errors) == 0
    effective = normalize_request_host(request_host)

    will_remain = True
    if host_enabled and valid:
        will_remain = host_allowed(
            effective,
            host_restriction_enabled=True,
            allowed_hosts=normalized,
        )

    return {
        "valid": valid,
        "normalized_hosts": normalized,
        "effective_request_host": effective,
        "client_will_remain_allowed": will_remain,
        "errors": errors,
    }


def update_authentication(
    db: Session,
    *,
    authentication_enabled: bool,
    username: str,
    current_password: str,
    new_password: str,
    confirm_password: str,
    confirm_disable: bool,
) -> AuthUpdateResult:
    settings = ensure_security_settings(db)
    errors: list[str] = []

    username = (username or "").strip()
    errors.extend(validate_username(username))

    changing_username = username != settings.authentication_username
    changing_password = bool(new_password)
    disabling = settings.authentication_enabled and not authentication_enabled

    needs_current = changing_username or changing_password or disabling
    if needs_current:
        if not current_password:
            errors.append("Current password is required for this change.")
        elif not verify_password(current_password, settings.authentication_password_hash):
            errors.append("Current password is incorrect.")

    if disabling and not confirm_disable:
        errors.append("Confirm that you understand the risk of disabling authentication.")

    if changing_password:
        if new_password != confirm_password:
            errors.append("New password and confirmation do not match.")
        if len(new_password) < MIN_PASSWORD_LENGTH:
            errors.append(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")

    if errors:
        return AuthUpdateResult(ok=False, message="Could not update authentication.", errors=errors)

    settings.authentication_username = username
    settings.authentication_enabled = authentication_enabled

    if changing_password:
        settings.authentication_password_hash = hash_password(new_password)
        settings.default_credentials_active = False

    db.add(settings)
    db.commit()
    db.refresh(settings)
    invalidate_security_cache()
    _bind_cache(settings)

    return AuthUpdateResult(ok=True, message="Authentication settings saved.")


def update_cidr(
    db: Session,
    *,
    cidr_restriction_enabled: bool,
    cidrs_text: str,
    client_ip: str,
    allow_lockout: bool,
    lockout_confirmation: str,
) -> CidrUpdateResult:
    settings = ensure_security_settings(db)
    normalized, errors = parse_and_normalize_cidrs(cidrs_text)

    if cidr_restriction_enabled and not normalized and not errors:
        errors.append("CIDR filtering cannot be enabled with an empty allowlist.")

    if errors:
        return CidrUpdateResult(
            ok=False,
            message="Could not update network restrictions.",
            errors=errors,
            normalized_cidrs=normalized,
        )

    will_remain = True
    if cidr_restriction_enabled:
        will_remain = client_ip_allowed(client_ip, normalized)

    if cidr_restriction_enabled and not will_remain:
        if not allow_lockout or (lockout_confirmation or "").strip() != LOCKOUT_CONFIRMATION_PHRASE:
            return CidrUpdateResult(
                ok=False,
                message=(
                    "These rules would block your current IP. "
                    "Enable the lockout override and type ALLOW LOCKOUT to proceed."
                ),
                errors=[
                    f"Current client IP {client_ip} is not covered by the allowlist.",
                ],
                normalized_cidrs=normalized,
            )

    settings.cidr_restriction_enabled = cidr_restriction_enabled
    settings.allowed_cidrs = json.dumps(normalized)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    invalidate_security_cache()
    _bind_cache(settings)

    return CidrUpdateResult(
        ok=True,
        message="Network restrictions saved.",
        normalized_cidrs=normalized,
    )


def update_hosts(
    db: Session,
    *,
    host_restriction_enabled: bool,
    hosts_text: str,
    request_host: str,
    allow_lockout: bool,
    lockout_confirmation: str,
) -> HostsUpdateResult:
    settings = ensure_security_settings(db)
    normalized, errors = parse_and_normalize_hosts(hosts_text)

    if errors:
        return HostsUpdateResult(
            ok=False,
            message="Could not update trusted hosts.",
            errors=errors,
            normalized_hosts=normalized,
        )

    effective = normalize_request_host(request_host)
    will_remain = True
    if host_restriction_enabled:
        will_remain = host_allowed(
            effective,
            host_restriction_enabled=True,
            allowed_hosts=normalized,
        )

    if host_restriction_enabled and not will_remain:
        if not allow_lockout or (lockout_confirmation or "").strip() != LOCKOUT_CONFIRMATION_PHRASE:
            return HostsUpdateResult(
                ok=False,
                message=(
                    "These rules would block your current Host. "
                    "Enable the lockout override and type ALLOW LOCKOUT to proceed."
                ),
                errors=[
                    f"Current request host '{effective}' is not covered by the allowlist "
                    "(private/loopback IP literals and localhost remain allowed)."
                ],
                normalized_hosts=normalized,
            )

    settings.host_restriction_enabled = host_restriction_enabled
    settings.allowed_hosts = json.dumps(normalized)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    invalidate_security_cache()
    _bind_cache(settings)

    return HostsUpdateResult(
        ok=True,
        message="Trusted hosts saved.",
        normalized_hosts=normalized,
    )


def _bind_cache(settings: SecuritySettings) -> None:
    global _policy_cache
    _policy_cache = _row_to_policy(settings)


def public_security_flags(db: Session) -> dict:
    """Safe flags for templates (never includes password or hash)."""
    policy = get_security_policy(db)
    return {
        "authentication_enabled": policy.authentication_enabled,
        "default_credentials_active": policy.default_credentials_active,
        "cidr_restriction_enabled": policy.cidr_restriction_enabled,
        "host_restriction_enabled": policy.host_restriction_enabled,
        "security_username": policy.authentication_username,
        "allowed_cidrs_text": "\n".join(policy.allowed_cidrs),
        "allowed_hosts_text": "\n".join(policy.allowed_hosts),
    }
