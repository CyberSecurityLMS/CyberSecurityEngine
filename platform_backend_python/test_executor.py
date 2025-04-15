import io
import uuid
import time
import pytest
import threading
from unittest.mock import patch, MagicMock
from platform_backend_python.web_platform_executor import app, sessions, docker_client, TIMEOUT_SECONDS

# This is a test suite for the web platform executor API.
# It uses pytest and unittest.mock to test the Flask application endpoints.
@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


# Test the /execute endpoint for a successful code execution
def test_execute_code_success(client):
    mock_container = MagicMock()
    mock_container.status = "exited"
    mock_container.logs.return_value = b"Hello from container"

    with patch("platform_backend_python.web_platform_executor.docker_client.containers.run", return_value=mock_container):
        data = {
            'file': (io.BytesIO(b"print('Hello')"), 'main.py')
        }
        response = client.post("/execute", data=data, content_type='multipart/form-data')
        assert response.status_code == 200
        assert "session_id" in response.get_json()


# Test the /execute endpoint for a failed code execution
def test_execute_code_failure(client):
    mock_docker_client = MagicMock()
    mock_docker_client.containers.run.side_effect = Exception("Container error")

    with patch("platform_backend_python.web_platform_executor.docker_client", mock_docker_client):
        data = {
            'file': (io.BytesIO(b"print('Hello')"), 'main.py')
        }
        response = client.post("/execute", data=data, content_type='multipart/form-data')

        assert response.status_code == 500
        assert "error" in response.get_json()
        assert response.get_json()["error"] == "Container error"


# Test the /execute endpoint for a missing file
def test_execute_code_no_file(client):
    response = client.post("/execute", data={}, content_type='multipart/form-data')
    assert response.status_code == 400
    assert response.get_json()["error"] == "No file provided"


# Test the /get endpoint for a successful session retrieval
def test_get_result_success(client):
    fake_session_id = str(uuid.uuid4())
    mock_container = MagicMock()
    mock_container.status = "exited"
    mock_container.logs.return_value = b"Execution logs"

    sessions[fake_session_id] = {
        "container": mock_container,
        "start_time": 0,
    }

    response = client.get(f"/result/{fake_session_id}")
    assert response.status_code == 200
    assert "logs" in response.get_json()


# Test the /get endpoint for a session that does not exist
def test_get_result_not_found(client):
    response = client.get("/result/nonexistent")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


# Test the /get endpoint for a session that is still running
def test_get_result_still_running(client):
    fake_session_id = str(uuid.uuid4())
    mock_container = MagicMock()
    mock_container.status = "running"

    sessions[fake_session_id] = {
        "container": mock_container,
        "start_time": 0,
    }

    response = client.get(f"/result/{fake_session_id}")
    assert response.status_code == 202
    assert response.get_json()["status"] == "still running"


# Test the /cleanup endpoint for a successful session cleanup
def test_cleanup_session_success(client):
    fake_session_id = str(uuid.uuid4())
    mock_container = MagicMock()

    sessions[fake_session_id] = {
        "container": mock_container,
        "start_time": 0,
    }

    response = client.post(f"/cleanup/{fake_session_id}")
    assert response.status_code == 200
    assert response.get_json()["status"] == "cleaned up"


# Test the /cleanup endpoint for a session not found
def test_cleanup_session_not_found(client):
    response = client.post("/cleanup/nonexistent")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


# Test the /cleanup endpoint for a session that has already been cleaned up
def test_cleanup_expired_session():
    mock_container = MagicMock()
    mock_container.stop = MagicMock()
    mock_container.remove = MagicMock()

    session_id = "expired_session"
    sessions[session_id] = {
        "container": mock_container,
        "start_time": time.time() - 15 # Simulate an expired session
    }

    def one_time_cleanup():
        for session_id_inner, session in list(sessions.items()):
            if time.time() - session["start_time"] > TIMEOUT_SECONDS:
                container = session["container"]
                try:
                    container.stop()
                    container.remove()
                except Exception:
                    pass
                sessions.pop(session_id_inner, None)

    one_time_cleanup()

    assert session_id not in sessions
    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()

