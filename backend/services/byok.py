"""Ephemeral in-memory store for buyer-supplied provider API keys (BYOK).

Keys are held strictly in process memory for the lifetime of an active job and
scrubbed immediately once the job finishes, fails, or is deleted. They are never
written to the database, disk, or logs.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_world_keys_by_project: dict[str, str] = {}
_stage_keys_by_project: dict[str, str] = {}


def set_project_world_key(project_id: str, api_key: str | None) -> None:
    cleaned = (api_key or "").strip()
    with _lock:
        if cleaned:
            _world_keys_by_project[project_id] = cleaned
        else:
            _world_keys_by_project.pop(project_id, None)


def get_project_world_key(project_id: str) -> str | None:
    with _lock:
        return _world_keys_by_project.get(project_id)


def pop_project_world_key(project_id: str) -> str | None:
    with _lock:
        return _world_keys_by_project.pop(project_id, None)


def set_project_stage_key(project_id: str, api_key: str | None) -> None:
    cleaned = (api_key or "").strip()
    with _lock:
        if cleaned:
            _stage_keys_by_project[project_id] = cleaned
        else:
            _stage_keys_by_project.pop(project_id, None)


def pop_project_stage_key(project_id: str) -> str | None:
    with _lock:
        return _stage_keys_by_project.pop(project_id, None)


def clear_project_keys(project_id: str) -> None:
    with _lock:
        _world_keys_by_project.pop(project_id, None)
        _stage_keys_by_project.pop(project_id, None)

_no_auto_3d_projects: set[str] = set()


def set_project_auto_3d(project_id: str, auto_3d: bool) -> None:
    with _lock:
        if auto_3d:
            _no_auto_3d_projects.discard(project_id)
        else:
            _no_auto_3d_projects.add(project_id)


def pop_project_auto_3d(project_id: str) -> bool:
    with _lock:
        if project_id in _no_auto_3d_projects:
            _no_auto_3d_projects.discard(project_id)
            return False
        return True
