import os
import time
import uuid
import shutil
import tempfile
import docker
import signal
import atexit
import tarfile
import io
from flask import Flask, request, jsonify
from flasgger import Swagger
from threading import Thread
from py_eureka_client import eureka_client

EUREKA_URL = os.environ.get("EUREKA_URL", "http://discovery-server:8761/eureka")
APP_NAME = os.environ.get("APP_NAME", "executor-svc")

eureka_client.init(
    eureka_server=EUREKA_URL,
    app_name=APP_NAME,
    instance_port=5000
)
print(f"Registered in Eureka as {APP_NAME}")

app = Flask(__name__)
swagger = Swagger(app, template_file="swagger.yml")

# Initialize the Docker client
docker_client = docker.from_env()

# Session management
sessions = {}

# Container settings
CONTAINER_IMAGE = "python:3.13-slim" # Base image for running the code
RESOURCE_LIMITS = {
    "cpu_quota": 50000,             # Limit CPU usage
    "cpu_period": 100000,           # Limit CPU usage
    "mem_limit": "128m",            # Limit memory usage
}
TIMEOUT_SECONDS = 10

prewarmed_pool = []
PREWARMED_POOL_SIZE = 1


def _make_tar(file_path):
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        arcname = os.path.basename(file_path)
        tar.add(file_path, arcname=arcname) 
    tar_stream.seek(0)
    return tar_stream


def create_prewarmed_container():
    try:
        container = docker_client.containers.run(
            CONTAINER_IMAGE,
            command=["sleep", "3600"],
            detach=True,
            auto_remove=False,
            network_disabled=True,
            tty=True,
            **RESOURCE_LIMITS,
        )
        prewarmed_pool.append(container)
        print("[Prewarm] Container created")
        return True
    except Exception as e:
        print(f"[Prewarm] Failed to create container: {e}")
        return False

@app.route("/prewarm", methods=["POST"])
def prewarm_container():
    if len(prewarmed_pool) >= PREWARMED_POOL_SIZE:
        return jsonify({"status": "Prewarm pool already at maximum size"}), 200
    
    success = create_prewarmed_container()
    if success:
        return jsonify({"status": "Container prewarmed successfully"}), 200
    else:
        return jsonify({"error": "Failed to prewarm container"}), 500


@app.route("/execute_pytest", methods=["POST"])
def execute_pytest():
    if 'files' not in request.files:
        return jsonify({"error": "No files provided"}), 400

    files = request.files.getlist('files')
    session_id = str(uuid.uuid4())
    session_dir = f"/tmp/{session_id}"
    os.makedirs(session_dir, exist_ok=True)

    # Save all uploaded files
    test_files = []
    for file in files:
        file_path = os.path.join(session_dir, file.filename)
        file.save(file_path)
        if file.filename.endswith('_test.py') or file.filename.startswith('test_'):
            test_files.append(os.path.basename(file_path))

    if not test_files:
        return jsonify({"error": "No test files found (should end with _test.py or start with test_)"}), 400

    try:
        # Install pytest in the container
        def prepare_container(container):
            # Install pytest and any other dependencies
            container.exec_run("pip install pytest", workdir="/code", tty=True)
            
            # For prewarmed containers, we need to copy files
            if container in prewarmed_pool:
                tar_data = _make_tar(session_dir)
                docker_client.api.put_archive(container.id, "/code", tar_data)

        # Use prewarmed container if available
        if prewarmed_pool:
            container = prewarmed_pool.pop()
            prepare_container(container)
            exec_result = container.exec_run(
                f"pytest {' '.join(test_files)} --json-report --no-header -v",
                workdir="/code"
            )
        else:
            container = docker_client.containers.run(
                CONTAINER_IMAGE,
                command=f"sh -c 'pip install pytest && pytest {' '.join(test_files)} --json-report --no-header -v'",
                detach=False,
                auto_remove=False,
                network_disabled=True,
                volumes={
                    session_dir: {
                        "bind": "/code",
                        "mode": "ro",
                    }
                },
                working_dir="/code",
                **RESOURCE_LIMITS,
            )
            exec_result = container

        exit_code = exec_result.exit_code
        output = exec_result.output.decode('utf-8') if hasattr(exec_result, 'output') else ""

        # Parse pytest results
        try:
            # Look for JSON report in output (if using --json-report)
            report_start = output.find('{"report":')
            pytest_report = json.loads(output[report_start:]) if report_start != -1 else None
        except json.JSONDecodeError:
            pytest_report = None

        if exit_code == 0:
            status = "success"
            status_code = 200
        elif exit_code == 1:
            status = "partial_success"
            status_code = 206
        else:
            status = "failure"
            status_code = 400

        # Try to get summary stats if JSON report is available
        summary = None
        if pytest_report and 'report' in pytest_report:
            summary = {
                'passed': pytest_report['report'].get('passed', 0),
                'failed': pytest_report['report'].get('failed', 0),
                'total': pytest_report['report'].get('total', 0),
                'duration': pytest_report['report'].get('duration', 0)
            }

        # Clean up container if we didn't use prewarmed one
        if not prewarmed_pool or container not in prewarmed_pool:
            try:
                container.stop()
                container.remove()
            except Exception as e:
                print(f"Error cleaning up container: {e}")

        # Clean up session directory
        shutil.rmtree(session_dir, ignore_errors=True)

        return jsonify({
            "status": status,
            "exit_code": exit_code,
            "summary": summary,
            "raw_output": output,
            "session_id": session_id
        }), status_code

    except Exception as e:
        print(f"[Error] Error while executing pytest: {e}")
        # Clean up any remaining resources
        shutil.rmtree(session_dir, ignore_errors=True)
        return jsonify({
            "error": str(e),
            "status": "failure",
            "exit_code": -1
        }), 500


@app.route("/execute", methods=["POST"])
def execute_code():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    code_file = request.files['file']
    session_id = str(uuid.uuid4())
    session_dir = f"/tmp/{session_id}"

    os.makedirs(session_dir, exist_ok=True)
    file_path = os.path.join(session_dir, code_file.filename)
    code_file.save(file_path)

    try:
        if prewarmed_pool:
            container = prewarmed_pool.pop()
            
            tar_data = _make_tar(file_path)
            docker_client.api.put_archive(container.id, "/code", tar_data)
            container.exec_run("python /code/main.py", detach=True)
        else:
            container = docker_client.containers.run(
                CONTAINER_IMAGE,
                command=["python", "-m", "http.server"],
                detach=True,
                auto_remove=False,
                network_disabled=True,
                volumes={
                    session_dir: {
                        "bind": "/code",
                        "mode": "ro",
                    }
                },
                working_dir="/code",
                **RESOURCE_LIMITS,
            )

        sessions[session_id] = {
            "container": container,
            "start_time": time.time(),
        }

        return jsonify({"session_id": session_id}), 200
    except Exception as e:
        print(f"[Error] Error while executing code: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/result/<session_id>", methods=["GET"])
def get_result(session_id):
    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    container = session["container"]

    try:
        container.reload()
        if container.status != "running":
            logs = container.logs().decode("utf-8")
            return jsonify({"logs": logs}), 200
        else:
            return jsonify({"status": "still running"}), 202
    except Exception as e:
        print(f"[Error] Error while retrieving logs: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/cleanup/<session_id>", methods=["POST"])
def cleanup_session(session_id):
    session = sessions.pop(session_id, None)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    container = session["container"]
    try:
        container.stop()
        container.remove()
        return jsonify({"status": "cleaned up"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def cleanup_expired_sessions():
    while True:
        for session_id, session in list(sessions.items()):
            if time.time() - session["start_time"] > TIMEOUT_SECONDS:
                container = session["container"]
                try:
                    container.stop()
                    container.remove()
                except Exception:
                    pass
                sessions.pop(session_id, None)

        time.sleep(5)

def shutdown_cleanup(*args):
    print("[Shutdown] Cleaning up prewarmed containers...")
    for container in prewarmed_pool:
        try:
            container.stop()
            container.remove()
        except Exception as e:
            print(f"[Shutdown] Error stopping container: {e}")
    prewarmed_pool.clear()

atexit.register(shutdown_cleanup)
signal.signal(signal.SIGTERM, shutdown_cleanup)
signal.signal(signal.SIGINT, shutdown_cleanup)

if __name__ == "__main__":
    Thread(target=cleanup_expired_sessions, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)