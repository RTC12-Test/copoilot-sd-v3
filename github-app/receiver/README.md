# ci-fixer-webhook receiver — stdlib only, no dependencies.
#
# Run locally:
#   WEBHOOK_SECRET=<secret> GITHUB_TOKEN=<tok> CENTRAL_OWNER=<owner> python3 server.py
#
# Or build a tiny image:
#   FROM python:3.12-slim
#   COPY . /srv
#   WORKDIR /srv
#   CMD ["python3", "server.py"]
