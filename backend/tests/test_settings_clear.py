"""QA fix verification: explicit key clearing for the settings API.

Covers the Phase 2.5 finding that a saved key could never be removed:
empty input must mean "keep current value", and removal must be an
explicit, confirmed operation with correct status reporting afterwards.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import backend.settings as settings
from backend.app import app


def client(host='127.0.0.1'):
    return TestClient(app, base_url='http://127.0.0.1:8000', client=(host, 50000))


def put(payload, host='127.0.0.1'):
    return client(host).put('/api/settings', json=payload)


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / 'settings.env'
    monkeypatch.setattr(settings, 'SETTINGS_PATH', path)
    return path


def test_clear_removes_saved_key_from_file_and_environment(settings_file):
    assert put({'nebius_api_key': 'synthetic-key'}).status_code == 200
    assert settings.read_settings().get('NEBIUS_API_KEY') == 'synthetic-key'

    response = put({'clear_nebius_api_key': True})
    assert response.status_code == 200
    body = response.json()
    assert body['settings']['nebius_api_key'] is False
    assert body['saved_keys']['nebius_api_key'] is False
    assert 'NEBIUS_API_KEY' not in settings.read_settings()
    assert not os.environ.get('NEBIUS_API_KEY')


def test_environment_only_key_is_reported_separately(settings_file, monkeypatch):
    monkeypatch.setenv('NEBIUS_API_KEY', 'from-environment')
    body = client().get('/api/settings').json()
    assert body['settings']['nebius_api_key'] is True
    assert body['saved_keys']['nebius_api_key'] is False
    assert body['environment_keys']['nebius_api_key'] is True


def test_saved_key_is_not_reported_as_environment_sourced(settings_file):
    put({'tavily_api_key': 'synthetic-tavily'})
    body = client().get('/api/settings').json()
    assert body['settings']['tavily_api_key'] is True
    assert body['saved_keys']['tavily_api_key'] is True
    assert body['environment_keys']['tavily_api_key'] is False


def test_clear_of_environment_only_key_deactivates_runtime(settings_file, monkeypatch):
    monkeypatch.setenv('TAVILY_API_KEY', 'env-only')
    response = put({'clear_tavily_api_key': True})
    assert response.status_code == 200
    assert response.json()['settings']['tavily_api_key'] is False
    assert not os.environ.get('TAVILY_API_KEY')


def test_set_and_clear_same_key_in_one_request_is_rejected(settings_file):
    response = put({'nebius_api_key': 'value', 'clear_nebius_api_key': True})
    assert response.status_code == 422
    assert 'NEBIUS_API_KEY' not in settings.read_settings()


def test_empty_value_keeps_the_saved_key(settings_file):
    put({'nebius_api_key': 'synthetic-key'})
    response = put({'nebius_api_key': ''})
    assert response.status_code == 200
    assert settings.read_settings().get('NEBIUS_API_KEY') == 'synthetic-key'
    assert response.json()['settings']['nebius_api_key'] is True


def test_clear_is_rejected_for_remote_and_cross_origin_clients(settings_file):
    assert put({'tavily_api_key': 'synthetic-tavily'}).status_code == 200
    remote = client('192.0.2.10').put('/api/settings', json={'clear_tavily_api_key': True})
    assert remote.status_code == 403
    cross_origin = client().put('/api/settings', json={'clear_tavily_api_key': True},
                                headers={'Origin': 'https://example.com'})
    assert cross_origin.status_code == 403
    assert settings.read_settings().get('TAVILY_API_KEY') == 'synthetic-tavily'


def test_clearing_contact_email_clears_the_sec_user_agent(settings_file):
    assert put({'sec_contact_email': 'contact@example.com'}).status_code == 200
    assert os.environ.get('SEC_USER_AGENT')

    response = put({'clear_sec_contact_email': True})
    assert response.status_code == 200
    assert response.json()['settings']['sec_contact_email'] is False
    assert 'SEC_CONTACT_EMAIL' not in settings.read_settings()
    assert not os.environ.get('SEC_USER_AGENT')


def test_probe_saved_settings_do_not_leak_between_tests(settings_file):
    """Deliberately mutates provider env through the API.

    The session guards in conftest must restore the environment afterwards;
    test_zz_isolation_guards verifies that from a later position in the run.
    """
    response = put({'sec_contact_email': 'probe@example.com', 'nebius_model': 'probe-model'})
    assert response.status_code == 200
    assert os.environ.get('SEC_CONTACT_EMAIL') == 'probe@example.com'
    assert os.environ.get('SEC_USER_AGENT')
