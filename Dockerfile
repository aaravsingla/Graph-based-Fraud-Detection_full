# Synapse Neptune CSV export container (AWS Batch on ECS Fargate)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    apt-transport-https \
    unixodbc \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

# Microsoft ODBC Driver 18 for SQL Server
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY batchjobs/csv_export/requirements.txt /app/batchjobs/csv_export/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/batchjobs/csv_export/requirements.txt

COPY batchjobs/csv_export /app/batchjobs/csv_export

WORKDIR /app/batchjobs/csv_export

ENV IS_LOCAL=false \
    EXPORT_TO_S3=true \
    EXPORT_S3_SYNC_EACH_STEP=false

CMD ["python", "run_all_exports.py", "--no-venv", "--no-install-deps", "--output-root", "../data"]
