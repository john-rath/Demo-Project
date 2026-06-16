# Sensing Hospital mock-environment image.
#
# One image, several entrypoints — each docker-compose service overrides the
# `command` to run a different module (edge fleet generator, the three
# microservices, or the remediation controller). They're all small Python
# apps auto-instrumented by dd-trace-py (`ddtrace-run`), so the REAL Datadog
# Agent (not the OTel collector) collects APM traces, logs, and — via the
# Docker socket mount on the agent — process/container infrastructure metrics.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Overridden per compose service. Default prints the available entrypoints.
CMD ["python", "-c", "print('set a command: edge_device | rtls_location_service | care_event_router | care_experience_platform | remediation_controller')"]
