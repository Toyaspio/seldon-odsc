import json
from pathlib import Path
from jsonschema import validate
from fastapi.testclient import TestClient
from backend.app import app

ROOT = Path(__file__).resolve().parents[2]


def test_generated_contracts_and_example():
    schema = json.loads((ROOT / 'contracts/report.schema.json').read_text(encoding='utf-8'))
    example = json.loads((ROOT / 'contracts/examples/research-report.mock.json').read_text(encoding='utf-8'))
    validate(example, schema)
    openapi = json.loads((ROOT / 'contracts/openapi.yaml').read_text(encoding='utf-8'))
    assert set(['/api/health', '/api/companies', '/api/research',
                '/api/research/{task_id}', '/api/reports/{report_id}']).issubset(openapi['paths'])
    assert openapi['paths']['/api/research']['post']['responses']['202']


def test_arbitrary_company_and_bad_date():
    client = TestClient(app)
    result = client.post('/api/research', json={'query': 'Research private company', 'companies': ['Example Corp']})
    assert result.status_code == 202
    assert client.post('/api/research', json={'query': 'Research Microsoft',
        'as_of_date': 'not-a-date'}).status_code == 422


def test_runtime_data_is_outside_project_tree():
    from backend.storage.store import DB_PATH
    assert ROOT not in DB_PATH.parents
