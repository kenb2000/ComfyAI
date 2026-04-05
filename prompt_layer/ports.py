"""Deterministic local port allocation backed by a shared cross-repo registry."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_REGISTRY_FILENAME = "MasterPorts.json"
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_LOCK_STALE_SECONDS = 30.0
DEFAULT_STARTUP_GRACE_SECONDS = 30.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _slugify(value: str) -> str:
    lowered = "".join(char.lower() if char.isalnum() else "-" for char in str(value).strip())
    while "--" in lowered:
        lowered = lowered.replace("--", "-")
    return lowered.strip("-") or "app"


def default_app_id(project_root: Path | str | None = None) -> str:
    env_value = os.environ.get("COMFYHYBRID_APP_ID", "").strip()
    if env_value:
        return _slugify(env_value)
    if project_root is not None:
        return _slugify(Path(project_root).resolve().name)
    return "comfyuihybrid"


def default_master_ports_path() -> Path:
    override = os.environ.get("MASTER_PORTS_PATH", "").strip()
    if override:
        return Path(override).expanduser()

    home = Path.home()
    root = home / "Projects"
    return root / DEFAULT_REGISTRY_FILENAME


def default_local_ports_path(project_root: Path | str) -> Path:
    return Path(project_root).resolve() / "tools" / "runtime" / "ports.json"


def default_port_range(preferred_port: int, width: int = 100) -> tuple[int, int]:
    start = max(1, int(preferred_port))
    end = min(65535, start + max(0, int(width) - 1))
    return start, end


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent), prefix=path.name, suffix=".tmp") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)


@contextmanager
def _registry_lock(path: Path, timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.time() + timeout_seconds

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                payload = {"pid": os.getpid(), "created_at": _now_iso()}
                os.write(fd, json.dumps(payload, ensure_ascii=True).encode("utf-8"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            if lock_path.exists():
                age_seconds = max(0.0, time.time() - lock_path.stat().st_mtime)
                if age_seconds >= DEFAULT_LOCK_STALE_SECONDS:
                    try:
                        lock_path.unlink()
                        continue
                    except OSError:
                        pass
            if time.time() >= deadline:
                raise TimeoutError(f"Timed out waiting for lock on {path}.")
            time.sleep(0.05)

    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "updated_at": _now_iso(), "entries": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": 1, "updated_at": _now_iso(), "entries": []}
    if not isinstance(raw, dict):
        return {"schema_version": 1, "updated_at": _now_iso(), "entries": []}
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    return {
        "schema_version": int(raw.get("schema_version", 1) or 1),
        "updated_at": str(raw.get("updated_at", _now_iso())),
        "entries": [entry for entry in entries if isinstance(entry, dict)],
    }


def _is_process_alive(pid: Any) -> bool:
    try:
        resolved = int(pid)
    except (TypeError, ValueError):
        return False
    if resolved <= 0:
        return False
    try:
        os.kill(resolved, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def is_port_free(host: str, port: int, protocol: str = "tcp") -> bool:
    family = socket.AF_INET6 if ":" in str(host) and not str(host).startswith("[") else socket.AF_INET
    sock_type = socket.SOCK_DGRAM if str(protocol).lower() == "udp" else socket.SOCK_STREAM
    bind_host = str(host or "127.0.0.1").strip() or "127.0.0.1"
    with socket.socket(family, sock_type) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            sock.bind((bind_host, int(port)))
        except OSError:
            return False
    return True


def _entry_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("app_id", "")),
        str(entry.get("service_name", "")),
        str(entry.get("protocol", "tcp")),
        str(entry.get("host", "127.0.0.1")),
    )


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(entry)
    normalized["app_id"] = str(normalized.get("app_id", "")).strip()
    normalized["service_name"] = str(normalized.get("service_name", "")).strip()
    normalized["protocol"] = str(normalized.get("protocol", "tcp")).strip().lower() or "tcp"
    normalized["host"] = str(normalized.get("host", "127.0.0.1")).strip() or "127.0.0.1"
    normalized["requested_port"] = int(normalized.get("requested_port", 0) or 0)
    normalized["assigned_port"] = int(normalized.get("assigned_port", 0) or 0)
    if normalized.get("pid") in (None, ""):
        normalized["pid"] = None
    else:
        normalized["pid"] = int(normalized["pid"])
    normalized["started_at"] = str(normalized.get("started_at") or _now_iso())
    if "notes" in normalized and normalized["notes"] is not None:
        normalized["notes"] = str(normalized["notes"])
    return normalized


def _cleanup_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for raw_entry in entries:
        entry = _normalize_entry(raw_entry)
        key = _entry_key(entry)
        if key in seen:
            stale.append({**entry, "stale_reason": "superseded"})
            continue
        seen.add(key)

        pid = entry.get("pid")
        assigned_port = int(entry.get("assigned_port", 0) or 0)
        protocol = str(entry.get("protocol", "tcp"))
        host = str(entry.get("host", "127.0.0.1"))
        started_at = _parse_iso(entry.get("started_at"))
        age_seconds = None
        if started_at is not None:
            age_seconds = max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())

        if pid is not None and not _is_process_alive(pid):
            stale.append({**entry, "stale_reason": "pid_not_running"})
            continue
        if assigned_port <= 0:
            stale.append({**entry, "stale_reason": "invalid_port"})
            continue
        if is_port_free(host, assigned_port, protocol=protocol):
            if age_seconds is not None and age_seconds <= DEFAULT_STARTUP_GRACE_SECONDS:
                active.append(entry)
                continue
            stale.append({**entry, "stale_reason": "unbound_reservation"})
            continue
        active.append(entry)

    return active, stale


def read_registry(registry_path: Path | str | None = None) -> dict[str, Any]:
    path = Path(registry_path) if registry_path is not None else default_master_ports_path()
    with _registry_lock(path):
        payload = _load_registry(path)
        active, stale = _cleanup_entries(payload.get("entries", []))
        if stale or payload.get("entries", []) != active:
            payload["entries"] = active
            payload["updated_at"] = _now_iso()
            _atomic_write_json(path, payload)
        return {
            "registry_path": str(path.resolve()),
            "schema_version": payload["schema_version"],
            "updated_at": payload["updated_at"],
            "entries": active,
            "stale_entries": stale,
        }


def _persist_entries(path: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "updated_at": _now_iso(),
        "entries": entries,
    }
    _atomic_write_json(path, payload)
    return payload


def record_reservation(
    *,
    app_id: str,
    service_name: str,
    protocol: str,
    host: str,
    requested_port: int,
    assigned_port: int,
    pid: int | None = None,
    started_at: str | None = None,
    notes: str | None = None,
    registry_path: Path | str | None = None,
) -> dict[str, Any]:
    path = Path(registry_path) if registry_path is not None else default_master_ports_path()
    with _registry_lock(path):
        payload = _load_registry(path)
        active, _ = _cleanup_entries(payload.get("entries", []))
        next_entries = [entry for entry in active if _entry_key(entry) != (app_id, service_name, protocol, host)]
        next_entries.append(
            _normalize_entry(
                {
                    "app_id": app_id,
                    "service_name": service_name,
                    "protocol": protocol,
                    "host": host,
                    "requested_port": requested_port,
                    "assigned_port": assigned_port,
                    "pid": pid,
                    "started_at": started_at or _now_iso(),
                    "notes": notes,
                }
            )
        )
        saved = _persist_entries(path, next_entries)
    return {
        "registry_path": str(path.resolve()),
        "entry": next(entry for entry in saved["entries"] if _entry_key(entry) == (app_id, service_name, protocol, host)),
    }


@dataclass(frozen=True)
class PortAllocation:
    app_id: str
    service_name: str
    protocol: str
    host: str
    requested_port: int
    assigned_port: int
    allowed_range: tuple[int, int]
    registry_path: str
    reused_existing: bool
    pid: int | None
    started_at: str
    notes: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id,
            "service_name": self.service_name,
            "protocol": self.protocol,
            "host": self.host,
            "requested_port": self.requested_port,
            "assigned_port": self.assigned_port,
            "allowed_range": list(self.allowed_range),
            "registry_path": self.registry_path,
            "reused_existing": self.reused_existing,
            "pid": self.pid,
            "started_at": self.started_at,
            "notes": self.notes,
        }


def allocate_port(
    *,
    app_id: str,
    service_name: str,
    preferred_port: int,
    host: str = "127.0.0.1",
    allowed_range: tuple[int, int] | None = None,
    strategy: str = "first_free",
    protocol: str = "tcp",
    pid: int | None = None,
    notes: str | None = None,
    registry_path: Path | str | None = None,
) -> PortAllocation:
    if strategy != "first_free":
        raise ValueError(f"Unsupported port allocation strategy: {strategy}")

    requested = int(preferred_port)
    start, end = allowed_range or default_port_range(requested)
    if requested < start or requested > end:
        raise ValueError(f"Preferred port {requested} is outside allowed range {start}-{end}.")

    path = Path(registry_path) if registry_path is not None else default_master_ports_path()
    protocol_name = str(protocol).lower() or "tcp"
    host_name = str(host or "127.0.0.1").strip() or "127.0.0.1"
    started_at = _now_iso()

    with _registry_lock(path):
        payload = _load_registry(path)
        active, stale = _cleanup_entries(payload.get("entries", []))
        previous = next(
            (
                entry
                for entry in active + stale
                if _entry_key(entry) == (app_id, service_name, protocol_name, host_name)
            ),
            None,
        )

        candidates: list[int] = []
        if previous is not None:
            previous_port = int(previous.get("assigned_port", 0) or 0)
            if start <= previous_port <= end:
                candidates.append(previous_port)
        if requested not in candidates:
            candidates.append(requested)
        for port in range(start, end + 1):
            if port not in candidates:
                candidates.append(port)

        assigned = None
        reused_existing = False
        occupied_ports = {
            int(entry.get("assigned_port", 0) or 0)
            for entry in active
            if str(entry.get("protocol", "tcp")) == protocol_name and str(entry.get("host", "127.0.0.1")) == host_name
        }
        for candidate in candidates:
            if candidate in occupied_ports:
                continue
            if is_port_free(host_name, candidate, protocol=protocol_name):
                assigned = candidate
                reused_existing = previous is not None and candidate == int(previous.get("assigned_port", 0) or 0)
                break
        if assigned is None:
            raise RuntimeError(f"No free {protocol_name.upper()} ports available in range {start}-{end} for {service_name}.")

        next_entries = [entry for entry in active if _entry_key(entry) != (app_id, service_name, protocol_name, host_name)]
        next_entries.append(
            _normalize_entry(
                {
                    "app_id": app_id,
                    "service_name": service_name,
                    "protocol": protocol_name,
                    "host": host_name,
                    "requested_port": requested,
                    "assigned_port": assigned,
                    "pid": pid,
                    "started_at": started_at,
                    "notes": notes,
                }
            )
        )
        _persist_entries(path, next_entries)

    return PortAllocation(
        app_id=app_id,
        service_name=service_name,
        protocol=protocol_name,
        host=host_name,
        requested_port=requested,
        assigned_port=assigned,
        allowed_range=(start, end),
        registry_path=str(path.resolve()),
        reused_existing=reused_existing,
        pid=pid,
        started_at=started_at,
        notes=notes,
    )


def release_reservation(
    *,
    app_id: str,
    service_name: str,
    host: str = "127.0.0.1",
    protocol: str = "tcp",
    registry_path: Path | str | None = None,
) -> None:
    path = Path(registry_path) if registry_path is not None else default_master_ports_path()
    protocol_name = str(protocol).lower() or "tcp"
    host_name = str(host or "127.0.0.1").strip() or "127.0.0.1"
    with _registry_lock(path):
        payload = _load_registry(path)
        active, _ = _cleanup_entries(payload.get("entries", []))
        next_entries = [entry for entry in active if _entry_key(entry) != (app_id, service_name, protocol_name, host_name)]
        _persist_entries(path, next_entries)


def get_service_reservation(
    *,
    app_id: str,
    service_name: str,
    host: str = "127.0.0.1",
    protocol: str = "tcp",
    registry_path: Path | str | None = None,
) -> dict[str, Any] | None:
    payload = read_registry(registry_path=registry_path)
    protocol_name = str(protocol).lower() or "tcp"
    host_name = str(host or "127.0.0.1").strip() or "127.0.0.1"
    for entry in payload["entries"]:
        if _entry_key(entry) == (app_id, service_name, protocol_name, host_name):
            return entry
    return None


def resolve_registered_port(
    *,
    app_id: str,
    service_name: str,
    preferred_port: int,
    host: str = "127.0.0.1",
    protocol: str = "tcp",
    registry_path: Path | str | None = None,
) -> int:
    entry = get_service_reservation(
        app_id=app_id,
        service_name=service_name,
        host=host,
        protocol=protocol,
        registry_path=registry_path,
    )
    if entry is None:
        return int(preferred_port)
    return int(entry.get("assigned_port", preferred_port) or preferred_port)


def resolve_base_url(
    *,
    base_url: str,
    app_id: str,
    service_name: str,
    default_port: int | None = None,
    protocol: str = "tcp",
    registry_path: Path | str | None = None,
) -> str:
    parsed = urlsplit(str(base_url).strip())
    host = (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    preferred_port = parsed.port or default_port or (443 if parsed.scheme == "https" else 80)
    assigned_port = resolve_registered_port(
        app_id=app_id,
        service_name=service_name,
        preferred_port=preferred_port,
        host=host,
        protocol=protocol,
        registry_path=registry_path,
    )
    netloc_host = host
    if ":" in host and not host.startswith("["):
        netloc_host = f"[{host}]"
    netloc = f"{netloc_host}:{assigned_port}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def repo_port_status(app_id: str, registry_path: Path | str | None = None) -> dict[str, Any]:
    payload = read_registry(registry_path=registry_path)
    entries = [entry for entry in payload["entries"] if str(entry.get("app_id", "")) == str(app_id)]
    stale_entries = [entry for entry in payload["stale_entries"] if str(entry.get("app_id", "")) == str(app_id)]

    seen_ports: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for entry in payload["entries"]:
        key = (
            str(entry.get("protocol", "tcp")),
            str(entry.get("host", "127.0.0.1")),
            int(entry.get("assigned_port", 0) or 0),
        )
        seen_ports.setdefault(key, []).append(entry)

    conflicts: list[dict[str, Any]] = []
    for (protocol, host, assigned_port), grouped in seen_ports.items():
        if len(grouped) <= 1:
            continue
        if not any(str(entry.get("app_id", "")) == str(app_id) for entry in grouped):
            continue
        conflicts.append(
            {
                "protocol": protocol,
                "host": host,
                "assigned_port": assigned_port,
                "entries": grouped,
            }
        )

    return {
        "app_id": app_id,
        "registry_path": payload["registry_path"],
        "entries": sorted(entries, key=lambda item: (str(item.get("service_name", "")), int(item.get("assigned_port", 0) or 0))),
        "stale_entries": sorted(stale_entries, key=lambda item: (str(item.get("service_name", "")), int(item.get("assigned_port", 0) or 0))),
        "conflicts": conflicts,
    }


def write_local_port_state(
    *,
    app_id: str,
    project_root: Path | str,
    registry_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> Path:
    resolved_output = Path(output_path) if output_path is not None else default_local_ports_path(project_root)
    payload = repo_port_status(app_id=app_id, registry_path=registry_path)
    payload["project_root"] = str(Path(project_root).resolve())
    payload["updated_at"] = _now_iso()
    _atomic_write_json(resolved_output, payload)
    return resolved_output