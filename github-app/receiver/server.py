"""
ci-fixer-webhook receiver.

Receives GitHub webhooks (workflow_run / check_run) from every child repo in the
org, filters for CI failures, and triggers the `ci-fixer` `repository_dispatch`
on the central repo so a fix workflow runs immediately.

Deploys as a tiny HTTP service (stdlib only). Set these env vars:

  WEBHOOK_SECRET      : shared secret GitHub signs webhooks with (required)
  GITHUB_TOKEN        : token with repo write on the CENTRAL repo (to dispatch)
  CENTRAL_OWNER       : owner of the central repo (required)
  CENTRAL_REPO        : name of the central repo, default "copilot-central"
  DISPATCH_EVENT_TYPE : event_type for repo_dispatch, default "ci-fixer"
  DISPATCH_BASE_URL   : API base, default https://api.github.com
  MAX_FOLLOWUP_FAILURES: only dispatch when conclusion is a hard failure
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Conclusions that we treat as "actionable failure".
HARD_FAILURES = {"failure", "timed_out", "startup_failure"}
FAILURES = HARD_FAILURES | {"cancelled"}

# In-flight dedup: we only want one dispatch per (repo, run) and we don't want
# to re-dispatch a run we already handled.
_seen = {}


def _env(name, default=None):
    return os.environ.get(name, default)


def _sha256_like(payload: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _verify_signature(payload: bytes, secret: str, header: str) -> bool:
    if not header:
        return False
    expected = _sha256_like(payload, secret)
    return hmac.compare_digest(header, expected)


def _dispatch(repo_dispatch_url: str, token: str, event_type: str, client_payload: dict) -> int:
    body = json.dumps({"event_type": event_type, "client_payload": client_payload}).encode()
    req = urllib.request.Request(
        repo_dispatch_url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _forward(child, conclusion, run_id, head_branch, head_sha, html_url, event) -> int:
    central_owner = _env("CENTRAL_OWNER")
    central_repo = _env("CENTRAL_REPO", "copilot-central")
    token = _env("GITHUB_TOKEN")
    dispatch_type = _env("DISPATCH_EVENT_TYPE", "ci-fixer")
    api_base = _env("DISPATCH_BASE_URL", "https://api.github.com")

    if not (central_owner and token):
        print("missing CENTRAL_OWNER/GITHUB_TOKEN, skipping dispatch")
        return 0

    client_payload = {
        "repository": child,
        "conclusion": conclusion,
        "run_id": int(run_id) if run_id else None,
        "head_branch": head_branch,
        "head_sha": head_sha,
        "html_url": html_url,
        "event": event,
    }
    url = f"{api_base}/repos/{central_owner}/{central_repo}/dispatches"
    status = _dispatch(url, token, dispatch_type, client_payload)
    print(f"dispatch -> {child} run {run_id} conclusion {conclusion}: HTTP {status}")
    return status


def _handle_workflow_run(payload: dict) -> None:
    action = payload.get("action")
    if action != "completed":
        return
    run = payload.get("workflow_run") or {}
    conclusion = run.get("conclusion")
    if conclusion not in FAILURES:
        return
    repo = (payload.get("repository") or {}).get("full_name")
    run_id = run.get("id")
    key = f"{repo}:{run_id}"
    if key in _seen:
        return
    _seen[key] = time.time()
    _forward(
        child=repo,
        conclusion=conclusion,
        run_id=run_id,
        head_branch=run.get("head_branch"),
        head_sha=run.get("head_sha"),
        html_url=run.get("html_url"),
        event=run.get("event"),
    )


def _handle_check_run(payload: dict) -> None:
    action = payload.get("action")
    if action != "completed":
        return
    check = payload.get("check_run") or {}
    conclusion = check.get("conclusion")
    if conclusion not in HARD_FAILURES:
        return
    repo = (payload.get("repository") or {}).get("full_name")
    # check_run has no run_id; use external_id / check_run id as correlation key.
    run_id = check.get("external_id") or check.get("id")
    key = f"{repo}:check:{run_id}"
    if key in _seen:
        return
    _seen[key] = time.time()
    head_branch = None
    head_sha = check.get("head_sha")
    url = check.get("html_url")
    prs = (payload.get("check_run") or {}).get("pull_requests") or []
    if prs:
        ref = prs[0].get("ref")
        if ref:
            head_branch = ref.replace("refs/heads/", "")
    _forward(
        child=repo,
        conclusion=conclusion,
        run_id=run_id,
        head_branch=head_branch,
        head_sha=head_sha,
        html_url=url,
        event="check_run",
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length)
        secret = _env("WEBHOOK_SECRET")
        if secret:
            sig = self.headers.get("X-Hub-Signature-256", "")
            if not _verify_signature(payload, secret, sig):
                print("invalid signature")
                self._send(401, "invalid signature")
                return
        event = self.headers.get("X-GitHub-Event", "")
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            self._send(400, "bad json")
            return
        print(f"event={event}")
        if event == "workflow_run":
            _handle_workflow_run(data)
        elif event == "check_run":
            _handle_check_run(data)
        else:
            # Ping / other events: acknowledge but do nothing.
            pass
        self._send(200, "ok")

    def do_GET(self):
        self._send(200, "ci-fixer-webhook receiver is up")

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")


def main():
    port = int(_env("PORT", "8080"))
    host = _env("HOST", "0.0.0.0")
    print(f"ci-fixer-webhook listening on {host}:{port}")
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
