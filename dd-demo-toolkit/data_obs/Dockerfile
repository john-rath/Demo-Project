# Image for the EY Data Streams Monitoring demo workers.
#
# Hosts three interchangeable services (producer / feature_pipeline /
# eval_consumer). docker-compose picks which entrypoint each container
# runs via the `command:` field.
#
# Keep this image lean — no toolkit Python deps, no verticals/ tree, no
# pyproject install. We only need `ddtrace` (for DSM auto-instrumentation
# of confluent-kafka) and `confluent-kafka` itself.

FROM python:3.13-slim

WORKDIR /app

# `confluent-kafka` ships prebuilt manylinux wheels that bundle
# librdkafka — no apt-get build chain needed on amd64/arm64.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY producer.py feature_pipeline.py eval_consumer.py ./

# Default to producer; docker-compose overrides with `command:` per-service.
ENV PYTHONUNBUFFERED=1
CMD ["python", "producer.py"]
