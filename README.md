# ECR - Experiment Control & Record

A controller-only experiment orchestration and recording framework for edge AI field experiments on embedded Linux targets.

## fast start

```
# Install dependencies
pip install -r requirements.txt


# option1: Run from src 
> python app.py

# option2: Build and run as binary
	> ./build.sh
  run:
	./dist/ecr
	./dist/ecr --host 0.0.0.0 --port 8080 --profiles-dir /path/to/profiles
```


## Overview

ECR executes entirely on a control laptop and communicates with target devices exclusively via SSH for command execution and SCP for artifact retrieval. No resident agents, services, or libraries are required on the target beyond standard SSH access.

### Key Features

- **Controller-only architecture**: All logic runs on your laptop
- **SSH/SCP communication**: Standard protocols, no target installation required
- **Append-only event stream**: Immutable experiment timeline and provenance
- **Profile-based configuration**: Declarative target and action definitions
- **Background collectors**: Optional low-frequency monitoring
- **Resilient execution**: Handles partial failures and connectivity loss
- **Export to ZIP**: Complete, self-describing experimental datasets

## Installation

```bash
# Clone or copy the ECR directory
cd ecr

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py
```

The web interface will be available at http://localhost:5000

## Quick Start

1. **Create a Target Profile**
   - Go to Profiles → New Profile
   - Define connection parameters and actions
   - Save the profile

2. **Create a Run**
   - Go to New Run
   - Select your profile
   - Add any parameters
   - Optionally define stages
   - Click Create Run

3. **Execute the Run**
   - Click Start to establish SSH connection
   - Execute actions individually or by stage
   - Toggle background collectors as needed
   - Add notes during the experiment
   - Click Complete when finished

4. **Export Results**
   - Click Export to download a ZIP archive
   - Archive contains all events, artifacts, and metadata

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Controller Laptop                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    ECR System                            │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │   │
│  │  │  Web UI  │  │  Core    │  │  SSH/SCP │  │ Storage │ │   │
│  │  │ (Flask)  │◄─┤  Engine  │◄─┤  Client  │  │ Manager │ │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │ SSH/SCP                          │
└──────────────────────────────┼──────────────────────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │   Embedded Target   │
                    │   (Linux + SSH)     │
                    └─────────────────────┘
```

## Profile Configuration

Profiles are YAML files that define target connections and available actions:

```yaml
name: my-device
description: "My edge AI device"

connection:
  host: "192.168.1.100"
  port: 22
  user: "root"
  key_file: "~/.ssh/id_rsa"
  timeout: 30

actions:
  run_inference:
    description: "Run ML inference"
    commands:
      - "cd /opt/model && ./infer.sh {model_name}"
    artifacts:
      - "/tmp/results.json"
    timeout: 300

background_collectors:
  system_stats:
    command: "uptime && free -m"
    interval: 60
    timeout: 10
```

### Parameter Substitution

Use `{param_name}` in commands and artifact paths. Parameters are set when creating a run or dynamically during execution.

## Run Directory Structure

Each run creates a self-contained directory:

```
runs/
└── 2026-01-27_143052_my-experiment/
    ├── manifest.json           # Run metadata & artifact index
    ├── events.jsonl            # Append-only event stream
    ├── profile_snapshot.yaml   # Frozen copy of profile used
    ├── artifacts/              # Collected output files
    │   ├── results.json
    │   └── metrics.csv
    └── logs/                   # Background collector logs
```

## Event Types

The event stream records all experiment activity:

- **Run lifecycle**: `run_created`, `run_started`, `run_paused`, `run_resumed`, `run_completed`, `run_interrupted`
- **Stage lifecycle**: `stage_started`, `stage_completed`
- **Action execution**: `action_started`, `action_completed`, `action_failed`
- **Command execution**: `command_started`, `command_output`, `command_completed`, `command_failed`
- **Artifacts**: `artifact_pull_started`, `artifact_pulled`, `artifact_pull_failed`
- **Collectors**: `collector_started`, `collector_stopped`, `collector_output`, `collector_error`
- **Connection**: `connection_established`, `connection_lost`, `connection_retry`
- **Operator**: `note`, `edit`, `parameter_set`

## Command Line Options

```bash
python app.py [OPTIONS]

Options:
  --host HOST           Host to bind to (default: 127.0.0.1)
  --port PORT           Port to bind to (default: 5000)
  --profiles-dir DIR    Directory for target profiles
  --runs-dir DIR        Directory for run data
  --debug               Enable debug mode
```

## API Endpoints

ECR provides a REST API for programmatic control:

- `GET /api/runs/<run_id>/events` - Get event stream
- `POST /api/runs/<run_id>/start` - Start/resume run
- `POST /api/runs/<run_id>/pause` - Pause run
- `POST /api/runs/<run_id>/complete` - Complete run
- `POST /api/runs/<run_id>/action` - Execute action
- `POST /api/runs/<run_id>/collector/start` - Start collector
- `POST /api/runs/<run_id>/collector/stop` - Stop collector
- `POST /api/runs/<run_id>/parameter` - Set parameter
- `POST /api/runs/<run_id>/note` - Add note
- `DELETE /api/runs/<run_id>` - Delete run

## Security Notes

- Web interface is bound to localhost by default
- Single trusted operator assumed
- No authentication or encryption included by design
- SSH keys should be properly secured on the controller

## License

MIT License - See LICENSE file for details.
