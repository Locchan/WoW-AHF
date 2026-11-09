FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml poetry.lock main.py ./
COPY wowahf ./wowahf

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir numpy PyMySQL

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Mount secrets/settings at runtime: -v /path/to/config.json:/config.json:ro
CMD ["python", "main.py"]
