FROM python:3.11-slim

# CPU-only by default (works everywhere). NVIDIA: add
# --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121
# (requires nvidia-container-toolkit on host; see docker-compose.lmr.yml's
# hoton-graphtr service for the matching runtime: nvidia + GPU reservation).
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
# Plain `pip install -r requirements.txt` alone resolves torch to the default
# PyPI wheel, which is CPU-only — install it explicitly first from the right
# index so the CUDA build (when requested) takes precedence.
RUN pip install --no-cache-dir torch==2.5.1 --index-url ${TORCH_INDEX_URL}
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8030

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8030"]
