import pytest
from app.config import Settings, _parse_bool
from pydantic import SecretStr, ValidationError


def test_parse_bool_strict():
    assert _parse_bool("true") is True
    assert _parse_bool("false") is False
    assert _parse_bool("1") is True
    assert _parse_bool("0") is False
    assert _parse_bool("yes") is True
    assert _parse_bool("no") is False
    with pytest.raises(ValueError):
        _parse_bool("")
    with pytest.raises(ValueError):
        _parse_bool("maybe")


def test_settings_rejects_placeholder_secret_in_production():
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            APP_SECRET_KEY=SecretStr("change-me"),
            UNIFI_MOCK_MODE=True,
            _env_file=None,
        )


def test_settings_allows_empty_secret_in_production_for_file_backed_key():
    s = Settings(
        APP_ENV="production",
        APP_SECRET_KEY=SecretStr(""),
        UNIFI_MOCK_MODE=True,
        _env_file=None,
    )
    assert s.app_secret_key() == ""


def test_settings_rejects_short_secret_in_production():
    with pytest.raises(ValidationError):
        Settings(
            APP_ENV="production",
            APP_SECRET_KEY=SecretStr("short"),
            UNIFI_MOCK_MODE=True,
            _env_file=None,
        )


def test_settings_accepts_development_placeholder():
    s = Settings(
        APP_ENV="development",
        APP_SECRET_KEY=SecretStr("change-me"),
        UNIFI_MOCK_MODE=True,
        _env_file=None,
    )
    assert s.APP_ENV == "development"


def test_settings_rejects_invalid_timeout(monkeypatch):
    monkeypatch.setenv("UNIFI_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_SECRET_KEY", "pytest-secret-key-not-for-prod")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_rejects_invalid_bool(monkeypatch):
    monkeypatch.setenv("UNIFI_VERIFY_SSL", "maybe")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_SECRET_KEY", "pytest-secret-key-not-for-prod")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_unifi_verify_defaults_and_ca_bundle(tmp_path):
    ca = tmp_path / "unifi-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
    s = Settings(
        APP_ENV="development",
        APP_SECRET_KEY=SecretStr("change-me"),
        UNIFI_MOCK_MODE=True,
        UNIFI_VERIFY_SSL=True,
        UNIFI_CA_BUNDLE=str(ca),
        _env_file=None,
    )
    assert s.unifi_verify() == str(ca)
    s2 = Settings(
        APP_ENV="development",
        APP_SECRET_KEY=SecretStr("change-me"),
        UNIFI_MOCK_MODE=True,
        UNIFI_VERIFY_SSL=False,
        UNIFI_CA_BUNDLE=str(ca),
        _env_file=None,
    )
    assert s2.unifi_verify() is False
    s3 = Settings(
        APP_ENV="development",
        APP_SECRET_KEY=SecretStr("change-me"),
        UNIFI_MOCK_MODE=True,
        UNIFI_VERIFY_SSL=True,
        UNIFI_CA_BUNDLE="",
        _env_file=None,
    )
    assert s3.unifi_verify() is True
