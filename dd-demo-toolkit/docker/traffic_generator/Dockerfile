# Real-browser RUM traffic generator. Uses the official Playwright Python image,
# which ships Chromium + system deps preinstalled (no runtime browser download).
# Pinned for reproducibility. NOTE: the base image is large (~1.7GB).
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# The base image is supposed to include the `playwright` Python package, but
# depending on which `python` ends up first on PATH at runtime the import can
# fail (`ModuleNotFoundError: No module named 'playwright'`). Install it
# explicitly so it's available regardless. Browser binaries are already there.
RUN pip install --no-cache-dir playwright==1.47.0

COPY traffic_generator.py /app/traffic_generator.py

CMD ["python", "traffic_generator.py"]
