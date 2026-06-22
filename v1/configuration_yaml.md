# ECR Profile Configuration Guide

This document explains all available options for configuring ECR target profiles.

## Profile Structure

```yaml
name: profile-name
description: "Human-readable description"

connection:
  # SSH connection settings
  
commands:
  # Available commands to run
  
background_collectors:
  # Periodic data collection commands
```

---

## Connection Settings

Configure how ECR connects to the target device via SSH.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `host` | string | required | IP address or hostname of the target |
| `port` | integer | 22 | SSH port number |
| `user` | string | "root" | SSH username |
| `key_file` | string | null | Path to SSH private key (e.g., `~/.ssh/id_rsa`) |
| `password` | string | null | SSH password (use key_file instead when possible) |
| `timeout` | integer | 30 | Connection timeout in seconds |

### Example

```yaml
connection:
  host: "192.168.1.100"
  port: 22
  user: "nvidia"
  key_file: "~/.ssh/jetson_key"
  timeout: 30
```

---

## Commands

Define commands that can be executed on-demand during an experiment.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `description` | string | "" | Human-readable description of what the command does |
| `command` | string | required | The shell command to execute |
| `run` | string | "host" | Where to run: `host` (controller) or `target` (via SSH) |
| `timeout` | integer | 60 | Maximum execution time in seconds |
| `artifacts` | list | [] | Files to pull from target after command completes |

### Run Location

- **`run: host`** (default) - Command runs on your local machine (the controller)
- **`run: target`** - Command runs on the remote device via SSH

### Parameter Substitution

Use `{parameter_name}` placeholders in commands. These are replaced with values set during run creation or execution.

```yaml
commands:
  run_inference:
    description: "Run model inference"
    command: "python3 inference.py --model {model_name} --input {input_file}"
    run: target
    timeout: 300
    artifacts:
      - "/tmp/results_{model_name}.json"
```

### Artifacts

List of remote file paths to download after the command completes. Only applies to `run: target` commands. Supports parameter substitution.

### Example

```yaml
commands:
  # Runs on controller (default)
  prepare_data:
    description: "Prepare input data locally"
    command: "python3 scripts/prepare.py --output /tmp/input.bin"
    timeout: 120
    
  # Runs on target device
  collect_metrics:
    description: "Collect GPU metrics from device"
    command: "tegrastats --interval 1000 --logfile /tmp/gpu.log &"
    run: target
    timeout: 10
    
  # Runs on target with artifacts
  run_benchmark:
    description: "Run performance benchmark"
    command: "cd /opt/benchmark && ./run.sh --iterations {iterations}"
    run: target
    timeout: 600
    artifacts:
      - "/opt/benchmark/results.json"
      - "/tmp/gpu.log"
```

---

## Background Collectors

Define commands that run periodically throughout the experiment to collect monitoring data.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `command` | string | required | The shell command to execute |
| `run` | string | "target" | Where to run: `host` or `target` |
| `interval` | integer | 60 | Seconds between executions |
| `timeout` | integer | 10 | Maximum execution time per collection |

### Example

```yaml
background_collectors:
  # Monitor target system resources
  system_stats:
    command: "uptime && free -m && df -h /"
    run: target
    interval: 60
    timeout: 10
    
  # Monitor GPU temperature
  gpu_temp:
    command: "cat /sys/class/thermal/thermal_zone*/temp"
    run: target
    interval: 30
    timeout: 5
    
  # Monitor controller network
  network_check:
    command: "ping -c 1 {target_ip} && curl -s http://{target_ip}:8080/health"
    run: host
    interval: 120
    timeout: 15
```

---

## Complete Example

```yaml
name: jetson-inference
description: "Jetson Nano ML inference experiment profile"

connection:
  host: "192.168.1.50"
  port: 22
  user: "nvidia"
  key_file: "~/.ssh/id_rsa"
  timeout: 30

commands:
  # Local preparation
  prepare_model:
    description: "Convert model to TensorRT format"
    command: "python3 convert_model.py --input {model_path} --output /tmp/model.trt"
    timeout: 300
    
  upload_model:
    description: "Upload model to device"
    command: "scp /tmp/model.trt nvidia@192.168.1.50:/opt/models/"
    timeout: 60
    
  # Remote execution
  system_info:
    description: "Get device system information"
    command: "uname -a && cat /etc/nv_tegra_release && free -h"
    run: target
    timeout: 30
    
  start_inference:
    description: "Start inference server"
    command: "cd /opt/inference && python3 server.py --model /opt/models/model.trt --port 8080"
    run: target
    timeout: 10
    
  run_benchmark:
    description: "Run inference benchmark"
    command: "python3 /opt/benchmark/run.py --iterations {iterations} --batch {batch_size}"
    run: target
    timeout: 600
    artifacts:
      - "/opt/benchmark/results.json"
      - "/opt/benchmark/latency.csv"
      
  cleanup:
    description: "Clean up temporary files"
    command: "rm -f /tmp/*.trt /opt/benchmark/results.json"
    run: target
    timeout: 30

background_collectors:
  gpu_metrics:
    command: "tegrastats --interval 1000 | head -1"
    run: target
    interval: 30
    timeout: 5
    
  memory_usage:
    command: "free -m | grep Mem"
    run: target
    interval: 60
    timeout: 5
    
  inference_health:
    command: "curl -s http://localhost:8080/health || echo 'server down'"
    run: target
    interval: 15
    timeout: 5
```

---

## Tips

1. **Use `run: host` for**:
   - Data preparation and post-processing
   - File transfers (scp, rsync)
   - Local analysis scripts
   - Coordination logic

2. **Use `run: target` for**:
   - Device-specific commands
   - Collecting device metrics
   - Running experiments on the device
   - Anything that needs device hardware

3. **Artifacts**:
   - Only work with `run: target` commands
   - Are automatically pulled after command completion
   - Support parameter substitution in paths

4. **Background collectors**:
   - Keep them lightweight (short timeout)
   - Use reasonable intervals (don't overload the device)
   - Good for monitoring system health during long experiments

5. **Parameters**:
   - Define placeholders like `{name}` in commands
   - Set values when creating a run or during execution
   - Useful for varying experiment configurations
