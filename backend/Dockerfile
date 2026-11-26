FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libglib2.0-0 libgl1 && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY configs/ /app/configs/
COPY aws/lambdas/ /app/aws/lambdas/
COPY edge/ /app/edge/
COPY backend/ /app/backend/
COPY tests/simulator_cli.py /app/tests/simulator_cli.py
COPY tests/mock_aws_store.py /app/tests/mock_aws_store.py
COPY tests/__init__.py /app/tests/__init__.py

RUN mkdir -p /app/edge/data/snapshots /app/models

ENV PYTHONPATH=/app:/app/aws/lambdas
EXPOSE 8000

CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
