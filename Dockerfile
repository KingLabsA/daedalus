# Daedalus — headless agent server (Hermes engine) or web IDE in a container.
#   docker build -t daedalus .
#   docker run -p 8765:8765 -v $PWD:/workspace --env-file .env daedalus            # ws server
#   docker run -p 8765:8765 -p 8899:8899 -v $PWD:/workspace daedalus web --no-browser
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE agent_ultimate.py hermes_cli.py ./
COPY core/ core/
COPY hermes_webui/ hermes_webui/
RUN pip install --no-cache-dir .
WORKDIR /workspace
ENV WS_HOST=0.0.0.0 HERMES_SUBCONSCIOUS=on
EXPOSE 8765 8899
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765')" || exit 1
ENTRYPOINT ["daedalus"]
CMD ["ws"]
