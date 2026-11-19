from fastapi.testclient import TestClient
from backend.app import app


def test_mock_flow():
    client = TestClient(app)
    assert client.get('/api/health').json()['mode'] == 'mock'
    assert len(client.get('/api/companies').json()['companies']) == 4
    task_id = client.post('/api/research', json={'query': 'Research NVIDIA AI investment'}).json()['task_id']
    import time
    for _ in range(100):
        task = client.get(f'/api/research/{task_id}').json()
        if task['status'] in ('partial', 'failed'):
            break
        time.sleep(.01)
    assert task['status'] == 'partial'
    report = client.get(f"/api/reports/{task['report_id']}").json()
    assert report['data_mode'] == 'mock'
    assert report['companies'][0]['company_name'] == 'NVIDIA'
    assert report['metrics'] == []


def test_invalid_input():
    client = TestClient(app)
    assert client.post('/api/research', json={'query': ''}).status_code == 422
    assert client.get('/api/research/missing').status_code == 404
