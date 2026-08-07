# contributor daemon container.
#
# Build:      docker build -t contributor .
# Run:        docker run -d --name contributor -v ${PWD}/contributor-data:/data \
#                 -e CONTRIBUTOR_CONFIG=/data/config.yaml contributor
# Inspect:    docker exec -it contributor keeper status
#
# NOTE: the daemon needs access to the target repositories. Mount them
# read/write and point repositories.path at the mounted location.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CONTRIBUTOR_HOME=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY keeper ./keeper
RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN mkdir -p /data && chmod 777 /data

VOLUME ["/data"]

ENTRYPOINT ["keeper"]
CMD ["start", "--foreground", "--config", "/data/config.yaml"]
