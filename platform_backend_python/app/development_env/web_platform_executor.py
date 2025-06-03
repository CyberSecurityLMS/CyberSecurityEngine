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
    except Exception as e:
        print(f"[Prewarm] Failed to create container: {e}")

def initialize_prewarmed_pool():
    while len(prewarmed_pool) < PREWARMED_POOL_SIZE:
        create_prewarmed_container()


def _make_tar(file_path):
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        arcname = os.path.basename(file_path)
        tar.add(file_path, arcname=arcname) 
    tar_stream.seek(0)
    return tar_stream


# Execute code in a Docker container
# This endpoint receives a code archive, runs it in a container, and returns a session ID
# The session ID can be used to check the status of the execution or retrieve logs
@app.route("/execute", methods=["POST"])
def execute_code():
    # Receive the code archive
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

        # Store the session info
        sessions[session_id] = {
            "container": container,
            "start_time": time.time(),
        }

        return jsonify({"session_id": session_id}), 200
    except Exception as e:
        print(f"[Error] Error while executing code: {e}")
        return jsonify({"error": str(e)}), 500

# Check the result of the execution
# This endpoint retrieves the logs of the container if it has finished executing
@app.route("/result/<session_id>", methods=["GET"])
def get_result(session_id):
    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    container = session["container"]

    try:
        container.reload()  # Refresh the container stat
        if container.status != "running":
            logs = container.logs().decode("utf-8")
            return jsonify({"logs": logs}), 200
        else:
            return jsonify({"status": "still running"}), 202
    except Exception as e:
        print(f"[Error] Error while retrieving logs: {e}")
        return jsonify({"error": str(e)}), 500

# Clean up the session
# This endpoint stops and removes the container associated with the session ID
# It also removes the session from the session management dictionary
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

# Background task to clean up timed-out containers
# This function runs in a separate thread and checks for expired sessions
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

        if len(prewarmed_pool) < PREWARMED_POOL_SIZE:
            create_prewarmed_container()

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
    # initialize_prewarmed_pool()
    Thread(target=cleanup_expired_sessions, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
