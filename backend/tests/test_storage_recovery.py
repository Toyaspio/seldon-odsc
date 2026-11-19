"""QA fix verification: damaged SQLite rows must not abort startup, must be
logged for diagnosis, and must never be silently deleted or echoed into logs.
"""
from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from backend.storage import store

TASK = {'task_id': 'good-task', 'status': 'researching', 'progress': 40,
        'current_step': 'retrieving', 'created_at': '', 'updated_at': '',
        'report_id': None, 'error_code': None, 'error_message': None}
REPORT = {'report_id': 'good-report', 'task_id': 'good-task', 'status': 'partial'}


def write_row(path, table, key, body):
    connection = sqlite3.connect(path)
    connection.execute(f"INSERT INTO {table} VALUES (?, ?)", (key, body))
    connection.commit()
    connection.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / 'recovery.sqlite3'
    monkeypatch.setattr(store, 'DB_PATH', path)
    return path


def log_messages(caplog):
    return ' '.join(record.getMessage() for record in caplog.records)


def test_corrupt_json_row_is_skipped_logged_and_left_in_place(db, caplog):
    store.save_task(TASK)
    write_row(db, 'tasks', 'broken-row', '{not json')

    with caplog.at_level(logging.WARNING, logger='backend.storage.store'):
        tasks, _reports = store.load_all()

    assert tasks['good-task']['status'] == 'failed'  # interrupted recovery still applied
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT body FROM tasks WHERE id='broken-row'").fetchone()[0] == '{not json'
    messages = log_messages(caplog)
    assert 'broken-row' in messages
    assert '{not json' not in messages  # row content is never echoed into logs


def test_valid_json_non_dict_row_is_skipped_and_logged(db, caplog):
    store.save_task(TASK)
    write_row(db, 'tasks', 'list-row', '[1, 2]')

    with caplog.at_level(logging.WARNING, logger='backend.storage.store'):
        tasks, _reports = store.load_all()

    assert set(tasks) == {'good-task'}
    assert 'list-row' in log_messages(caplog)


def test_id_mismatch_row_is_skipped_and_logged(db, caplog):
    store.save_task(TASK)
    write_row(db, 'tasks', 'row-key', json.dumps({**TASK, 'task_id': 'other-id'}))

    with caplog.at_level(logging.WARNING, logger='backend.storage.store'):
        tasks, _reports = store.load_all()

    assert set(tasks) == {'good-task'}
    assert 'row-key' in log_messages(caplog)


def test_corrupt_report_row_is_skipped_and_kept(db, caplog):
    store.save_task(TASK)
    store.save_report(REPORT)
    write_row(db, 'reports', 'report-bad', 'not-json-at-all')

    with caplog.at_level(logging.WARNING, logger='backend.storage.store'):
        _tasks, reports = store.load_all()

    assert set(reports) == {'good-report'}
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT body FROM reports WHERE id='report-bad'").fetchone() is not None
    assert 'report-bad' in log_messages(caplog)


def test_interrupted_task_delivers_latest_completed_stage(db):
    store.save_task(TASK)
    store.save_news_checkpoint(TASK['task_id'], 'financial_completed', {'report': REPORT})
    later = {**REPORT, 'report_id': 'with-news', 'news_analysis': {'status': 'completed'}}
    store.save_news_checkpoint(TASK['task_id'], 'article_final', {'report': later})
    tasks, reports = store.load_all()
    assert tasks[TASK['task_id']]['report_id'] == 'with-news'
    assert tasks[TASK['task_id']]['status'] == 'partial'
    assert reports['with-news']['news_analysis']['status'] == 'completed'
