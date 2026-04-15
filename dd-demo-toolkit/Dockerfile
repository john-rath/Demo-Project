# dd-demo-toolkit container
FROM python:3.12-slim

WORKDIR /app

# Copy application code
COPY dd_demo_toolkit/ ./dd_demo_toolkit/
COPY verticals/ ./verticals/
COPY pyproject.toml requirements.txt ./

# Install package
RUN pip install --no-cache-dir -e .

# Create non-root user and fix permissions
RUN useradd -m -u 1000 simulator \
    && chown -R simulator:simulator /app

USER simulator

ENV DD_DEMO_VERTICAL=healthcare
ENV PYTHONUNBUFFERED=1

CMD dd-demo simulate --vertical $DD_DEMO_VERTICAL
