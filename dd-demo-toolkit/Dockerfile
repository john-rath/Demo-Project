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

# When DD_DEMO_SUB_VERTICAL is set (e.g. "bd"), append --sub-vertical so
# the overlay's devices, services, and incident plugins are loaded into
# the simulator. Empty / unset → base vertical only.
CMD ["sh", "-c", "dd-demo simulate --vertical $DD_DEMO_VERTICAL ${DD_DEMO_SUB_VERTICAL:+--sub-vertical $DD_DEMO_SUB_VERTICAL}"]
