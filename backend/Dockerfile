FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ /app/requirements/
RUN pip install --upgrade pip && \
    pip install -r requirements/local.txt

COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh
RUN chmod +x /app/scripts/entrypoint.sh

COPY . /app/

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
