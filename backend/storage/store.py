import json
import logging
import os
import sqlite3
from tempfile import NamedTemporaryFile
from uuid import UUID
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_DIR = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'SeldonSystem'


def _resolve_db_path() -> Path:
    """Resolve the database lazily so SELDON_DATA_DIR set after import applies.

    SELDON_DATA_DIR points at the directory that holds SeldonSystem data
    (same convention as the default user-data location). Test sessions point
    this at an isolated temp directory before any request.
    """
    data_dir = os.environ.get('SELDON_DATA_DIR')
    if data_dir:
        return Path(data_dir) / 'tasks.sqlite3'
    return DEFAULT_DB_DIR / 'tasks.sqlite3'


class _DbPathProxy:
    """Path-like proxy so monkeypatching ``store.DB_PATH`` keeps working."""

    def __fspath__(self):
        return str(_resolve_db_path())

    def __getattr__(self, name):
        return getattr(_resolve_db_path(), name)

    def __eq__(self, other):
        return _resolve_db_path() == other

    def __hash__(self):
        return hash(_resolve_db_path())

    def __truediv__(self, other):
        return _resolve_db_path() / other

    def __str__(self):
        return str(_resolve_db_path())


DB_PATH = _DbPathProxy()


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    connection.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def save_task(task: dict) -> None:
    with _connect() as db:
        db.execute('INSERT OR REPLACE INTO tasks VALUES (?, ?)',
                   (task['task_id'], json.dumps(task, ensure_ascii=False)))


def save_report(report: dict) -> None:
    with _connect() as db:
        db.execute('INSERT OR REPLACE INTO reports VALUES (?, ?)',
                   (report['report_id'], json.dumps(report, ensure_ascii=False)))


def save_completion(task: dict, report: dict) -> None:
    with _connect() as db:
        db.execute('INSERT OR REPLACE INTO reports VALUES (?, ?)',
                   (report['report_id'], json.dumps(report, ensure_ascii=False)))
        db.execute('INSERT OR REPLACE INTO tasks VALUES (?, ?)',
                   (task['task_id'], json.dumps(task, ensure_ascii=False)))


class ReviewCheckpointConflict(RuntimeError):
    pass


def save_review_checkpoint(task_id: str, report: dict, job: dict) -> None:
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS investment_drafts (id TEXT PRIMARY KEY, report TEXT NOT NULL, draft TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS investment_reviews (id TEXT PRIMARY KEY, body TEXT NOT NULL, done INTEGER NOT NULL)')
        immutable = {key: job[key] for key in ('draft', 'version', 'draft_hash', 'generated_at', 'model')}
        db.execute('INSERT OR IGNORE INTO investment_drafts VALUES (?, ?, ?)',
                   (task_id, json.dumps(report, ensure_ascii=False), json.dumps(immutable, ensure_ascii=False)))
        stored, = db.execute('SELECT draft FROM investment_drafts WHERE id=?', (task_id,)).fetchone()
        if json.loads(stored)['draft_hash'] != job['draft_hash']:
            raise ValueError('Investment draft ID collision')
        existing = db.execute('SELECT body, done FROM investment_reviews WHERE id=?', (task_id,)).fetchone()
        version = job.get('_revision', 0)
        disk_version = json.loads(existing[0]).get('_revision', 0) if existing else 0
        if disk_version != version or (existing and existing[1]):
            raise ReviewCheckpointConflict('Review checkpoint changed')
        state = {key: value for key, value in job.items() if key not in immutable}
        state['_revision'] = version + 1
        db.execute('INSERT INTO investment_reviews VALUES (?, ?, 0) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                   (task_id, json.dumps(state, ensure_ascii=False)))
    job['_revision'] = version + 1


def load_pending_reviews():
    with _connect() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='investment_reviews'").fetchone()
        if not exists:
            return []
        pending = []
        for task_id, report, draft, state in db.execute(
                'SELECT r.id, d.report, d.draft, r.body FROM investment_reviews r '
                'JOIN investment_drafts d ON r.id=d.id WHERE done=0'):
            try:
                pending.append((task_id, json.loads(report), json.loads(draft) | json.loads(state)))
            except (ValueError, TypeError):
                logger.warning('Skipped damaged investment review checkpoint')
        return pending


def load_review_for_report(task_id):
    try:
        with _connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='investment_reviews'").fetchone():
                return None
            row = db.execute('SELECT d.report, d.draft, r.body FROM investment_drafts d '
                             'JOIN investment_reviews r ON d.id=r.id WHERE d.id=?', (task_id,)).fetchone()
        if row:
            report, draft, review = map(json.loads, row)
            return report, draft | review
    except (sqlite3.Error, ValueError, TypeError):
        logger.warning('Saved investment review could not be read')
    return None


def finish_review(task_id: str) -> None:
    with _connect() as db:
        db.execute('UPDATE investment_reviews SET done=1 WHERE id=?', (task_id,))


def save_news_checkpoint(task_id: str, stage: str, snapshot: dict) -> None:
    with _connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS news_checkpoints (task_id TEXT, stage TEXT, body TEXT NOT NULL, '
                   'PRIMARY KEY (task_id, stage))')
        db.execute('INSERT OR IGNORE INTO news_checkpoints VALUES (?, ?, ?)',
                   (task_id, stage, json.dumps(snapshot, ensure_ascii=False)))


def stage_completion(task: dict, report: dict) -> None:
    """Preserve a finished task when SQLite cannot commit it."""
    directory = DB_PATH.parent / 'pending-completions'
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / f"{UUID(task['task_id'])}.json"
    pending = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, delete=False) as file:
            pending = Path(file.name)
            json.dump({'task': task, 'report': report}, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        pending.replace(final)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _recover_completions() -> None:
    directory = DB_PATH.parent / 'pending-completions'
    for path in directory.glob('*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            task, report = record['task'], record['report']
            if (str(UUID(task['task_id'])) != path.stem or report['task_id'] != task['task_id']
                    or report['report_id'] != task['report_id']
                    or task['status'] != report['status'] or task['status'] not in {'partial', 'completed'}):
                raise ValueError('Invalid completion record')
            save_completion(task, report)
            path.unlink()
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            logger.warning('Could not recover a staged completion; it was left in place')


def _decode_row(table: str, key: str, body: str) -> dict | None:
    """Decode one stored row; log the row id (never its content) on damage.

    Damaged rows stay in the database untouched — a single bad record must not
    abort startup or silently disappear.
    """
    try:
        record = json.loads(body)
    except ValueError:
        logger.warning('Skipped damaged %s row id=%s (invalid JSON; left in place for diagnosis)',
                       table, key)
        return None
    if not isinstance(record, dict) or record.get(f'{table[:-1]}_id') != key:
        logger.warning('Skipped damaged %s row id=%s (missing or mismatched id; left in place)',
                       table, key)
        return None
    return record


def load_all() -> tuple[dict, dict]:
    _recover_completions()
    with _connect() as db:
        tasks = {}
        for key, body in db.execute('SELECT id, body FROM tasks'):
            record = _decode_row('tasks', key, body)
            if record is not None:
                tasks[key] = record
        reports = {}
        for key, body in db.execute('SELECT id, body FROM reports'):
            record = _decode_row('reports', key, body)
            if record is not None:
                reports[key] = record
        checkpoints = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='news_checkpoints'").fetchone():
            for task_id, body in db.execute('SELECT task_id, body FROM news_checkpoints ORDER BY rowid'):
                try:
                    if report := json.loads(body).get('report'):
                        checkpoints[task_id] = report
                except (ValueError, TypeError, AttributeError):
                    continue
    for task in tasks.values():
        if task['status'] in ('pending', 'researching', 'verifying'):
            if report := checkpoints.get(task['task_id']):
                report['task_id'] = task['task_id']
                task.update(status='partial', progress=100, current_step='done',
                            report_id=report['report_id'], updated_at=datetime.now(timezone.utc).isoformat())
                save_completion(task, report)
                reports[report['report_id']] = report
                continue
            task.update(status='failed', error_code='PROCESS_INTERRUPTED',
                        error_message='Research process ended before completion',
                        updated_at=datetime.now(timezone.utc).isoformat())
            save_task(task)
    return tasks, reports
