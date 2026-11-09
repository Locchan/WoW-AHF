ARG BASE_IMAGE=wow_ahf-base:latest
FROM ${BASE_IMAGE}

WORKDIR /app

COPY main.py ./
COPY wowahf ./wowahf

# Mount secrets/settings at runtime: -v /path/to/config.json:/config.json:ro
CMD ["python", "main.py"]
