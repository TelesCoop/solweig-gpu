"""Run identity: a content hash of everything that determines a SOLWEIG result.

The output directory is named <scenario>_<run_id>, where run_id is the first 8 hex
digits of the sha256 of the run's `config` (params, met file and input rasters, all
by content hash). Each such directory holds a run_config.json that can be
re-verified: same config -> same id, any changed input or param -> different id.
"""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

CONFIG_NAME = "run_config.json"
ID_LEN = 8
INPUT_FILES = [
    "Building_DSM.tif",
    "DEM.tif",
    "Trees.tif",
    "Landcover.tif",
    "cache_buildings.geojson",
]


def sha256_file(path, chunk=8 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def _file_entry(path):
    path = Path(path)
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def _git(*args):
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        ).stdout
    except Exception:
        return None


def build_config(scenario, met_file, params, inputs_dir="inputs"):
    """params: JSON-serialisable dict of every setting that affects the result."""
    try:
        version = metadata.version("solweig-gpu")
    except metadata.PackageNotFoundError:
        version = None
    inputs_dir = Path(inputs_dir)
    return {
        "scenario": scenario,
        "met_file": _file_entry(met_file),
        "inputs": {n: _file_entry(inputs_dir / n) for n in INPUT_FILES},
        "params": params,
        "solweig_gpu_version": version,
    }


def run_id(config):
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:ID_LEN]


def run_dir_name(config):
    return f"{config['scenario']}_{run_id(config)}"


def provenance():
    """Code state, recorded for information only (not part of the id)."""
    diff = _git("diff", "HEAD") or ""
    return {
        "git_commit": (_git("rev-parse", "HEAD") or "").strip() or None,
        "git_dirty": bool(diff.strip()),
        "git_diff_sha256": hashlib.sha256(diff.encode()).hexdigest() if diff else None,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_run_config(out_dir, config):
    out_dir = Path(out_dir)
    record = {
        "run_id": run_id(config),
        "config": config,
        "provenance": provenance(),
        "history": [],
    }
    (out_dir / CONFIG_NAME).write_text(json.dumps(record, indent=2) + "\n")
    return record


def read_run_config(out_dir):
    """Load a run's record and check its id matches both its content and its dir name."""
    out_dir = Path(out_dir)
    record = json.loads((out_dir / CONFIG_NAME).read_text())
    rid = run_id(record["config"])
    if record["run_id"] != rid:
        raise ValueError(f"{out_dir}: run_config.json was edited (id {rid} != {record['run_id']})")
    if out_dir.name != f"{record['config']['scenario']}_{rid}":
        raise ValueError(f"{out_dir}: directory name does not match run id {rid}")
    return record


def log_step(out_dir, step):
    out_dir = Path(out_dir)
    record = read_run_config(out_dir)
    record["history"].append(
        {"step": step, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "git_commit": (_git("rev-parse", "HEAD") or "").strip() or None}
    )
    (out_dir / CONFIG_NAME).write_text(json.dumps(record, indent=2) + "\n")


def check_met_file(record):
    """Raise if the met file on disk is not the one the run was made with."""
    m = record["config"]["met_file"]
    if sha256_file(m["path"]) != m["sha256"]:
        raise ValueError(f"{m['path']} changed since run {record['run_id']}")


def iter_runs(outputs="outputs"):
    """Yield (dir, record) for every verified run dir; skip others with a warning."""
    outputs = Path(outputs)
    for d in sorted(p for p in outputs.iterdir() if p.is_dir()):
        if not (d / CONFIG_NAME).exists():
            print(f"skipping {d}: no {CONFIG_NAME}")
            continue
        yield d, read_run_config(d)
