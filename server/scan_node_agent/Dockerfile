FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /opt/sere1nfish

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 scan-node \
    && mkdir -p /var/lib/sere1nfish-node \
    && chown -R scan-node:scan-node /var/lib/sere1nfish-node /opt/sere1nfish /opt/playwright

COPY --chown=scan-node:scan-node . /opt/sere1nfish/scan_node_agent

USER scan-node

ENTRYPOINT ["python", "-m", "scan_node_agent"]
