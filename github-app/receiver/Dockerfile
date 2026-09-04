FROM python:3.12-slim

WORKDIR /srv

COPY server.py /srv/server.py

ENV PORT=8080
EXPOSE 8080

# NOTE: set these at runtime
#   WEBHOOK_SECRET, GITHUB_TOKEN, CENTRAL_OWNER, CENTRAL_REPO, DISPATCH_EVENT_TYPE

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=3)" || exit 1

CMD ["python3", "server.py"]
