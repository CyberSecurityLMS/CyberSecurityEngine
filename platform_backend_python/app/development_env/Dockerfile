FROM python:3.13-slim

WORKDIR /app 

COPY requirements.txt web_platform_executor.py swagger.yml ./

RUN apt-get update && apt-get install -y gcc && \
    pip install --no-cache-dir -r requirements.txt




CMD ["python", "web_platform_executor.py"]



