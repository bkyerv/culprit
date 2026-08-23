FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CULPRIT_WEB_DIR=/app/services/control/web

WORKDIR /app

COPY services/control ./services/control
RUN pip install --no-cache-dir ./services/control

EXPOSE 8080

CMD ["uvicorn", "culprit_control.app:app", "--host=0.0.0.0", "--port=8080"]
