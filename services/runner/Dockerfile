FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY packages/culprit_core ./packages/culprit_core
COPY services/runner ./services/runner
COPY scenarios ./scenarios
RUN pip install --no-cache-dir ./packages/culprit_core ./services/runner

EXPOSE 8080

CMD ["uvicorn", "culprit_runner.app:app", "--host=0.0.0.0", "--port=8080"]
