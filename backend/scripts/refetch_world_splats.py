"""One-off: re-cache finished worlds at their lowest-resolution splat.

Worlds cached before the 100k change hold the ~30 MB full-res splat. This
clears ``worlds/{id}/{source}.spz`` and downloads the smallest ``spz_urls``
entry instead. It only reads existing worlds from World Labs (GET) and
downloads files: it never creates a world and never spends credits.

Dry run by default. Run from ``backend/`` with the service's environment:

    python -m scripts.refetch_world_splats --all            # report only
    python -m scripts.refetch_world_splats --all --apply    # do it
    python -m scripts.refetch_world_splats --project <id> --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from config import get_settings
from db import get_project, init_db, list_projects
from services.world_assets import refetch_lowest_splat
from storage import init_storage


async def run(project_ids: list[str] | None, apply: bool, sources: list[str]) -> int:
    settings = get_settings()
    init_db(settings)
    init_storage(settings)
    if not settings.worldlabs_api_key:
        print("WORLDLABS_API_KEY is not set; nothing to do.", file=sys.stderr)
        return 2
    projects = [get_project(pid) for pid in project_ids] if project_ids else list_projects(admin=True)
    failures = 0
    for project in projects:
        if project is None:
            print("missing project")
            failures += 1
            continue
        for source in sources:
            status = await refetch_lowest_splat(project, source, apply=apply)
            print(f"{project.id} {source}: {status}")
            failures += status.startswith("error")
    if not apply:
        print("Dry run. Re-run with --apply to refetch.")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", action="append", help="project id (repeatable)")
    target.add_argument("--all", action="store_true", help="every project with a world")
    parser.add_argument("--apply", action="store_true", help="actually clear and re-download (default: dry run)")
    parser.add_argument("--source", choices=["original", "staged", "both"], default="both")
    args = parser.parse_args(argv)
    sources = ["original", "staged"] if args.source == "both" else [args.source]
    return asyncio.run(run(args.project, args.apply, sources))


if __name__ == "__main__":
    raise SystemExit(main())
