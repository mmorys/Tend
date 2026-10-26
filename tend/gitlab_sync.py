"""Periodic GitLab MR review sync."""
import threading
import time
from datetime import datetime, timezone
import urllib.request
import urllib.error
import urllib.parse
import json

from . import gitlab_secrets, gitlab_config, store as db

_interval = 15 * 60  # seconds
_stop = threading.Event()
_thread = None

def _now():
    return datetime.now(timezone.utc).isoformat()

def _request(path, token, url_base):
    base = url_base.rstrip("/")
    full = f"{base}/api/v4{path}"
    req = urllib.request.Request(full, headers={"PRIVATE-TOKEN": token, "User-Agent": "tether-chat"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            return json.loads(data)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        raise RuntimeError(f"GitLab API error {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"GitLab request failed: {e}") from e

def _paginate(path, token, url_base, params=None):
    results = []
    page = 1
    per_page = 100
    while True:
        qs = f"?page={page}&per_page={per_page}"
        if params:
            qs += "&" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
        data = _request(path + qs, token, url_base)
        if not isinstance(data, list):
            break
        results.extend(data)
        if len(data) < per_page:
            break
        page += 1
    return results

def _fetch_user(token, url_base):
    return _request("/user", token, url_base)

def _fetch_projects(token, url_base, user_id):
    # Projects where user is member
    # Use pagination via membership filter
    path = "/projects"
    params = {"membership": "true", "owned": "false", "min_access_level": 10}
    # Note: API v4 supports membership param
    return _paginate(path, token, url_base, params)

def _fetch_mrs_for_project(token, url_base, project_id, reviewer_id):
    path = f"/projects/{project_id}/merge_requests"
    params = {"state": "opened", "reviewer_id": reviewer_id}
    return _paginate(path, token, url_base, params)

def _find_existing_thread(mr):
    title = f"Review MR !{mr['iid']} in {mr['project_path']}"
    # Search by exact title
    rows = db.query("SELECT id FROM threads WHERE title = ? AND archived_at IS NULL", (title,))
    if rows:
        return rows[0]["id"]
    return None

def _create_thread(mr):
    title = f"Review MR !{mr['iid']} in {mr['project_path']}"
    summary = f"Merge request {mr['title']}\n{self_url(mr)}"
    categories = ["GitLab"]
    thread = db.create(title, summary, categories)
    # Store MR identifiers in action_config
    db.update(thread["id"], {"action_config": {"gitlab_mr": {"project_id": mr["project_id"], "iid": mr["iid"], "project_path": mr["project_path"]}}})
    # Add initial activity
    db.record_activity(thread["id"], f"MR opened by {mr['author']['name']}", kind="milestone")
    return thread["id"]

def self_url(mr):
    base = mr.get("web_url", "")
    return f"{base}\n\nReviewers: {', '.join(r['name'] for r in mr.get('reviewers', []))}"

def sync_once():
    token = gitlab_secrets.get()
    if not token:
        return {"skipped": "no token"}
    url_base = gitlab_config.get_url()
    try:
        user = _fetch_user(token, url_base)
        user_id = user["id"]
    except Exception as e:
        return {"error": f"user fetch failed: {e}"}
    try:
        projects = _fetch_projects(token, url_base, user_id)
    except Exception as e:
        return {"error": f"projects fetch failed: {e}"}
    created = 0
    updated = 0
    for proj in projects:
        try:
            mrs = _fetch_mrs_for_project(token, url_base, proj["id"], user_id)
        except Exception:
            continue
        for mr in mrs:
            existing = _find_existing_thread(mr)
            if not existing:
                _create_thread(mr)
                created += 1
            else:
                # Optionally add update if MR changed
                updated += 1
    return {"created": created, "updated": updated, "projects": len(projects)}

def _loop():
    while not _stop.is_set():
        try:
            sync_once()
        except Exception:
            pass
        _stop.wait(_interval)

def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, daemon=True, name="gitlab-sync")
    _thread.start()

def stop():
    _stop.set()
