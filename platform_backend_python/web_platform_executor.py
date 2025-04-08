from flask import Flask, request, jsonify
import docker
import os
import uuid
import time

app = Flask(__name__)

# Initialize the Docker client
docker_client = docker.from_env()

# Container settings
CONTAINER_IMAGE = "python:3.9-slim" # Base image for running the code
RESOURCE_LIMITS = {
    "cpu_quota": 50000,             # Limit CPU usage
    "mem_limit": "256m",            # Limit memory usage
}
TIMEOUT_SECONDS = 10

# Session management
sessions = {}

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
        # Create the container
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
        return jsonify({"error": str(e)}), 500

# Check the result of the execution
# This endpoint retrieves the logs of the container if it has finished executing
@app.route("/result/<session_id>", methods=["GET"])
def get_result(session_id):
    session = sessions.get(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    container = session["container"]
    if container.status != "running":
        logs = container.logs().decode("utf-8")
        return jsonify({"logs": logs}), 200
    else:
        return jsonify({"status": "still running"}), 202

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
        time.sleep(1)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
