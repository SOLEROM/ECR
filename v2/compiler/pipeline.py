"""
Pipeline orchestration — run a stage **range** safely (R7).

Maps ``--from/--to/--only`` to the transforms (distill / expand / build), enforces the
rules that protect your edits, threads the build.yaml status book, and runs the
acceptance gate at the end of a build.

Rules (systemPlan §6):
  - **Range-only** — a run touches only the transforms in ``from..to``.
  - **Approved lock** — a transform whose output artifact is ``approved`` is not
    overwritten without ``--force`` (this also makes an upstream re-run *stop* before it
    cascades over a downstream artifact you blessed).
  - **Staleness** — running a transform marks the downstream stages ``stale``.
  - **Verified** — a build only "succeeds" if the gate is green; otherwise non-zero.
"""

import os
import sys
from dataclasses import dataclass
from typing import List, Optional

from . import STAGES, STATUS_APPROVED, STATUS_DRAFT, STATUS_STALE
from . import spec, stages, build as buildmod, llm
from .gate import run_gate
from .manifest import Manifest

# transform = (name, input stage, output stage)
_TRANSFORMS = [
    ("distill", "dream", "params"),
    ("expand", "params", "subparts"),
    ("build", "subparts", "app"),
]
_IDX = {s: i for i, s in enumerate(STAGES)}


@dataclass
class RunResult:
    ok: bool
    ran: List[str]
    messages: List[str]
    gate_log: Optional[str] = None


def _select(from_stage: str, to_stage: str, only: Optional[str]) -> List[tuple]:
    if only:
        if only not in _IDX:
            raise ValueError(f"unknown stage {only!r}; pick one of {STAGES[1:]}")
        return [t for t in _TRANSFORMS if t[2] == only]
    fi, ti = _IDX[from_stage], _IDX[to_stage]
    # run transforms that consume `from` onward and produce up to `to`
    return [t for t in _TRANSFORMS if _IDX[t[1]] >= fi and _IDX[t[2]] <= ti]


def run(app_dir: str, *, from_stage: str = "dream", to_stage: str = "app",
        only: Optional[str] = None, force: bool = False,
        llm_pref: Optional[str] = None, python: Optional[str] = None) -> RunResult:
    system_dir = os.path.join(app_dir, "system")
    if not os.path.isdir(system_dir):
        raise spec.SpecError(f"no system/ folder in {app_dir} — fork the template first "
                             "(`compile.sh new <name>`)")
    book = spec.load_build(system_dir)
    provider = llm.resolve(llm_pref or book.llm)
    python = python or sys.executable

    plan = _select(from_stage, to_stage, only)
    ran: List[str] = []
    msgs: List[str] = [f"llm backend: {provider}"]
    gate_log = None

    for name, in_stage, out_stage in plan:
        # approved lock — don't clobber a blessed artifact (also halts an upstream
        # re-run before it cascades over downstream edits)
        if book.status_of(out_stage) == STATUS_APPROVED and not force:
            msgs.append(f"halt: {out_stage} is approved — re-review or pass --force "
                        f"(skipped {name} and everything after)")
            break

        if name == "distill":
            dream = spec.read_dream(system_dir)
            params, notes = stages.distill(dream, provider)
            spec.write_params(system_dir, params)
            msgs += [f"distill → {spec.PARAMS}"] + [f"  · {n}" for n in notes]
        elif name == "expand":
            params = spec.read_params(system_dir)
            dream = spec.read_dream(system_dir) if os.path.exists(
                os.path.join(system_dir, spec.DREAM)) else ""
            sub, notes = stages.expand(params, dream, provider)
            _write_subparts(system_dir, sub)
            msgs += [f"expand → {spec.SUBPARTS_DIR}/ ({len(sub)} files)"] + \
                    [f"  · {n}" for n in notes]
        elif name == "build":
            params = spec.read_params(system_dir)
            subparts = spec.read_subparts(system_dir)
            manifest, report = buildmod.build(app_dir, params, subparts)
            _emit_report(system_dir, report)
            ok, gate_log = run_gate(app_dir, python)
            manifest.save(meta={"app": book.app, "from": from_stage, "to": to_stage,
                                "gate": "pass" if ok else "fail"})
            # the manifest + report themselves are owned bookkeeping
            msgs.append(f"build → {len(manifest.owned)} owned file(s)")
            msgs.append("acceptance gate:")
            msgs.append(gate_log)
            book.set_status("app", STATUS_DRAFT)
            _mark_downstream(book, out_stage)
            spec.save_build(book)
            if not ok:
                msgs.append("BUILD NOT VERIFIED — gate is red; fix the spec and rebuild.")
                return RunResult(False, ran + ["build"], msgs, gate_log)

        ran.append(name)
        book.set_status(out_stage, STATUS_DRAFT)
        _mark_downstream(book, out_stage)

    spec.save_build(book)
    # a run that STOPS at an editable checkpoint (only/params/subparts) reminds you to review
    if to_stage != "app" or only in ("params", "subparts"):
        msgs.append(f"stopped at editable checkpoint — review, then mark approved in "
                    f"{spec.BUILD}")
    return RunResult(True, ran, msgs, gate_log)


def _write_subparts(system_dir: str, sub: dict):
    for stem, data in sub.items():
        spec.write_subpart(system_dir, stem, data,
                           header=f"# layer3.subparts/{stem}.yaml — drafted by `expand`; "
                                  "EDIT add/remove/overrides + each item's mode:.")


def _emit_report(system_dir: str, report: List[str]):
    path = os.path.join(system_dir, "compile-report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n")


def _mark_downstream(book: spec.BuildBook, out_stage: str):
    """Editing/regenerating a stage makes everything after it stale until re-run."""
    oi = _IDX[out_stage]
    for s, i in _IDX.items():
        if i > oi and s in book.stages:
            if book.status_of(s) != STATUS_APPROVED:
                book.set_status(s, STATUS_STALE)
