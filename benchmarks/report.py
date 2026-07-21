"""Build a Markdown comparison report from a benchmarks/runs/<ts>/ directory
produced by compare_solweig.sh.

Usage: uv run python benchmarks/report.py benchmarks/runs/<ts>
"""

import json
import re
import sys
from pathlib import Path

STAGES = {
    "solweig_gpu": "solweig-gpu (pipeline/02_run_solweig.py)",
    "solweig_umep": "solweig / umep-rust (pipeline/02b_run_solweig_umep.py)",
}


def read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def parse_gnu_time(text: str) -> dict:
    out = {}
    patterns = {
        "elapsed": r"Elapsed \(wall clock\) time.*: (.+)",
        "max_rss_kb": r"Maximum resident set size \(kbytes\): (\d+)",
        "user_s": r"User time \(seconds\): ([\d.]+)",
        "sys_s": r"System time \(seconds\): ([\d.]+)",
        "cpu_pct": r"Percent of CPU this job got: (.+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            out[key] = m.group(1)
    return out


def parse_internal_timings(stage: str, log_text: str, repo_root: Path) -> dict:
    out = {}
    if stage == "solweig_gpu":
        for m in re.finditer(r"solweig_gpu total=([\d.]+)s \(N_WORKERS=(\d+)\)", log_text):
            out["reported_total_s"] = float(m.group(1))
            out["n_workers"] = int(m.group(2))
        m = re.search(r"Using (\d+) parallel workers", log_text)
        if m:
            out["tile_workers"] = int(m.group(1))
    elif stage == "solweig_umep":
        m = re.search(r"backend=(\S+)", log_text)
        if m:
            out["backend"] = m.group(1)
        json_path = repo_root / "outputs" / "umep" / "timing_umep.json"
        if json_path.exists():
            try:
                out["timing_json"] = json.loads(json_path.read_text())
            except json.JSONDecodeError:
                pass
    return out


def fmt_mb(kb_str):
    try:
        return f"{int(kb_str) / 1024:.0f} MB"
    except (TypeError, ValueError):
        return "n/a"


def main():
    if len(sys.argv) != 2:
        print("usage: report.py <run_dir>", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    repo_root = Path(__file__).resolve().parent.parent

    rows = []
    for stage, label in STAGES.items():
        duration_f = run_dir / f"{stage}.duration_s"
        status_f = run_dir / f"{stage}.status"
        log_text = read(run_dir / f"{stage}.log")
        time_text = read(run_dir / f"{stage}.time")

        wall_s = read(duration_f).strip() or "n/a"
        status = read(status_f).strip() or "n/a"
        gtime = parse_gnu_time(time_text)
        internal = parse_internal_timings(stage, log_text, repo_root)

        rows.append(
            {
                "stage": stage,
                "label": label,
                "wall_s": wall_s,
                "status": status,
                "gtime": gtime,
                "internal": internal,
            }
        )

    lines = []
    lines.append(f"# SOLWEIG implementation comparison — {run_dir.name}\n")
    lines.append(
        "Sequential run (never concurrent) to avoid GPU/RAM contention with "
        "other processes on this machine.\n"
    )

    lines.append("## Summary\n")
    lines.append("| | " + " | ".join(r["label"] for r in rows) + " |")
    lines.append("|---|" + "---|" * len(rows))
    lines.append(
        "| exit status (0=ok) | " + " | ".join(r["status"] for r in rows) + " |"
    )
    lines.append(
        "| wall clock (bash, s) | " + " | ".join(r["wall_s"] for r in rows) + " |"
    )
    lines.append(
        "| GNU time elapsed | "
        + " | ".join(r["gtime"].get("elapsed", "n/a") for r in rows)
        + " |"
    )
    lines.append(
        "| peak RSS | "
        + " | ".join(fmt_mb(r["gtime"].get("max_rss_kb")) for r in rows)
        + " |"
    )
    lines.append(
        "| CPU usage | "
        + " | ".join(r["gtime"].get("cpu_pct", "n/a") for r in rows)
        + " |"
    )

    if all(r["status"] == "0" for r in rows):
        try:
            wall_vals = [float(r["wall_s"]) for r in rows]
            if all(v > 0 for v in wall_vals):
                ratio = max(wall_vals) / min(wall_vals)
                faster = rows[wall_vals.index(min(wall_vals))]["label"]
                lines.append(f"\n**{faster} was {ratio:.2f}x faster (wall clock).**\n")
        except (ValueError, ZeroDivisionError):
            pass
    else:
        lines.append(
            "\n**Speed comparison skipped: at least one stage exited with a "
            "non-zero status (see status row above).**\n"
        )

    lines.append("\n## Details\n")
    for r in rows:
        lines.append(f"### {r['label']}\n")
        lines.append(f"- status: `{r['status']}`, wall clock: `{r['wall_s']}s`")
        for k, v in r["gtime"].items():
            lines.append(f"- {k}: `{v}`")
        for k, v in r["internal"].items():
            if k == "timing_json":
                lines.append(f"- internal timing json:\n```json\n{json.dumps(v, indent=2)}\n```")
            else:
                lines.append(f"- {k}: `{v}`")
        lines.append("")

    lines.append("## Resource snapshots\n")
    for tag in [
        "start",
        "before_solweig_gpu",
        "after_solweig_gpu",
        "before_solweig_umep",
        "after_solweig_umep",
    ]:
        mem = read(run_dir / f"mem_{tag}.txt").strip()
        gpu = read(run_dir / f"gpu_{tag}.txt").strip()
        if not mem and not gpu:
            continue
        lines.append(f"**{tag}**\n```\n{mem}\n\n{gpu}\n```\n")

    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines))
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
