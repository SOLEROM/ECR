# ECR System Architecture - Machine Readable Summary

## Purpose
ECR (Experiment Control & Record) is a controller-only experiment orchestration framework for edge AI field experiments on embedded Linux targets.

## Core Concept
- All execution happens on the CONTROLLER (laptop/workstation)
- Target devices are accessed ONLY via SSH/SCP when explicitly requested
- No agents or services installed on target devices
- Experiments are recorded as immutable event streams

## Execution Model
Commands in profiles have two execution modes:
- `run: host` (DEFAULT) - Command executes on the controller machine
- `run: target` - Command executes on remote target via SSH

This allows mixing local processing (data analysis, file manipulation) with remote operations (device control, data collection).

## Directory Structure
```
ecr/
├── app.py                 # Flask application entry point
├── core/
│   ├── engine.py          # Run orchestration, action execution
│   ├── ssh_client.py      # SSH/SCP wrapper with reconnection
│   ├── events.py          # Append-only JSONL event stream
│   ├── storage.py         # Run directory and manifest management
│   └── profiles.py        # YAML profile loading, parameter substitution
├── web/
│   ├── routes.py          # REST API and web routes
│   └── templates/         # Jinja2 HTML templates
├── profiles/              # Target profile YAML files
└── runs/                  # Experiment run data (one folder per run)
```

## Key Data Structures

### Profile (YAML)
Defines a target device and available commands:
- connection: SSH parameters (host, port, user, key_file)
- commands: Named command definitions with `run: host|target`
- background_collectors: Periodic monitoring commands

### Run Directory
```
runs/{run_id}/
├── manifest.json          # Metadata, parameters, artifact list
├── events.jsonl           # Append-only event timeline
├── profile_snapshot.yaml  # Frozen profile copy
├── artifacts/             # Collected files
└── logs/                  # Background collector output
```

### Event Stream (JSONL)
Each line is a JSON object:
```json
{"seq": 1, "timestamp": "ISO8601", "event_type": "string", "data": {}}
```
Event types: run_*, command_*, artifact_*, collector_*, connection_*, note, error

## API Endpoints

### Run Control
- POST /api/runs/{id}/start - Start/resume run
- POST /api/runs/{id}/pause - Pause run
- POST /api/runs/{id}/complete - Complete run
- DELETE /api/runs/{id} - Delete run

### Execution
- POST /api/runs/{id}/command - Execute single command by name
- POST /api/runs/{id}/collector/start - Start background collector
- POST /api/runs/{id}/collector/stop - Stop background collector

### Data
- GET /api/runs/{id}/events - Get event stream
- POST /api/runs/{id}/parameter - Set parameter
- POST /api/runs/{id}/note - Add operator note
- GET /runs/{id}/export - Download ZIP archive

## Parameter Substitution
Commands support `{param_name}` placeholders replaced at execution time.
Parameters set via UI or API before/during run.

## Resilience Features
- SSH auto-reconnection with configurable retry
- Partial failure tolerance (failed commands logged, run continues)
- Run pause/resume across sessions
- Connection state tracked in event stream

## Extension Points
- Add new profiles in profiles/ directory
- Custom background collectors for device-specific monitoring
- Artifact collection from any remote path
- Event stream can be parsed by external tools

## Constraints
- Single operator assumed (no auth)
- Localhost-bound web interface
- No real-time streaming of command output (poll-based)
- Artifacts pulled after command completion (not streamed)
