"""The environment is read in one module; these pin what it does with it."""

from __future__ import annotations

import pytest

from kb.config import MissingSetting, WebhookSettings, migrations_dir, process_environment


def test_the_webhook_refuses_to_start_without_a_secret():
    # Covers: NFR-CFG-01
    with pytest.raises(MissingSetting) as caught:
        WebhookSettings.from_env({})
    assert caught.value.variable == "KB_WEBHOOK_SECRET"


def test_the_webhook_binds_loopback_unless_told_otherwise():
    # Covers: NFR-CFG-01
    settings = WebhookSettings.from_env({"KB_WEBHOOK_SECRET": "s"})
    assert (settings.host, settings.port) == ("127.0.0.1", 8323)

    settings = WebhookSettings.from_env(
        {"KB_WEBHOOK_SECRET": "s", "KB_WEBHOOK_HOST": "0.0.0.0", "KB_WEBHOOK_PORT": "9000"}
    )
    assert (settings.host, settings.port) == ("0.0.0.0", 9000)


def test_migrations_dir_prefers_the_environment(monkeypatch, tmp_path):
    # Covers: NFR-CFG-01
    monkeypatch.setenv("KB_MIGRATIONS_DIR", str(tmp_path))
    assert migrations_dir() == tmp_path
    assert process_environment()["KB_MIGRATIONS_DIR"] == str(tmp_path)


def test_migrations_dir_finds_the_checkout_without_it(monkeypatch):
    # Covers: NFR-CFG-01
    monkeypatch.delenv("KB_MIGRATIONS_DIR", raising=False)
    assert (migrations_dir() / "001_initial.sql").exists()
