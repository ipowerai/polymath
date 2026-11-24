# The Polymath dashboard in a container. It serves the data folder mounted at /data.
#
#   docker build -t polymath .
#   docker run -d -p 8765:8765 -v ~/.polymath:/data --user "$(id -u):$(id -g)" \
#     -e POLYMATH_WEB_TOKEN=<a long random token> -e TZ=America/Los_Angeles polymath
#
# Listening on every interface means a sign-in token is required. Without
# POLYMATH_WEB_TOKEN, one is generated, saved in /data/web-token and printed in the logs.
#
# Study in the browser needs a question writer. The image has no Claude Code, so build it
# with the Anthropic SDK and pass an API key (billed per token):
#   docker build --build-arg WITH_API=1 -t polymath .
#   docker run ... -e POLYMATH_TUTOR=api -e ANTHROPIC_API_KEY=<key> polymath
# See docs/DASHBOARD.md.
FROM python:3.12-slim
ARG WITH_API=0

# Time zone data, so TZ sets where your study days start and end.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
RUN if [ "$WITH_API" = "1" ]; then pip install --no-cache-dir anthropic; fi

WORKDIR /app
COPY scripts/ scripts/
COPY web/ web/
COPY blueprints/ blueprints/

ENV POLYMATH_HOME=/data \
    POLYMATH_WEB_HOST=0.0.0.0 \
    POLYMATH_WEB_PORT=8765 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8765
CMD ["python3", "scripts/polymath.py", "serve"]
