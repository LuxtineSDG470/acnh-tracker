FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data TZ=America/Chicago
LABEL org.opencontainers.image.source="https://github.com/LuxtineSDG470/acnh-tracker" \
      org.opencontainers.image.title="ACNH Tracker"
WORKDIR /srv/tracker
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 tracker \
    && useradd --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin tracker \
    && mkdir /data && chown 10001:10001 /data
# The source and startup helper remain root-owned; the app executes as UID 10001.
COPY app ./app
# The included bundle has only normalized item records and approved imagery.
COPY bundle ./bundle
COPY wsgi.py ./wsgi.py
COPY docker_entrypoint.py ./docker_entrypoint.py
# Docker will start this short initialization step as root to handle host bind
# mounts, then the entrypoint drops privileges before executing Gunicorn.
USER 0:0
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()"
ENTRYPOINT ["python", "/srv/tracker/docker_entrypoint.py"]
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "60", "wsgi:app"]
