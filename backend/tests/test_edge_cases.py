from datetime import date
from decimal import Decimal
from fastapi.testclient import TestClient
import pytest
from backend.app import app
from backend.financial.validator import percentage, validate_period, FinancialValidationError
from backend.agent.models import Claim, Source
from backend.evidence.verifier import verify_claim


def _terminal(client, task_id):
    import time
    for _ in range(100):
        task = client.get(f'/api/research/{task_id}').json()
        if task['status'] in ('partial', 'completed', 'failed'):
            return task
        time.sleep(.01)
    raise AssertionError('Task did not terminate')


def test_chinese_alias_and_unresolved_company():
    client = TestClient(app)
    task = client.post('/api/research', json={'query': '研究英伟达 AI 资本支出'}).json()
    done = _terminal(client, task['task_id'])
    assert done['status'] == 'partial'
    report = client.get(f"/api/reports/{done['report_id']}").json()
    assert report['companies'][0]['company_name'] == 'NVIDIA'
    task = client.post('/api/research', json={'query': '研究一家未知公司'}).json()
    done = _terminal(client, task['task_id'])
    assert done['status'] == 'failed'
    assert done['error_code'] == 'COMPANY_UNRESOLVED'


def test_evidence_is_not_verified_by_url_or_quote_alone():
    source = Source(source_id='s', title='Test', publisher='Test', url='https://example.invalid',
                    source_type='mock', quoted_text='Test quote')
    claim = Claim(claim_id='c', text='Unverified', verification_status='supported', evidence_ids=['s'])
    assert verify_claim(claim, [source]).verification_status == 'insufficient'


def test_matching_structured_evidence_and_contradiction():
    source = Source(source_id='s', title='Filing', publisher='Issuer',
        url='https://example.com/filing', publication_date='2025-04-01',
        source_type='filing', quoted_text='Revenue was 10 million dollars.',
        company_id='example', metric_name='revenue', numeric_value=10,
        unit='USD millions', period_end='2025-03-31')
    claim = Claim(claim_id='c', text='Revenue was 10 million dollars.',
        verification_status='insufficient', evidence_ids=['s'],
        company_id='example', metric_name='revenue', numeric_value=10,
        unit='USD millions', period_end='2025-03-31')
    assert verify_claim(claim, [source]).verification_status == 'supported'
    assert verify_claim(claim.model_copy(update={'numeric_value': 11}), [source]).verification_status == 'contradicted'


def test_financial_error_cases():
    with pytest.raises(FinancialValidationError):
        percentage(Decimal(1), Decimal(0))
    with pytest.raises(FinancialValidationError):
        validate_period(date(2025, 3, 31), date(2025, 1, 1), 'quarter')
