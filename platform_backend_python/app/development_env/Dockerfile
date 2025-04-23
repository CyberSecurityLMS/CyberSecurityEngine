FROM python:3.9-slim

RUN apt-get update && apt-get install -y gcc && \
    pip install --no-cache-dir flask flasgger docker

WORKDIR /app
COPY web_platform_executor.py .

CMD ["python", "web_platform_executor.py"]



