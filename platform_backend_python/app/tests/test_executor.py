import io
import uuid
import time
import subprocess
import threading
import os
import signal
import shutil
import tempfile
import docker
import atexit
import tarfile
from flask import Flask, request, jsonify
from flasgger import Swagger
from threading import Thread
from py_eureka_client import eureka_client

import pytest
from unittest.mock import patch, MagicMock
from platform_backend_python.app.development_env import web_platform_executor as executor
from platform_backend_python.app.development_env.web_platform_executor import (
    app, sessions, docker_client, TIMEOUT_SECONDS
)

@pytest.fixture
def client():
    with app.test_client() as client:
        yield client

# === EXECUTE ===

def test_execute_code_success(client):
    mock_container = MagicMock(status="exited")
    mock_container.logs.return_value = b"Execution done."

    with patch.object(docker_client.containers, 'run', return_value=mock_container):
        data = {'file': (io.BytesIO(b"print('Hello')"), 'main.py')}
        response = client.post("/execute", data=data, content_type='multipart/form-data')
        assert response.status_code == 200
        assert "session_id" in response.get_json()

def test_execute_code_failure(client):
    mock_docker_client = MagicMock()
    mock_docker_client.containers.run.side_effect = Exception("Container error")

    with patch("platform_backend_python.app.development_env.web_platform_executor.docker_client", mock_docker_client):
        data = {'file': (io.BytesIO(b"print('Hello')"), 'main.py')}
        response = client.post("/execute", data=data, content_type='multipart/form-data')
        assert response.status_code == 500
        assert "error" in response.get_json()
        assert response.get_json()["error"] == "Container error"

def test_execute_code_no_file(client):
    response = client.post("/execute", data={}, content_type='multipart/form-data')
    assert response.status_code == 400
    assert response.get_json()["error"] == "No file provided"

def test_execute_invalid_file_name(client):
    data = {'file': (io.BytesIO(b"print('bad')"), 'malicious.sh')}
    response = client.post("/execute", data=data, content_type='multipart/form-data')
    assert response.status_code == 400
    assert "must be a .py file" in response.get_json()["error"]

def test_execute_empty_file_name(client):
    data = {'file': (io.BytesIO(b"print()"), '')}
    response = client.post("/execute", data=data, content_type='multipart/form-data')
    assert response.status_code == 400
    assert "No file provided" in response.get_json()["error"]

def test_execute_put_archive_failure(client):
    mock_container = MagicMock()
    executor.prewarmed_pool.clear()
    executor.prewarmed_pool.append(mock_container)

    with patch.object(executor.docker_client.api, 'put_archive', side_effect=Exception("Archive fail")), \
         patch.object(mock_container, 'exec_run') as mock_exec_run:
        data = {'file': (io.BytesIO(b"print('fail')"), 'main.py')}
        response = client.post("/execute", data=data, content_type='multipart/form-data')
        assert response.status_code == 500
        assert "Archive fail" in response.get_json()["error"]
        mock_exec_run.assert_not_called()

def test_execute_exec_run_failure(client):
    mock_container = MagicMock()
    executor.prewarmed_pool.clear()
    executor.prewarmed_pool.append(mock_container)

    with patch.object(executor.docker_client.api, 'put_archive'), \
         patch.object(mock_container, 'exec_run', side_effect=Exception("exec error")):
        data = {'file': (io.BytesIO(b"print('fail')"), 'main.py')}
        response = client.post("/execute", data=data, content_type='multipart/form-data')
        assert response.status_code == 500
        assert "exec error" in response.get_json()["error"]

# === RESULT ===

def test_get_result_success(client):
    sid = str(uuid.uuid4())
    mock_container = MagicMock(status="exited")
    mock_container.logs.return_value = b"Execution logs"

    sessions[sid] = {"container": mock_container, "start_time": time.time()}
    response = client.get(f"/result/{sid}")
    assert response.status_code == 200
    assert "logs" in response.get_json()

def test_get_result_not_found(client):
    response = client.get("/result/nonexistent")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"

def test_get_result_still_running(client):
    sid = str(uuid.uuid4())
    mock_container = MagicMock(status="running")

    sessions[sid] = {"container": mock_container, "start_time": time.time()}
    response = client.get(f"/result/{sid}")
    assert response.status_code == 202
    assert response.get_json()["status"] == "still running"

def test_get_result_exec_error(client):
    sid = str(uuid.uuid4())
    mock_container = MagicMock(status="exited")
    mock_container.logs.side_effect = Exception("log fail")
    sessions[sid] = {"container": mock_container, "start_time": time.time()}

    response = client.get(f"/result/{sid}")
    assert response.status_code == 500
    assert "log fail" in response.get_json()["error"]

# === CLEANUP ===

def test_cleanup_session_success(client):
    sid = str(uuid.uuid4())
    mock_container = MagicMock()
    sessions[sid] = {"container": mock_container, "start_time": time.time()}

    response = client.post(f"/cleanup/{sid}")
    assert response.status_code == 200
    assert response.get_json()["status"] == "cleaned up"

def test_cleanup_session_not_found(client):
    response = client.post("/cleanup/nonexistent")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"

def test_cleanup_expired_session():
    sid = "expired_session"
    mock_container = MagicMock()
    mock_container.stop = MagicMock()
    mock_container.remove = MagicMock()

    sessions[sid] = {"container": mock_container, "start_time": time.time() - 15}
    with patch("platform_backend_python.app.development_env.web_platform_executor.TIMEOUT_SECONDS", 10):
        for k, s in list(sessions.items()):
            if time.time() - s["start_time"] > TIMEOUT_SECONDS:
                try:
                    s["container"].stop()
                    s["container"].remove()
                except Exception:
                    pass
                sessions.pop(k, None)

    assert sid not in sessions
    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()

# === PREWARMING ===

def test_prewarmed_container_used(client):
    mock_container = MagicMock()
    mock_container.id = "mocked_id"
    executor.prewarmed_pool.clear()
    executor.prewarmed_pool.append(mock_container)

    mock_exec_result = MagicMock(exit_code=0, output=b"")

    with patch.object(executor.docker_client.api, 'put_archive') as mock_put_archive, \
         patch.object(mock_container, 'exec_run', return_value=mock_exec_result):
        data = {'file': (io.BytesIO(b"print('Hello')"), 'main.py')}
        response = client.post("/execute", data=data, content_type='multipart/form-data')

        assert response.status_code == 200
        assert len(executor.prewarmed_pool) == 0
        mock_put_archive.assert_called_once()
        mock_container.exec_run.assert_called_once()

def test_create_prewarmed_container_adds_to_pool():
    executor.prewarmed_pool.clear()
    mock_container = MagicMock()
    with patch.object(docker_client.containers, 'run', return_value=mock_container):
        executor.create_prewarmed_container()
        assert len(executor.prewarmed_pool) == 1
        assert executor.prewarmed_pool[0] is mock_container

def test_initialize_prewarmed_pool_fills_pool():
    executor.prewarmed_pool.clear()
    mock_container = MagicMock()
    with patch.object(docker_client.containers, 'run', return_value=mock_container):
        executor.initialize_prewarmed_pool()
        assert len(executor.prewarmed_pool) == executor.PREWARMED_POOL_SIZE

def test_prevent_overfill_pool():
    executor.prewarmed_pool.clear()
    executor.prewarmed_pool.extend([MagicMock()] * executor.PREWARMED_POOL_SIZE)
    with patch.object(docker_client.containers, 'run') as mock_run:
        executor.initialize_prewarmed_pool()
        mock_run.assert_not_called()
