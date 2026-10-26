"""Persist GitLab instance URL."""
import json
from pathlib import Path

def config_path():
    return Path.home() / ".tend" / "gitlab.json"

def load():
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}

def save(data):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    import tempfile, os
    fd, tmp = tempfile.mkstemp(prefix=".gitlab-", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def get_url():
    return load().get("url", "https://gitlab.com")

def set_url(url):
    if not url:
        raise ValueError("URL required")
    cfg = load()
    cfg["url"] = url.rstrip("/")
    save(cfg)
