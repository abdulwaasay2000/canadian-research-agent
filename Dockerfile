FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src CRA_HOME=/app CRA_DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
RUN useradd --create-home cra && mkdir -p /app/data && chown -R cra /app
USER cra

EXPOSE 8501
# Web default: dashboard on $PORT (hosts set it; 8501 locally). API: override CMD with
#   uvicorn cra.api.main:app --host 0.0.0.0 --port 8000
CMD ["sh", "start.sh"]
