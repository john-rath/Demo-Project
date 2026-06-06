FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    psycopg2-binary \
    opentelemetry-sdk \
    opentelemetry-exporter-otlp-proto-grpc \
    opentelemetry-semantic-conventions
COPY worker.py .
CMD ["python", "worker.py"]
