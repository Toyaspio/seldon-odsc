from http import HTTPStatus
import socket

import pytest

from backend import settings
from backend.tests.test_p1_settings import local_client, local_settings


@pytest.fixture
def source_state(monkeypatch):
    monkeypatch.setattr(settings, '_startup_sources', {})
    monkeypatch.setattr(settings, '_applied_local_keys', set())


def test_startup_and_save_report_effective_source_without_returning_values(
        local_settings, source_state, monkeypatch):
    inherited, dotenv, replacement = 'fixture-inherited', 'fixture-dotenv', 'fixture-replacement'
    monkeypatch.setenv('NEBIUS_API_KEY', inherited)
    local_settings.write_text('NEBIUS_API_KEY=fixture-previous\n'
                              f'TAVILY_API_KEY={dotenv}\n'
                              'SEC_CONTACT_EMAIL=fixture-contact@example.test\n', encoding='utf-8')
    monkeypatch.setattr(settings, 'load_dotenv', lambda: monkeypatch.setenv('TAVILY_API_KEY', dotenv))
    settings.load_startup_settings()

    client = local_client()
    initial = client.get('/api/settings')
    assert initial.json()['effective_sources'] == {
        'nebius_api_key': 'environment', 'tavily_api_key': 'dotenv',
        'sec_contact_email': 'local_settings',
    }
    assert initial.json()['environment_keys']['tavily_api_key'] is False
    saved = client.put('/api/settings', json={'nebius_api_key': replacement})
    assert saved.status_code == HTTPStatus.OK
    assert saved.json()['effective_sources']['nebius_api_key'] == 'local_settings'
    assert saved.json()['effective_sources']['tavily_api_key'] == 'dotenv'
    assert saved.json()['startup_overrides']['nebius_api_key'] == 'environment'
    assert settings.runtime_values('NEBIUS_API_KEY', 'TAVILY_API_KEY') == (replacement, dotenv)
    refreshed = client.get('/api/settings')
    assert refreshed.json() == saved.json()
    for response in (initial, saved, refreshed):
        for secret in (inherited, dotenv, replacement, 'fixture-previous', 'fixture-contact@example.test'):
            assert secret not in response.text


@pytest.mark.parametrize('configured, expected', [(False, 'not_configured'), (True, 'unavailable')])
def test_tls_check_status_and_failure_are_secret_free(local_settings, monkeypatch, configured, expected):
    monkeypatch.setenv('NEBIUS_API_KEY', 'fixture-nebius')
    if configured:
        monkeypatch.setenv('TAVILY_API_KEY', 'fixture-tavily')
    else:
        monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    contacted = []

    class LocalSocket:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class LocalContext:
        def wrap_socket(self, raw, *, server_hostname):
            assert server_hostname == 'api.tokenfactory.nebius.com'
            return raw

    def probe(address, timeout):
        contacted.append(address)
        if address[0] != 'api.tokenfactory.nebius.com':
            raise OSError('fixture-tavily')
        return LocalSocket()

    monkeypatch.setattr(socket, 'create_connection', probe)
    monkeypatch.setattr('ssl.create_default_context', LocalContext)
    result = local_client().post('/api/settings/connections')
    assert result.status_code == HTTPStatus.OK
    assert result.json()['checks'] == {'nebius': 'reachable', 'tavily': expected}
    assert result.json()['provider_authentication'] == 'untested'
    assert not result.json()['paid_request_made']
    assert 'fixture-' not in result.text
    assert contacted == [('api.tokenfactory.nebius.com', 443), ('api.tavily.com', 443), ('data.sec.gov', 443)]
