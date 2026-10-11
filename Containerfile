FROM docker.io/library/python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/sfdojo.db UPLOAD_DIR=/data/uploads LOG_FILE=/data/sfdojo-events.log
RUN useradd --uid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin sfdojo \
 && mkdir -p /app /data && chown sfdojo:sfdojo /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY seed.py .
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
USER sfdojo
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
