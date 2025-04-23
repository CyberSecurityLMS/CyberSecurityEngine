
---

# 🚀 Docker Code Executor API

A lightweight Flask-based REST API that allows users to upload and execute Python scripts in isolated Docker containers. Each execution is sandboxed, with resource limits and session management for security and performance.

---

## 📦 Features

- 🔐 Runs code inside isolated Docker containers
- 🧼 Session cleanup & prewarming
- 🧪 Check execution results and logs
- 🔁 Docker Compose support for easy deployment
- 🧾 Swagger (OpenAPI) documentation included

---

## 📁 Project Structure

```
CYBERSECURITYPLATFORM/
├── platform_backend_python/
    ├── docker-compose.yml
    └── app/
        └── development_env/
            ├── web_platform_executor.py
            ├── swagger.yml
            └── Dockerfile
```

---

## 🚀 Quickstart

### 1. Build & Run

Make sure Docker is installed, then:

```bash
docker-compose up --build
```

The API will be available at: [http://localhost:5000](http://localhost:5000)

### 2. Swagger Docs

Interactive API docs:  
👉 [http://localhost:5000/apidocs](http://localhost:5000/apidocs)

---

## 📌 API Endpoints

### 🔸 `POST /execute`
Upload and execute a Python file in Docker.

**Request:** `multipart/form-data`

- `file`: Python script (e.g. `main.py`)

**Response:**
```json
{
  "session_id": "abc-123-xyz"
}
```

---

### 🔸 `GET /result/{session_id}`
Fetch execution logs by session ID.

**Response if completed:**
```json
{
  "logs": "Hello from Python\n"
}
```

**If still running:**
```json
{
  "status": "still running"
}
```

---

### 🔸 `POST /cleanup/{session_id}`
Stop and remove a running container.

**Response:**
```json
{
  "status": "cleaned up"
}
```

---

### 🔸 `POST /prewarm`
Manually trigger prewarming of Docker containers.

**Response:**
```json
{
  "status": "prewarm triggered"
}
```

---

## 🛠 Tech Stack

- **Python 3.9**
- **Flask + Flasgger**
- **Docker SDK for Python**
- **Docker Compose**

---

## ⚠️ Notes

- Only Python scripts (`.py`) are supported.
- Code runs with CPU and memory limits for safety.
- Docker socket (`/var/run/docker.sock`) must be mounted for container control.

---
