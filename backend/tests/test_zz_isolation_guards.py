"""Session isolation guards (run last alphabetically).

These tests prove the suite is hermetic: storage paths bind to the per-session
temporary directory instead of the real user data, the provider environment
returns to its session baseline after every test, and the real user database
byte content is identical before and after the whole run.

The module deliberately takes its baseline snapshot inside the session-scoped
fixture (not at import time): pytest imports test modules during collection,
before any session fixture runs, so an import-time snapshot would run before
conftest has isolated the environment and would capture real saved settings.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import backend.settings as settings
from backend.storage import store

_PROVIDER_ENV_KEYS = ('NEBIUS_API_KEY', 'NEBIUS_MODEL', 'TAVILY_API_KEY',
                      'SEC_CONTACT_EMAIL', 'SEC_USER_AGENT')
_BASELINE: dict[str, str | None] = {}

_REAL_DB = (Path(os.environ.get('LOCALAPPDATA') or Path.home())
            / 'SeldonSystem' / 'tasks.sqlite3')


def _digest() -> str | None:
    if not _REAL_DB.is_file():
        return None
    return hashlib.sha256(_REAL_DB.read_bytes()).hexdigest()


_DIGEST_AT_COLLECTION = _digest()


@pytest.fixture(scope='module', autouse=True)
def _snapshot_baseline(_isolated_data_environment):
    """Snapshot after conftest isolation is active (module import is too early)."""
    _BASELINE.update({key: os.environ.get(key) for key in _PROVIDER_ENV_KEYS})


def test_task_database_is_bound_to_the_isolated_session_directory():
    session_dir = os.environ.get('SELDON_DATA_DIR')
    assert session_dir, 'SELDON_DATA_DIR must be bound by the test conftest before backend imports'
    assert store.DB_PATH == Path(session_dir) / 'tasks.sqlite3', (
        f'store.DB_PATH={store.DB_PATH} is not the isolated session database')
    real = (Path(os.environ.get('LOCALAPPDATA') or Path.home())
            / 'SeldonSystem' / 'tasks.sqlite3')
    assert store.DB_PATH != real


def test_settings_file_is_bound_to_the_isolated_session_directory():
    session_file = os.environ.get('SELDON_SETTINGS_FILE')
    assert session_file, 'SELDON_SETTINGS_FILE must be bound by the test conftest'
    assert settings.SETTINGS_PATH == Path(session_file)


def test_provider_environment_matches_session_baseline():
    current = {key: os.environ.get(key) for key in _PROVIDER_ENV_KEYS}
    assert current == _BASELINE, 'provider environment leaked across tests'


def test_real_user_database_was_not_modified_during_the_run():
    assert _digest() == _DIGEST_AT_COLLECTION, (
        f'the real user database {_REAL_DB} changed during the test run')
