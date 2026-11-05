"""Minimal file-based model registry (MLflow substitute, see DECISIONS.md D-005).

Tracks per run: experiment/model name, version, params, features, dataset hash, metrics, training date and the
SHA-256 of the artefact. Artefacts are pickled via joblib, which can execute code on load, so `load` refuses any
artefact whose SHA-256 does not match the registry entry. Only load registries you created.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import joblib


class ModelUnavailableError(RuntimeError):
    """Model artefact missing, unregistered, or failed integrity verification."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "registry.json"

    def _index(self) -> list[dict]:
        return json.loads(self.index_path.read_text()) if self.index_path.exists() else []

    def save(self, name: str, model, *, params: dict, features: list[str], metrics: dict, dataset_hash: str,
             data_kind: str, extra: dict | None = None) -> dict:
        idx = self._index()
        version = 1 + max([m["version"] for m in idx if m["name"] == name], default=0)
        d = self.root / name / f"v{version}"
        d.mkdir(parents=True, exist_ok=True)
        art = d / "model.joblib"
        tmp = art.with_suffix(".tmp")
        joblib.dump(model, tmp)
        os.replace(tmp, art)
        meta = dict(name=name, version=version, run_id=f"{name}-v{version}",
                    created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    params=params, features=features, dataset_hash=dataset_hash, data_kind=data_kind,
                    metrics=metrics, artefact=str(art.relative_to(self.root)), sha256=_sha256(art),
                    extra=extra or {})
        _atomic_json(d / "meta.json", meta)
        idx.append(meta)
        _atomic_json(self.index_path, idx)
        return meta

    def latest_meta(self, name: str) -> dict:
        cands = [m for m in self._index() if m["name"] == name]
        if not cands:
            raise ModelUnavailableError(f"no registered model named '{name}'")
        return max(cands, key=lambda m: m["version"])

    def load(self, name: str, version: int | None = None):
        metas = [m for m in self._index() if m["name"] == name and (version is None or m["version"] == version)]
        if not metas:
            raise ModelUnavailableError(f"model '{name}' (version={version}) is not registered")
        meta = max(metas, key=lambda m: m["version"])
        art = self.root / meta["artefact"]
        if not art.exists():
            raise ModelUnavailableError(f"artefact missing for {meta['run_id']}: {art.name}")
        if _sha256(art) != meta["sha256"]:
            raise ModelUnavailableError(f"integrity check failed for {meta['run_id']} (sha256 mismatch)")
        return joblib.load(art), meta

    def list(self) -> list[dict]:
        return self._index()
