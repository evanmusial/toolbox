FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py shortlinks.py ./

USER 65532:65532

CMD ["sh", "-c", "exec gunicorn --bind :${PORT:-8080} --workers 1 --threads 16 --timeout 30 --access-logfile - --error-logfile - app:app"]
