import subprocess
import threading
import time
import os
import signal
from flasgger import Swagger

def run_flask():
    try:
        subprocess.run(["python", "./platform_backend_python/app/development_env/web_platform_executor.py"])
    except KeyboardInterrupt:
        print("[Flask] Приложение остановлено.")


def run_pytest():
    time.sleep(2)
    try:
        subprocess.run(["pytest", "./platform_backend_python/app/development_env/test_executor.py"])
    except KeyboardInterrupt:
        print("[Pytest] Тесты остановлены.")

if __name__ == "__main__":
    print("[Runner] Запуск Flask и Pytest...")

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    test_thread = threading.Thread(target=run_pytest)
    test_thread.start()

    flask_thread.join()
    test_thread.join()

    print("[Runner] Всё завершено.")
