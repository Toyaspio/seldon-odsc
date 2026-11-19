import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_KEYS = ('MODEL_PROVIDER', 'SEARCH_PROVIDER', 'FINANCIAL_DATA_PROVIDER')
LIVE = ('NebiusModelProvider', 'TavilySearchProvider', 'SecFinancialDataProvider')
MOCK = ('MockModelProvider', 'MockSearchProvider', 'MockFinancialDataProvider')
INSPECT_ADAPTERS = """
import json
import logging
logging.basicConfig(level=logging.INFO)
from backend.app import _orchestrator, health
print(json.dumps({
    'adapters': [type(provider).__name__ for provider in (
        _orchestrator.planner.model, _orchestrator.router.search, _orchestrator.router.financial,
    )],
    'mode': health()['mode'],
}))
"""


@pytest.mark.parametrize(('overrides', 'dotenv', 'expected', 'mode', 'banner'), [
    ({}, {}, LIVE, 'live-unverified', 'LIVE configured'),
    (dict.fromkeys(PROVIDER_KEYS, 'mock'), {}, MOCK, 'mock', 'Mock'),
    ({}, dict.fromkeys(PROVIDER_KEYS, 'mock'), MOCK, 'mock', 'Mock'),
    ({'MODEL_PROVIDER': 'nebius'}, {'MODEL_PROVIDER': 'mock', 'SEARCH_PROVIDER': 'mock'},
     (LIVE[0], MOCK[1], LIVE[2]), 'live-unverified', 'Mixed'),
])
def test_provider_initialization_on_startup(tmp_path, overrides, dotenv, expected, mode, banner):
    sensitive_fixture = 'fixture-not-for-startup-output'
    if dotenv:
        (tmp_path / '.env').write_text(
            ''.join(f'{key}={value}\n' for key, value in dotenv.items()), encoding='utf-8')
    environment = os.environ.copy()
    for key in (*PROVIDER_KEYS, 'NEBIUS_API_KEY', 'TAVILY_API_KEY', 'SEC_CONTACT_EMAIL',
                'SEC_USER_AGENT', 'PYTHON_DOTENV_DISABLED', 'SELDON_OFFICIAL_ISSUER_CATALOG'):
        environment.pop(key, None)
    environment.update(
        overrides,
        PYTHONPATH=str(PROJECT_ROOT),
        NEBIUS_API_KEY=sensitive_fixture,
        SELDON_DATA_DIR=str(tmp_path),
        SELDON_SETTINGS_FILE=str(tmp_path / 'settings.env'),
    )
    child = subprocess.run([sys.executable, '-c', INSPECT_ADAPTERS], cwd=tmp_path, env=environment,
                           capture_output=True, text=True, encoding='utf-8', timeout=30)
    if sensitive_fixture in child.stdout or sensitive_fixture in child.stderr:
        pytest.fail('Startup exposed a credential in its output')
    assert child.returncode == 0, child.stderr
    actual = json.loads(child.stdout)
    assert tuple(actual['adapters']) == expected
    assert actual['mode'] == mode
    assert f'Research providers ({banner}):' in child.stderr
    assert all(adapter in child.stderr for adapter in expected)
