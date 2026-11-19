from backend.storage import store


def test_restart_marks_interrupted_task_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', tmp_path / 'tasks.sqlite3')
    store.save_task({'task_id': 't', 'status': 'researching', 'error_code': None})
    tasks, reports = store.load_all()
    assert reports == {}
    assert tasks['t']['status'] == 'failed'
    assert tasks['t']['error_code'] == 'PROCESS_INTERRUPTED'
