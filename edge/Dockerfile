FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgl1 ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY edge/requirements.txt /app/edge/requirements.txt
RUN pip install --no-cache-dir -r /app/edge/requirements.txt

COPY configs/ /app/configs/
COPY edge/ /app/edge/
COPY models/ /app/models/

ENV PYTHONPATH=/app
CMD ["python", "-m", "edge.src.edge_runner"]
