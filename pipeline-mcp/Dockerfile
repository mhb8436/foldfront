FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /app/requirements.txt

COPY src /app/src

ENV PYTHONPATH=/app/src \
    PIPELINE_OUTPUT_ROOT=/app/outputs

EXPOSE 8000

# Default to HTTP so it is easy to run on NCP.
CMD ["python", "-m", "pipeline_mcp.http_server"]
