FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements-p0.txt ./
RUN pip install --no-cache-dir -r requirements-p0.txt

COPY src ./src

EXPOSE 8080

CMD ["uvicorn", "culprit_runner.app:app", "--host=0.0.0.0", "--port=8080"]
