# Real-browser RUM traffic generator. Uses the official Playwright Python image,
# which ships Chromium + its system deps preinstalled (no runtime download).
# Pinned for reproducibility. NOTE: this base image is large (~1.7GB) — that's
# why the traffic generator is opt-in behind the `traffic` compose profile.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app
COPY traffic_generator.py /app/traffic_generator.py

CMD ["python", "traffic_generator.py"]
