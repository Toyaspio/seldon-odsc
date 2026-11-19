"""Isolate test imports and runs from local settings, providers, and task storage."""
import os
import tempfile
from pathlib import Path

import pytest

_PROVIDER_ENV_KEYS = ('NEBIUS_API_KEY', 'NEBIUS_MODEL', 'TAVILY_API_KEY',
                      'SEC_CONTACT_EMAIL', 'SEC_USER_AGENT')


def pytest_configure(config):
    # Test modules import the app while pytest collects them.
    # A session fixture alone runs after the settings and database have been loaded.
    temporary = tempfile.TemporaryDirectory(prefix='seldon-qa-tests-')
    environment = pytest.MonkeyPatch()
    environment.setenv('SELDON_DATA_DIR', temporary.name)
    environment.setenv('SELDON_SETTINGS_FILE', str(Path(temporary.name) / 'settings.env'))
    environment.setenv('PYTHON_DOTENV_DISABLED', '1')
    for key in ('MODEL_PROVIDER', 'SEARCH_PROVIDER', 'FINANCIAL_DATA_PROVIDER'):
        environment.setenv(key, 'mock')
    for key in _PROVIDER_ENV_KEYS:
        environment.delenv(key, raising=False)
    config.add_cleanup(temporary.cleanup)
    config.add_cleanup(environment.undo)


@pytest.fixture(scope='session', autouse=True)
def _isolated_data_environment():
    return {key: os.environ.get(key) for key in _PROVIDER_ENV_KEYS}


@pytest.fixture(autouse=True)
def _restore_provider_environment(_isolated_data_environment):
    yield
    for key, value in _isolated_data_environment.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
