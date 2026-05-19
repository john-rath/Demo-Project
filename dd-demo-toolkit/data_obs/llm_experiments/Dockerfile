# Image for the EY LLM Obs Experiment runner.
#
# Designed as a one-shot job: build, run experiment, exit. Compose
# launches it via `--profile llm-experiments run --rm`, so no restart
# loop and no long-running container footprint.

FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY risk_eval_experiment.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "risk_eval_experiment.py"]
