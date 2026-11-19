from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from backend import app as api, settings
from backend.providers.live import NebiusModelProvider
from backend.tests.test_phase2 import PLAN


@pytest.fixture
def local_settings(monkeypatch, tmp_path):
    path = tmp_path / 'settings.env'
    monkeypatch.setattr(settings, 'SETTINGS_PATH', path)
    return path


def local_client():
    return TestClient(api.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000))


@pytest.mark.parametrize('updates', [
    {'sec_contact_email': 'changed@example.test'},
    {'nebius_api_key': 'fixture-changed-nebius', 'nebius_model': 'fixture-changed-model'},
    {'tavily_api_key': 'fixture-changed-tavily'},
    {'sec_contact_email': 'changed@example.test', 'nebius_model': 'fixture-changed-model',
     'tavily_api_key': 'fixture-changed-tavily'},
])
def test_save_updates_only_submitted_values(local_settings, monkeypatch, updates):
    injected = {'NEBIUS_API_KEY': 'fixture-env-nebius', 'NEBIUS_MODEL': 'fixture-env-model',
                'TAVILY_API_KEY': 'fixture-env-tavily', 'SEC_CONTACT_EMAIL': 'old@example.test'}
    for key, value in injected.items():
        monkeypatch.setenv(key, value)
    local_settings.write_text('SEC_CONTACT_EMAIL=old@example.test\n', encoding='utf-8')
    response = local_client().put('/api/settings', json=updates)
    assert response.status_code == 200
    changed = {key.upper(): value for key, value in updates.items()}
    assert settings.read_settings() == {'SEC_CONTACT_EMAIL': injected['SEC_CONTACT_EMAIL'], **changed}
    assert {key: os.getenv(key) for key in injected} == {**injected, **changed}
    assert all(value not in response.text for key, value in (injected | changed).items() if key.endswith('_KEY'))


def test_reload_precedence_and_defaults(local_settings, monkeypatch):
    local_settings.write_text('NEBIUS_API_KEY=fixture-file-nebius\nNEBIUS_MODEL=fixture-file-model\n',
                              encoding='utf-8')
    monkeypatch.setenv('NEBIUS_API_KEY', 'fixture-env-nebius')
    monkeypatch.setenv('TAVILY_API_KEY', 'fixture-env-tavily')
    monkeypatch.setenv('NEBIUS_MODEL', '')
    settings.load_saved_settings()
    assert os.getenv('NEBIUS_API_KEY') == 'fixture-env-nebius'
    assert os.getenv('TAVILY_API_KEY') == 'fixture-env-tavily'
    assert NebiusModelProvider().active_model() == 'fixture-file-model'
    monkeypatch.delenv('NEBIUS_MODEL')
    local_settings.unlink()
    settings.load_saved_settings()
    assert NebiusModelProvider().active_model() == settings.DEFAULT_NEBIUS_MODEL


def test_save_failure_leaves_previous_disk_and_runtime_values(local_settings, monkeypatch):
    settings.save_settings(settings.SettingsUpdate(tavily_api_key='fixture-original'))
    old = local_settings.read_bytes()

    def fail_replace(self, dest):
        assert self != dest and self.parent == local_settings.parent
        raise PermissionError('forbidden')
    monkeypatch.setattr(Path, 'replace', fail_replace)
    response = local_client().put('/api/settings', json={'tavily_api_key': 'fixture-replacement'})
    assert response.status_code == 500
    assert 'fixture-replacement' not in response.text
    assert local_settings.read_bytes() == old
    assert os.getenv('TAVILY_API_KEY') == 'fixture-original'
    assert not list(local_settings.parent.glob('*.tmp'))


def test_concurrent_updates_preserve_both_fields(local_settings):
    start = Barrier(2)

    def save(update):
        start.wait(timeout=3)
        return settings.save_settings(settings.SettingsUpdate(**update))
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(save, {'nebius_model': 'fixture-threads-model'})
        b = pool.submit(save, {'tavily_api_key': 'fixture-threads-tavily'})
        assert a.result(timeout=10)['saved']
        assert b.result(timeout=10)['saved']
    assert settings.read_settings() == {
        'NEBIUS_MODEL': 'fixture-threads-model', 'TAVILY_API_KEY': 'fixture-threads-tavily'
    }


def test_settings_request_validation_does_not_echo_invalid_secrets(local_settings):
    sentinel = 'fixture-oversized-sensitive-input'
    client = local_client()
    invalid = client.put('/api/settings', json={'nebius_api_key': sentinel * 20})
    malformed = client.put('/api/settings', content=f'{{"tavily_api_key":"{sentinel}"',
                           headers={'content-type': 'application/json'})
    for response in (invalid, malformed):
        assert response.status_code == 422
        assert sentinel not in response.text


def test_saved_model_matches_health_and_next_provider_request(local_settings, monkeypatch):
    bodies = []
    def open_fixture(request, timeout):
        bodies.append(json.loads(request.data))
        payload = {'choices': [{'message': {'content': json.dumps(PLAN)}}]}
        return BytesIO(json.dumps(payload).encode())

    provider = NebiusModelProvider(api_key='fixture-model-probe', opener=open_fixture)
    monkeypatch.setattr(api, '_model_mode', 'nebius')
    monkeypatch.setattr(api, '_model_provider', provider)
    monkeypatch.setenv('NEBIUS_MODEL', 'fixture-start-model')
    with local_client() as client:
        assert client.get('/api/health').json()['model'] == 'fixture-start-model'
        response = client.put('/api/settings', json={'nebius_model': 'fixture-next-model'})
        assert response.status_code == 200
        assert response.json()['settings']['nebius_model'] == 'fixture-next-model'
        assert client.get('/api/health').json()['model'] == 'fixture-next-model'
        provider.plan('fixture query')
    assert bodies[0]['model'] == 'fixture-next-model'


def test_save_preserves_unmanaged_lines_and_protects_empty_temp_first(local_settings, monkeypatch):
    local_settings.write_text('# keep comment\nUNMANAGED_OPTION=preserve\n', encoding='utf-8')
    restrict = settings._restrict_windows_file
    def before_write(path):
        assert path.read_bytes() == b''
        restrict(path)
    monkeypatch.setattr(settings, '_restrict_windows_file', before_write)
    settings.save_settings(settings.SettingsUpdate(nebius_model='fixture-preserve-model'))
    assert local_settings.read_text(encoding='utf-8').splitlines() == [
        '# keep comment', 'UNMANAGED_OPTION=preserve', 'NEBIUS_MODEL=fixture-preserve-model',
    ]
    assert 'UNMANAGED_OPTION' not in settings.read_settings()


def test_parallel_processes_merge_different_fields(local_settings, tmp_path):
    import subprocess
    import sys
    from time import monotonic, sleep

    code = """
import sys, time
from pathlib import Path
from backend import settings

settings.SETTINGS_PATH = Path(sys.argv[1])
ready, start = Path(sys.argv[2]), Path(sys.argv[3])
original = settings._persist
def slow_persist(path, lines):
    time.sleep(.15)
    original(path, lines)
settings._persist = slow_persist
ready.touch()
deadline = time.monotonic() + 6
while not start.exists():
    if time.monotonic() > deadline:
        raise SystemExit(2)
    time.sleep(.01)
settings.save_settings(settings.SettingsUpdate(**{sys.argv[4]: sys.argv[5]}))
"""
    gate = tmp_path / 'start'
    updates = [('nebius_model', 'fixture-process-model'), ('tavily_api_key', 'fixture-process-search')]
    workers = []
    try:
        ready = [tmp_path / f'ready-{i}' for i in range(2)]
        for marker, (key, value) in zip(ready, updates):
            workers.append(subprocess.Popen(
                [sys.executable, '-c', code, str(local_settings), str(marker), str(gate), key, value],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        deadline = monotonic() + 6
        while not all(marker.exists() for marker in ready):
            assert monotonic() < deadline, 'settings subprocesses did not reach their start gate'
            sleep(.02)
        gate.touch()
        assert all(worker.wait(timeout=15) == 0 for worker in workers)
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
    assert settings.read_settings() == {key.upper(): value for key, value in updates}


def test_sec_save_preserves_provider_authorization(local_settings, monkeypatch):
    from backend.providers.live import TavilySearchProvider

    values = {'NEBIUS_API_KEY': 'fixture-nebius-auth', 'TAVILY_API_KEY': 'fixture-tavily-auth'}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert local_client().put('/api/settings', json={'sec_contact_email': 'sec-only@example.test'}).status_code == 200

    def provider_fixture(expected_key, response):
        def opener(request, timeout):
            assert request.get_header('Authorization') == f'Bearer {values[expected_key]}'
            return BytesIO(json.dumps(response).encode())
        return opener

    model = NebiusModelProvider(opener=provider_fixture('NEBIUS_API_KEY', {
        'choices': [{'message': {'content': json.dumps(PLAN)}}]
    }))
    search = TavilySearchProvider(opener=provider_fixture('TAVILY_API_KEY', {'results': []}))
    assert model.plan('fixture question')['companies'] == PLAN['companies']
    assert search.search('fixture query', 1) == []
