# Self-contained base image: current Supervisors no longer pass BUILD_FROM.
# SUPERVISOR_TOKEN and /data/options.json are provided by the Supervisor directly (init: false), so no s6/bashio is needed.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nmap iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir anthropic httpx pyyaml websockets pillow

COPY guardian ./guardian
COPY guardian_default_config.yaml ./default_config.yaml

CMD ["python", "-u", "-m", "guardian", "addon"]
