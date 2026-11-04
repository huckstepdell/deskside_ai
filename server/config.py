"""Deployment configuration.

Two layers:
- models.json (tracked): the curated catalog — labels, traits, role
  recommendations, install tiers. Editorial, versioned, shared.
- demo-config.json (gitignored, optional): everything specific to
  one deployment — companion inference hosts, role overrides. A fresh GB10
  with no config file gets a fully working single-box demo.

demo-config.json shape:
{
  "hosts": {
    "bigbox": {"label": "Lab server — 4× L40S", "url": "http://bigbox:11434",
                "ssh_power": "bigbox",          // ssh alias for nvidia-smi watts (optional)
                "telemetry_url": "http://bigbox:9101"}  // node metrics agent (optional, preferred over ssh)
  },
  "roles": {"workhorse": "some-model:tag"}      // override any models.json role
}
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# UI-only deployments point the primary host ("gb10") at a remote compute node.
LOCAL_OLLAMA = os.environ.get("OLLAMA_URL") or "http://localhost:11434"
PRIMARY_REMOTE = bool(os.environ.get("OLLAMA_URL"))
PRIMARY_LABEL = os.environ.get("PRIMARY_LABEL") or ("GB10 — this box" if not PRIMARY_REMOTE else "Primary node")
# metrics agent (server/agent.py) of the primary node, when it isn't this machine
TELEMETRY_URL = (os.environ.get("TELEMETRY_URL") or "").rstrip("/")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


_models = _load(ROOT / "models.json")
_local = _load(Path(os.environ.get("DEMO_CONFIG") or ROOT / "demo-config.json"))
try:  # DEMO_HOSTS: same shape as the "hosts" object, for deployments with no config file
    _local.setdefault("hosts", {}).update(json.loads(os.environ.get("DEMO_HOSTS") or "{}"))
except ValueError:
    pass

CATALOG: dict[str, dict] = _models.get("catalog", {})
TIERS: dict[str, list] = _models.get("tiers", {})
def _clean_roles(overrides: dict) -> dict:
    """Ignore override values that aren't plausible Ollama tags — installers
    before Aug 2026 could write a mangled 'role=model' string into this file,
    and a bad role silently breaks every page that resolves one."""
    return {k: v for k, v in overrides.items()
            if isinstance(v, str) and v and "=" not in v and not v.split()[1:]}


ROLES: dict[str, str] = {**_models.get("roles", {}), **_clean_roles(_local.get("roles", {}))}

# The local box is always host "gb10"; companion boxes come from local config.
HOSTS: dict[str, dict] = {
    "gb10": {"label": PRIMARY_LABEL, "url": LOCAL_OLLAMA, "local": not PRIMARY_REMOTE,
             "telemetry_url": TELEMETRY_URL or None},
}
for key, h in _local.get("hosts", {}).items():
    if key == "gb10" or not isinstance(h, dict) or not h.get("url"):
        continue
    HOSTS[key] = {"label": h.get("label", key), "url": h["url"],
                  "local": False, "ssh_power": h.get("ssh_power"),
                  "telemetry_url": (h.get("telemetry_url") or "").rstrip("/") or None}


def role(name: str) -> str:
    """The configured model for a role (may or may not be installed —
    callers that need certainty should check against Ollama's tag list)."""
    return ROLES.get(name, "")
