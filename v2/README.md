# ccFleet

This repository is **two things**:

### 1 · The template application — [`README_template.md`](README_template.md)
**ccFleet**, a self-contained web app for operating a fleet of remote nodes over SSH:
bring the fleet **up**, **watch** its health, and bring it **down**, with **everything
recorded**. Built on *config over code* — the operator changes the fleet, command
profiles, buttons and connectivity checks from the browser, no source edits.

```bash
./run.sh --mock        # simulated fleet, no hardware → http://127.0.0.1:5000
```

### 2 · The Rebuild system (the Compiler) — [`README_rebuild.md`](README_rebuild.md)
A staged pipeline that turns a short **wish list** into a complete, *verified* fork of the
template — and **rebuilds** it whenever the wish list changes. Three editable stages
(*dream → params → sub-parts → app*), then an acceptance gate (`pytest` + `--mock` +
`--dry-run`) that must pass before a build counts.

```bash
./compile.sh new myapp                                # fork the template
./compile.sh --app apps/myapp --from dream --to app   # build a verified app
```

---

**Which do I read?** Operating or extending the app itself →
[`README_template.md`](README_template.md) (brief: [`CLAUDE.md`](CLAUDE.md)). Generating a
new app from a wish list, or working on the build system →
[`README_rebuild.md`](README_rebuild.md) (brief: [`CLAUDE_rebuild.md`](CLAUDE_rebuild.md),
rationale: [`systemPlan.md`](systemPlan.md)).

> **Two engineering briefs, two parts.** `CLAUDE.md` = the template app · `CLAUDE_rebuild.md`
> = the Rebuild system. The served folders split the same way: `design/` = part-1
> subsystem docs (rendered on the **Help** page); `system/` = a part-2 wish list.
