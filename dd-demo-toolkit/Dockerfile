# dd-demo-toolkit container
FROM python:3.12-slim

WORKDIR /app

# Copy application code. We include dd_demo_toolkit_ui/ even though the
# UI is designed to run locally on the SE's laptop (not in the simulator
# container) — pyproject.toml lists it in [tool.setuptools] packages, so
# pip's editable install needs the dir to exist. FastAPI/uvicorn are NOT
# installed here (they live in the optional `[ui]` extras), so running
# `dd-demo-ui` inside this container would fail with ImportError — which
# is the desired behaviour. Only `dd-demo simulate / setup / teardown`
# are meant to run from this image.
COPY dd_demo_toolkit/ ./dd_demo_toolkit/
COPY dd_demo_toolkit_ui/ ./dd_demo_toolkit_ui/
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
