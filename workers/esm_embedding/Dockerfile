FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/workspace/hf_cache \
    TRANSFORMERS_CACHE=/workspace/hf_cache \
    ESM_MODEL_NAME=facebook/esm2_t6_8M_UR50D \
    ESM_BATCH_SIZE=64 \
    ESM_MAX_LENGTH=1024

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY embedder.py handler.py http_server.py /app/

CMD ["python", "-u", "/app/handler.py"]
