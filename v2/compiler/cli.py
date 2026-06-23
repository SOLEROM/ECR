"""
Compiler CLI — ``compile.sh`` dispatches here (``python -m compiler``).

Subcommands:
  (default)            run the pipeline:  --from/--to/--only over an app's system/
  new <name>           fork the template into apps/<name>/
  scaffold <part>      dump a catalog part's default as an editable Layer-3 file
  check                manifest drift check (did a human edit generated output?)
  status               print the build.yaml stage statuses
"""

import argparse
import os
import sys

import yaml

from . import STAGES, fork, pipeline, spec, catalog
from .manifest import Manifest

TEMPLATE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_dir(args) -> str:
    return os.path.abspath(args.app or os.getcwd())


def cmd_new(args) -> int:
    name = args.name
    dest = os.path.join(TEMPLATE_DIR, "apps", name)
    try:
        fork.fork(TEMPLATE_DIR, dest, force=args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"forked template → {os.path.relpath(dest)}")
    print(f"next: edit {os.path.relpath(dest)}/system/layer1.dream.md, then "
          f"`compile.sh --app {os.path.relpath(dest)} --from dream --to app`")
    return 0


def cmd_scaffold(args) -> int:
    if not args.part:
        print(catalog.index())
        return 0
    try:
        desc, body, mode = catalog.part_default(args.part)
    except KeyError:
        print(f"error: unknown part {args.part!r}\n\n{catalog.index()}", file=sys.stderr)
        return 1
    app_dir = _app_dir(args)
    system_dir = os.path.join(app_dir, "system")
    header = f"# layer3.subparts/{args.part}.yaml — {desc} (mode default: {mode})"
    path = spec.write_subpart(system_dir, args.part, body, header=header)
    print(f"scaffolded {os.path.relpath(path)}  (edit it, then rebuild)")
    return 0


def cmd_check(args) -> int:
    app_dir = _app_dir(args)
    man = Manifest.load(app_dir)
    if not man.owned:
        print("no manifest found — build the app first")
        return 1
    drift = man.check()
    if not drift:
        print(f"OK — all {len(man.owned)} generated file(s) match the manifest "
              "(no hand-edits).")
        return 0
    print("WARNING — generated files edited by hand (spec is the source of truth, D3):")
    for d in drift:
        print(f"  · {d}")
    print("→ move the change into system/ and rebuild, or re-run build to re-own.")
    return 2


def cmd_status(args) -> int:
    app_dir = _app_dir(args)
    book = spec.load_build(os.path.join(app_dir, "system"))
    print(f"app: {book.app}    llm: {book.llm}")
    for stage in STAGES[1:]:
        st = book.stages.get(stage)
        if st:
            print(f"  {stage:<10} {st.status:<9} ({st.artifact})")
    return 0


def cmd_run(args) -> int:
    app_dir = _app_dir(args)
    try:
        res = pipeline.run(app_dir, from_stage=args.from_stage, to_stage=args.to_stage,
                           only=args.only, force=args.force, llm_pref=args.llm)
    except (spec.SpecError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for m in res.messages:
        print(m)
    if not res.ok:
        print("\nFAILED.", file=sys.stderr)
        return 1
    print(f"\nOK — ran: {', '.join(res.ran) or '(nothing)'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="compile.sh",
                                description="ccFleet Compiler — wish list → working app")
    p.add_argument("--app", help="the app dir (default: cwd; must contain system/)")
    p.add_argument("--from", dest="from_stage", choices=STAGES, default="dream",
                   help="start the pipeline from this stage (default: dream)")
    p.add_argument("--to", dest="to_stage", choices=STAGES, default="app",
                   help="run the pipeline up to this stage (default: app)")
    p.add_argument("--only", choices=STAGES[1:],
                   help="run only the transform that produces this stage, then stop")
    p.add_argument("--llm", choices=("auto", "claude", "offline"),
                   help="LLM backend for distill/expand (default: build.yaml/auto)")
    p.add_argument("--force", action="store_true",
                   help="overwrite an approved artifact / existing fork")
    sub = p.add_subparsers(dest="cmd")

    pn = sub.add_parser("new", help="fork the template into apps/<name>/")
    pn.add_argument("name")
    pn.add_argument("--force", action="store_true")

    ps = sub.add_parser("scaffold", help="dump a catalog part default as a Layer-3 file")
    ps.add_argument("part", nargs="?")

    sub.add_parser("check", help="manifest drift check")
    sub.add_parser("status", help="print build.yaml stage statuses")

    args = p.parse_args(argv)
    if args.cmd == "new":
        return cmd_new(args)
    if args.cmd == "scaffold":
        return cmd_scaffold(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "status":
        return cmd_status(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
