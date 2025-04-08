import io
import uuid
import pytest
from unittest.mock import patch, MagicMock
from platform_backend_python.web_platform_executor import app, sessions


@pytest.fixture
def client():
    with app.test_client() as client:
        yield client


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


def test_execute_code_no_file(client):
    response = client.post("/execute", data={}, content_type='multipart/form-data')
    assert response.status_code == 400
    assert response.get_json()["error"] == "No file provided"


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


def test_get_result_not_found(client):
    response = client.get("/result/nonexistent")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"


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


def test_cleanup_session_not_found(client):
    response = client.post("/cleanup/nonexistent")
    assert response.status_code == 404
    assert response.get_json()["error"] == "Session not found"
