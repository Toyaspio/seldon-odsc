"""Settings for a single-user localhost application."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from tempfile import mkstemp
from threading import RLock
from urllib.parse import urlsplit
from typing import Literal

from dotenv import load_dotenv
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

_settings_lock = RLock()
DEFAULT_NEBIUS_MODEL = 'zai-org/GLM-5.3-Flash'

DEFAULT_SETTINGS_PATH = (Path(os.environ.get('LOCALAPPDATA') or Path.home())
                         / 'SeldonSystem' / 'settings.env')
FIELDS = ('NEBIUS_API_KEY', 'NEBIUS_MODEL', 'TAVILY_API_KEY',
          'SEC_CONTACT_EMAIL')
# Deletion must be an explicit confirmed action; an empty input value means "keep".
CLEARABLE = ('NEBIUS_API_KEY', 'TAVILY_API_KEY', 'SEC_CONTACT_EMAIL')
StartupSource = Literal['environment', 'dotenv']
EffectiveSource = Literal['environment', 'dotenv', 'local_settings', 'runtime']
_startup_sources: dict[str, StartupSource] = {}
_applied_local_keys: set[str] = set()


def _resolve_settings_path() -> Path:
    """Resolve lazily so SELDON_SETTINGS_FILE set after import applies."""
    configured = os.environ.get('SELDON_SETTINGS_FILE')
    if configured:
        return Path(configured)
    return DEFAULT_SETTINGS_PATH


class _SettingsPathProxy:
    """Path-like proxy so monkeypatching ``settings.SETTINGS_PATH`` keeps working."""

    def __fspath__(self):
        return str(_resolve_settings_path())

    def __getattr__(self, name):
        return getattr(_resolve_settings_path(), name)

    def __eq__(self, other):
        return _resolve_settings_path() == other

    def __hash__(self):
        return hash(_resolve_settings_path())

    def __truediv__(self, other):
        return _resolve_settings_path() / other

    def __str__(self):
        return str(_resolve_settings_path())


SETTINGS_PATH = _SettingsPathProxy()

class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    nebius_api_key: str | None = Field(default=None, max_length=512)
    nebius_model: str | None = Field(default=None, max_length=200)
    tavily_api_key: str | None = Field(default=None, max_length=512)
    sec_contact_email: str | None = Field(default=None, max_length=320)
    clear_nebius_api_key: bool = False
    clear_tavily_api_key: bool = False
    clear_sec_contact_email: bool = False

class SettingsStatus(BaseModel):
    settings: dict[str, bool | str]
    saved_keys: dict[str, bool]
    environment_keys: dict[str, bool]
    effective_sources: dict[str, EffectiveSource | None]
    startup_overrides: dict[str, StartupSource | None]
    saved: bool


class ConnectionDiagnostics(BaseModel):
    transport: dict[str, str]
    checks: dict[str, Literal['not_configured', 'reachable', 'unavailable']]
    provider_authentication: Literal['untested']
    research_ready: Literal[False]
    paid_request_made: Literal[False]


def require_local_request(request: Request) -> None:
    """Reject remote clients, rebinding hosts and cross-origin browser writes."""
    loopback = {'127.0.0.1', '::1', 'localhost'}
    host = request.headers.get('host', '')
    if (not request.client or request.client.host not in loopback
            or request.url.hostname not in loopback):
        raise HTTPException(403, 'Settings are available on localhost only')
    origin = request.headers.get('origin')
    if origin and (urlsplit(origin).scheme not in {'http', 'https'}
            or urlsplit(origin).netloc.lower() != host.lower()):
        raise HTTPException(403, 'Cross-origin settings request denied')
    if request.headers.get('sec-fetch-site', 'same-origin') not in {'same-origin', 'none'}:
        raise HTTPException(403, 'Cross-site settings request denied')


def _settings_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return []


def _parse_settings(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        key, sep, value = line.partition('=')
        if sep and key in FIELDS:
            result[key] = value
    return result


def read_settings() -> dict[str, str]:
    return _parse_settings(_settings_lines(Path(SETTINGS_PATH)))


def _clear_actions(update: SettingsUpdate) -> dict[str, bool]:
    return {
        'NEBIUS_API_KEY': update.clear_nebius_api_key,
        'TAVILY_API_KEY': update.clear_tavily_api_key,
        'SEC_CONTACT_EMAIL': update.clear_sec_contact_email,
    }


def runtime_values(*keys) -> tuple[str | None, ...]:
    with _settings_lock:
        return tuple(os.getenv(key) for key in keys)


def status() -> dict:
    with _settings_lock:
        path = Path(SETTINGS_PATH)
        saved = _parse_settings(_settings_lines(path))
        return _status(path, saved)


def _status(path: Path, saved: dict[str, str]) -> dict:
    configured = {key.lower(): bool(os.getenv(key)) for key in CLEARABLE}
    configured['nebius_model'] = os.getenv('NEBIUS_MODEL') or DEFAULT_NEBIUS_MODEL
    effective_sources = {}
    for key in CLEARABLE:
        current = os.getenv(key)
        if not current:
            source = None
        elif key in _applied_local_keys:
            source = 'local_settings' if current == saved.get(key) else 'runtime'
        else:
            source = _startup_sources.get(key, 'environment')
        effective_sources[key.lower()] = source
    return {'settings': configured,
            'effective_sources': effective_sources,
            'startup_overrides': {key.lower(): _startup_sources.get(key) for key in CLEARABLE},
            'saved_keys': {key.lower(): bool(saved.get(key)) for key in CLEARABLE},
            'environment_keys': {key.lower(): bool(os.getenv(key)) and saved.get(key) != os.getenv(key)
                                 for key in CLEARABLE},
            'saved': path.is_file()}


@contextmanager
def _file_lock(path: Path):
    # Keep the lock inode stable across saves. It never contains settings data.
    fd = os.open(path.with_name(path.name + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _restrict_windows_file(path: Path) -> None:
    if os.name == 'nt':
        import subprocess
        try:
            user = subprocess.check_output(['whoami'], text=True, timeout=10).strip()
            subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'{user}:F'],
                           check=True, capture_output=True, timeout=10)
        except subprocess.SubprocessError:
            raise OSError('Could not protect settings file') from None


def _persist(path: Path, lines: list[str]) -> None:
    if not lines:
        path.unlink(missing_ok=True)
        return
    fd, name = mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            _restrict_windows_file(temp)
            handle.write('\n'.join(lines) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _changes(update: SettingsUpdate) -> dict[str, str | None]:
    changes = {}
    clears = _clear_actions(update)
    for key in FIELDS:
        value = getattr(update, key.lower())
        if clears.get(key):
            if value not in (None, ''):
                raise HTTPException(422, f'Cannot set and clear {key.lower()} in one request')
            changes[key] = None
        elif value not in (None, ''):
            if any(char in value for char in ('\n', '\r', '\x00', '=')):
                raise HTTPException(422, 'Invalid setting value')
            if key == 'SEC_CONTACT_EMAIL' and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value):
                raise HTTPException(422, 'Invalid SEC contact email')
            changes[key] = value
    return changes


def save_settings(update: SettingsUpdate) -> dict:
    changes = _changes(update)
    if not changes:
        return status()
    path = Path(SETTINGS_PATH)
    with _settings_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(path):
                lines = [line for line in _settings_lines(path)
                         if line.partition('=')[0] not in changes]
                lines.extend(f'{key}={value}' for key, value in changes.items() if value is not None)
                saved = _parse_settings(lines)
                _persist(path, lines)
                # Apply only the committed changes, including explicit clears.
                for key, value in changes.items():
                    if value is None:
                        os.environ.pop(key, None)
                        _applied_local_keys.discard(key)
                    else:
                        os.environ[key] = value
                        _applied_local_keys.add(key)
                if 'SEC_CONTACT_EMAIL' in changes:
                    contact = changes['SEC_CONTACT_EMAIL']
                    if contact:
                        os.environ['SEC_USER_AGENT'] = f'Seldon Research Agent/0.1 contact {contact}'
                    else:
                        os.environ.pop('SEC_USER_AGENT', None)
                return _status(path, saved)
        except (OSError, UnicodeError):
            raise HTTPException(500, 'Could not save local settings') from None


def load_startup_settings() -> None:
    inherited = {key for key in CLEARABLE if os.getenv(key)}
    load_dotenv()
    with _settings_lock:
        _startup_sources.clear()
        _startup_sources.update({key: 'environment' if key in inherited else 'dotenv'
                                 for key in CLEARABLE if os.getenv(key)})
        _applied_local_keys.clear()
        load_saved_settings()


def load_saved_settings() -> None:
    with _settings_lock:
        for key, value in read_settings().items():
            if value and not os.getenv(key):
                os.environ[key] = value
                _applied_local_keys.add(key)
        contact = os.getenv('SEC_CONTACT_EMAIL')
        if contact:
            os.environ.setdefault('SEC_USER_AGENT', f'Seldon Research Agent/0.1 contact {contact}')


def connection_diagnostics() -> dict:
    """Check TLS transport without sending credentials or making paid requests."""
    import socket
    import ssl
    result = {}
    nebius, tavily = runtime_values('NEBIUS_API_KEY', 'TAVILY_API_KEY')
    configured = {'nebius': bool(nebius), 'tavily': bool(tavily)}
    for name, host in {'nebius': 'api.tokenfactory.nebius.com', 'tavily': 'api.tavily.com', 'sec': 'data.sec.gov'}.items():
        try:
            with socket.create_connection((host, 443), timeout=4) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                    result[name] = 'reachable'
        except OSError:
            result[name] = 'unavailable'
    checks = {name: result[name] if present else 'not_configured'
              for name, present in configured.items()}
    return {'transport': result, 'checks': checks, 'provider_authentication': 'untested', 'research_ready': False,
            'paid_request_made': False}
