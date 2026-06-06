"""Reproducible static-site build orchestration for the classics library."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from pipeline import to_pretext

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output"
PRETEXT_DIR = ROOT / "pretext"
STATE_PATH = OUTPUT_DIR / ".pipeline-build-state.json"
STATE_VERSION = 1
DIGEST_VERSION = b"books-b6-build-v1"


def run(command: Sequence[str], *, cwd: Path = ROOT) -> None:
    printable = " ".join(command)
    print(f"+ {printable}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_for_target(target: to_pretext.WorkTarget) -> str:
    digest = hashlib.sha256()
    digest.update(DIGEST_VERSION)
    digest.update(target.publication.encode("utf-8"))
    digest.update(target.normalized_path.read_bytes())
    digest.update((ROOT / "pipeline" / "to_pretext.py").read_bytes())
    return digest.hexdigest()


def load_state() -> dict[str, Any]:
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": STATE_VERSION, "targets": {}}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION or not isinstance(raw.get("targets"), dict):
        return {"version": STATE_VERSION, "targets": {}}
    return raw


def write_state(targets: Sequence[to_pretext.WorkTarget]) -> None:
    payload = {
        "version": STATE_VERSION,
        "generatedAt": int(time.time()),
        "targets": {target.work_id: digest_for_target(target) for target in targets},
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def output_is_present(target: to_pretext.WorkTarget) -> bool:
    output_dir = OUTPUT_DIR / target.output_rel
    return (output_dir / "index.html").exists() and any(output_dir.glob("*.html"))


def write_changed_sources(targets: Sequence[to_pretext.WorkTarget], state: dict[str, Any]) -> list[to_pretext.WorkTarget]:
    prior = state.get("targets", {})
    changed: list[to_pretext.WorkTarget] = []
    for target in targets:
        digest = digest_for_target(target)
        if prior.get(target.work_id) == digest and output_is_present(target):
            continue
        changed.append(target)
        work = to_pretext.load_json(target.normalized_path)
        tree = to_pretext.work_to_pretext_tree(work)
        to_pretext.write_tree(tree, to_pretext.SOURCE_DIR / target.source_rel)

    to_pretext.write_publications()
    to_pretext.write_project(list(targets))
    return changed


def generate_pretext_sources(*, changed_only: bool) -> tuple[list[to_pretext.WorkTarget], list[to_pretext.WorkTarget]]:
    targets = to_pretext.iter_work_targets()
    if not changed_only:
        generated = to_pretext.generate_all()
        return generated, generated

    changed = write_changed_sources(targets, load_state())
    return targets, changed


def build_pretext_targets(targets: Sequence[to_pretext.WorkTarget]) -> None:
    for target in targets:
        run(["uv", "run", "pretext", "build", "--clean", target.target_name], cwd=PRETEXT_DIR)


def crawl_args(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "-m", "pipeline.crawl"]
    if args.all:
        command.append("--all")
    else:
        command.append("--marquee")
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="crawl and build the full classics corpus; reserved for scale-up runs")
    parser.add_argument("--limit", type=int, help="limit selected works for CI smoke builds")
    parser.add_argument("--changed-only", action="store_true", help="skip data refresh and rebuild only works whose normalized source changed")
    parser.add_argument("--skip-pagefind", action="store_true", help="skip search indexing")
    args = parser.parse_args(argv)

    if args.changed_only:
        print("changed-only: reusing existing normalized data and author assets", flush=True)
    else:
        run(crawl_args(args))
        run([sys.executable, "-m", "pipeline.portraits"])
        run([sys.executable, "-m", "pipeline.covers"])

    targets, pretext_targets = generate_pretext_sources(changed_only=args.changed_only)
    if pretext_targets:
        build_pretext_targets(pretext_targets)
    else:
        print("changed-only: no PreTeXt targets changed", flush=True)

    run(["npx", "vite", "build"])
    run(["uv", "run", "python", "-m", "pipeline.inject"])
    if not args.skip_pagefind:
        run(["npx", "pagefind", "--site", "output"])

    write_state(targets)
    print(
        json.dumps(
            {
                "targets": len(targets),
                "pretextBuilt": len(pretext_targets),
                "changedOnly": args.changed_only,
                "pagefind": not args.skip_pagefind,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
