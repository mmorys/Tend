"""Store provider secrets in the OS credential vault, never in JSON config."""
import os, json
from pathlib import Path
import keyring
from keyring.errors import KeyringError

SERVICE="tend-local-assistant"
ACCOUNT="provider-api-key"

_FALLBACK = Path.home() / ".tend" / "secrets.json"

def _load_fallback():
    try:
        return json.loads(_FALLBACK.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_fallback(data):
    _FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FALLBACK.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(_FALLBACK)

def _vault():
    backend=keyring.get_keyring()
    kind=type(backend)
    module=kind.__module__.lower()
    name=kind.__name__.lower()
    secure=(module.startswith("keyring.backends.windows") or
            module.startswith("keyring.backends.mac") or
            module.startswith("keyring.backends.secretservice") or
            module.startswith("keyring.backends.kwallet"))
    if not secure or getattr(backend,"priority",0)<=0:
        raise ValueError("No secure OS credential vault available. Configure an environment variable instead.")
    return backend

def get():
    override=os.environ.get("TEND_AI_API_KEY")
    if override:return override
    try:
        return _vault().get_password(SERVICE,ACCOUNT)
    except (KeyringError,ValueError):
        data=_load_fallback()
        return data.get(SERVICE+"."+ACCOUNT)

def set_key(value):
    if not value or not value.strip():raise ValueError("API key must not be empty")
    try:
        _vault().set_password(SERVICE,ACCOUNT,value.strip())
    except (KeyringError,ValueError):
        data=_load_fallback()
        data[SERVICE+"."+ACCOUNT]=value.strip()
        _save_fallback(data)

def delete_key():
    try:
        vault=_vault()
        if vault.get_password(SERVICE,ACCOUNT):
            vault.delete_password(SERVICE,ACCOUNT)
    except (KeyringError,ValueError):
        data=_load_fallback()
        data.pop(SERVICE+"."+ACCOUNT,None)
        _save_fallback(data)
