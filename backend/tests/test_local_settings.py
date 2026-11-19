from fastapi.testclient import TestClient

from backend.app import app


def client(host='127.0.0.1'):
    return TestClient(app, base_url='http://127.0.0.1:8000',
                      client=(host, 50000))


def test_settings_only_accept_loopback_and_same_origin():
    assert client().get('/api/settings').status_code == 200
    assert client('192.0.2.10').get('/api/settings').status_code == 403
    assert client().get('/api/settings', headers={'Host': 'example.com'}).status_code == 403
    assert client().put('/api/settings', headers={'Origin': 'https://example.com'},
                        json={'nebius_model': 'test-model'}).status_code == 403
    assert client().put('/api/settings', headers={'Sec-Fetch-Site': 'cross-site'},
                        json={'nebius_model': 'test-model'}).status_code == 403


def test_settings_response_exposes_only_secret_presence():
    response = client().get('/api/settings')
    assert response.status_code == 200
    assert isinstance(response.json()['settings']['nebius_api_key'], bool)
    assert isinstance(response.json()['settings']['tavily_api_key'], bool)
    assert isinstance(response.json()['settings']['sec_contact_email'], bool)
    assert 'max_research_cost_usd' not in response.json()['settings']
    assert client().put('/api/settings',
                        json={'max_research_cost_usd': 1}).status_code == 422


def test_local_save_never_echoes_key(monkeypatch, tmp_path):
    import backend.settings as settings
    monkeypatch.setattr(settings, 'SETTINGS_PATH', tmp_path / 'settings.env')
    monkeypatch.setenv('NEBIUS_API_KEY', '')
    key = 'synthetic-local-test-key'
    response = client().put('/api/settings', json={'nebius_api_key': key})
    assert response.status_code == 200
    assert response.json()['settings']['nebius_api_key'] is True
    assert key not in response.text
    assert key in settings.SETTINGS_PATH.read_text(encoding='utf-8')
