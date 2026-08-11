FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /runner
RUN pip install --no-cache-dir browsergym-miniwob
COPY run_smoke.py /runner/run_smoke.py

ENTRYPOINT ["python", "/runner/run_smoke.py"]
