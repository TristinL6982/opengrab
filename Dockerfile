FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENGRAB_BIND=0.0.0.0 OPENGRAB_PORT=5678 OPENGRAB_DATA_DIR=/config OPENGRAB_DOWNLOAD_DIR=/downloads
WORKDIR /app
COPY requirements.txt setup.py ./
COPY src ./src
RUN python -m pip install --no-cache-dir . && mkdir -p /config /downloads
VOLUME ["/config", "/downloads"]
EXPOSE 5678
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5678/api/health', timeout=3)" || exit 1
CMD ["python", "-m", "opengrab", "--headless", "--bind", "0.0.0.0"]
