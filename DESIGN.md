No code, no fluff.

---

# Experiment Control & Record (ECR) – System Design

## 1. Purpose

Provide a laptop-local, web-based system to:

* run edge-AI field experiments on embedded Linux targets via SSH
* execute predefined remote commands
* collect results and logs
* maintain a time-ordered experiment record
* export a complete run bundle

Single user, offline-first, no software running on the target.

---

## 2. Scope and Constraints

### In scope

* Single run or staged mission per experiment
* Immutable timeline with optional admin edit/delete
* Metrics and operator notes
* SSH command execution and SCP pulls
* Folder-per-run storage with JSON manifests
* Zip export

### Out of scope (for now)

* Multi-user access
* Authentication/authorization
* Encryption
* Real-time streaming or raw data capture
* Mandatory HTML report generation

---

## 3. Deployment Model

* **Runs entirely on control laptop**
* Local-only web UI (`127.0.0.1`)
* Target accessed via SSH and SCP
* No agent or daemon on target

---

## 4. High-Level Architecture

Components (all on laptop):

1. Web UI
2. Python API server
3. Execution engine (SSH/SCP)
4. Filesystem run store
5. Target profile and command catalog

Target system:

* Existing OS + SSH
* No additional services required

---

## 5. Target Profiles

A target profile defines how a system is controlled.

Profile contents:

* SSH connection info (host, user, options)
* Named actions (Verify, Configure, Run, Stop, Export, etc.)
* Commands per action
* Optional parameters (UI form inputs)
* File pull definitions (paths, globs)
* Optional background collectors

Profiles are static configuration files (JSON/YAML).

---

## 6. Actions and Commands

Action types:

* Remote command execution
* Remote command with stdout/stderr capture
* File pull via SCP
* Composite actions (ordered steps)

Execution behavior:

* Timeouts per command
* Exit code recorded
* Failure does not abort the run unless explicitly configured
* All actions produce timeline events

---

## 7. Run Lifecycle

Typical flow:

1. Create run (select profile, set run mode, add notes)
2. Verify connection
3. Apply configuration / parameters
4. Start run
5. Optional background collectors run
6. Stop run
7. Pull artifacts
8. Export zip

---

## 8. Data Model

### Run

* `run_id`
* target profile ID
* run mode / parameters
* start/end timestamps
* operator notes
* status

### Event (append-only)

* timestamp (wall + monotonic)
* type (action_start, action_end, cmd_start, cmd_end, pull, note, edit, delete_marker)
* action/command reference
* parameters used
* exit code (if applicable)
* artifact references

### Artifact

* File path
* Type (command output, pulled file)
* Size and checksum
* Capture timestamp

---

## 9. Immutability Model

* Event log is append-only
* Edits are new “edit events”
* Deletion is either:

  * soft delete (delete_marker event)
  * hard delete (filesystem removal, admin action)

The timeline always preserves original actions.

---

## 10. Storage Layout

```
ecr_data/
  profiles/
    voxl.yaml
  runs/
    <run_id>/
      manifest.json
      events.ndjson
      cmd/
        <action>.stdout.txt
        <action>.stderr.txt
      artifacts/
        <pulled files>
      export/
        <run_id>.zip
```

---

## 11. Background Logging

* Optional, profile-defined commands
* Low frequency
* Strict timeouts
* Non-blocking
* Logged as periodic events

No interference with live operation.

---

## 12. Failure Handling

* SSH failure logged as event
* Commands can fail without stopping run
* Partial runs are valid
* Runs are recoverable and restartable
* Export always reflects captured state

---

## 13. Export

* Creates a zip of the run directory
* Includes manifest, event log, artifacts
* Manual trigger
* No automatic sync

---

## 14. Future Extensions (Optional)

* HTML report generator plugin
* Run comparison tooling
* Metrics visualization
* Time synchronization helpers
* Profile import/export

---

## 15. Non-Goals

* Real-time control loop integration
* Message bus assumptions
* Mandatory logging agents on target
* Centralized server or cloud dependency

---

