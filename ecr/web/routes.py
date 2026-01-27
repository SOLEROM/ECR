"""
Flask routes for ECR web interface.
"""

import os
import json
from flask import (
    Blueprint, render_template, request, jsonify, 
    redirect, url_for, send_file, Response
)
from datetime import datetime

# Will be set by app.py
engine = None
profile_manager = None
storage_manager = None

web = Blueprint('web', __name__)


def init_routes(eng, prof_mgr, stor_mgr):
    """Initialize routes with engine and managers."""
    global engine, profile_manager, storage_manager
    engine = eng
    profile_manager = prof_mgr
    storage_manager = stor_mgr


# ============ Dashboard ============

@web.route('/')
def dashboard():
    """Main dashboard showing all runs."""
    runs = storage_manager.list_runs()
    profiles = profile_manager.list_profiles()
    return render_template('dashboard.html', runs=runs, profiles=profiles)


# ============ Profiles ============

@web.route('/profiles')
def profiles_list():
    """List all profiles."""
    profiles = []
    for name in profile_manager.list_profiles():
        profile = profile_manager.load_profile(name)
        if profile:
            profiles.append({
                'name': profile.name,
                'description': profile.description,
                'host': profile.connection.host,
                'commands_count': len(profile.commands),
                'collectors_count': len(profile.background_collectors)
            })
    return render_template('profiles.html', profiles=profiles)


@web.route('/profiles/<name>')
def profile_view(name):
    """View a single profile."""
    profile = profile_manager.load_profile(name)
    if not profile:
        return "Profile not found", 404
    return render_template('profile_view.html', profile=profile)


@web.route('/profiles/<name>/edit', methods=['GET', 'POST'])
def profile_edit(name):
    """Edit a profile."""
    if request.method == 'POST':
        yaml_content = request.form.get('yaml_content', '')
        filepath = os.path.join(profile_manager.profiles_dir, name + '.yaml')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(yaml_content)
        return redirect(url_for('web.profile_view', name=name))
    
    profile = profile_manager.load_profile(name)
    if not profile:
        return "Profile not found", 404
    
    with open(profile.filepath, 'r', encoding='utf-8') as f:
        yaml_content = f.read()
    
    return render_template('profile_edit.html', profile=profile, yaml_content=yaml_content)


@web.route('/profiles/new', methods=['GET', 'POST'])
def profile_new():
    """Create a new profile."""
    if request.method == 'POST':
        name = request.form.get('name', 'new-profile')
        yaml_content = request.form.get('yaml_content', '')
        filepath = os.path.join(profile_manager.profiles_dir, name + '.yaml')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(yaml_content)
        return redirect(url_for('web.profile_view', name=name))
    
    # Default template
    template = """name: new-target
description: "Description of the target device"

connection:
  host: "192.168.1.100"
  port: 22
  user: "root"
  key_file: "~/.ssh/id_rsa"
  timeout: 30

commands:
  # Commands run on HOST (controller) by default
  local_check:
    description: "Check local environment"
    command: "echo 'Running on controller' && pwd"
    # run: host  # default, runs on controller
    
  # Commands with run: target execute via SSH
  target_info:
    description: "Get target system info"
    command: "uname -a && uptime"
    run: target
    timeout: 30
    
  collect_logs:
    description: "Collect logs from target"
    command: "cat /var/log/syslog | tail -100"
    run: target
    artifacts:
      - "/tmp/collected_log.txt"
    timeout: 60

background_collectors:
  system_stats:
    command: "uptime && free -m"
    run: target
    interval: 60
    timeout: 10
"""
    return render_template('profile_edit.html', profile=None, yaml_content=template)


@web.route('/api/profiles/<name>', methods=['DELETE'])
def profile_delete(name):
    """Delete a profile."""
    if profile_manager.delete_profile(name):
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Profile not found'}), 404


# ============ Runs ============

@web.route('/runs/new', methods=['GET', 'POST'])
def run_new():
    """Create a new run."""
    if request.method == 'POST':
        data = request.form
        profile_name = data.get('profile_name')
        run_name = data.get('name') or None
        
        # Parse parameters
        parameters = {}
        param_keys = request.form.getlist('param_key[]')
        param_values = request.form.getlist('param_value[]')
        for k, v in zip(param_keys, param_values):
            if k.strip():
                parameters[k.strip()] = v
        
        run_id = engine.create_run(
            profile_name=profile_name,
            name=run_name,
            parameters=parameters
        )
        
        if run_id:
            return redirect(url_for('web.run_view', run_id=run_id))
        return "Failed to create run", 400
    
    profiles = profile_manager.list_profiles()
    selected_profile = request.args.get('profile')
    profile_data = None
    if selected_profile:
        profile = profile_manager.load_profile(selected_profile)
        if profile:
            profile_data = profile.to_dict()
            profile_data['commands_list'] = list(profile.commands.keys())
    
    return render_template('run_new.html', 
                          profiles=profiles, 
                          selected_profile=selected_profile,
                          profile_data=profile_data)


@web.route('/runs/<run_id>')
def run_view(run_id):
    """View a run."""
    ctx = engine.get_run_context(run_id)
    if not ctx:
        return "Run not found", 404
    
    events = engine.get_events(run_id)
    
    # Get active collectors
    active_ctx = engine._active_runs.get(run_id)
    active_collectors = []
    if active_ctx:
        active_collectors = [
            name for name, c in active_ctx.collectors.items() if c.running
        ]
    
    return render_template('run_view.html', 
                          ctx=ctx, 
                          events=events,
                          active_collectors=active_collectors)


@web.route('/api/runs/<run_id>/start', methods=['POST'])
def run_start(run_id):
    """Start or resume a run."""
    success = engine.start_run(run_id)
    return jsonify({'success': success})


@web.route('/api/runs/<run_id>/pause', methods=['POST'])
def run_pause(run_id):
    """Pause a run."""
    success = engine.pause_run(run_id)
    return jsonify({'success': success})


@web.route('/api/runs/<run_id>/complete', methods=['POST'])
def run_complete(run_id):
    """Complete a run."""
    success = engine.complete_run(run_id)
    return jsonify({'success': success})


@web.route('/api/runs/<run_id>/command', methods=['POST'])
def run_execute_command(run_id):
    """Execute a command."""
    data = request.json or {}
    command_name = data.get('command')
    
    if not command_name:
        return jsonify({'success': False, 'error': 'No command specified'}), 400
    
    result = engine.execute_command(run_id, command_name)
    return jsonify(result)


@web.route('/api/runs/<run_id>/parameter', methods=['POST'])
def run_set_parameter(run_id):
    """Set a parameter."""
    data = request.json or {}
    name = data.get('name')
    value = data.get('value', '')
    
    if not name:
        return jsonify({'success': False, 'error': 'No parameter name specified'}), 400
    
    success = engine.set_parameter(run_id, name, value)
    return jsonify({'success': success})


@web.route('/api/runs/<run_id>/collector/start', methods=['POST'])
def run_start_collector(run_id):
    """Start a background collector."""
    data = request.json or {}
    collector_name = data.get('collector')
    
    if not collector_name:
        return jsonify({'success': False, 'error': 'No collector specified'}), 400
    
    success = engine.start_collector(run_id, collector_name)
    return jsonify({'success': success})


@web.route('/api/runs/<run_id>/collector/stop', methods=['POST'])
def run_stop_collector(run_id):
    """Stop a background collector."""
    data = request.json or {}
    collector_name = data.get('collector')
    
    if not collector_name:
        return jsonify({'success': False, 'error': 'No collector specified'}), 400
    
    success = engine.stop_collector(run_id, collector_name)
    return jsonify({'success': success})


@web.route('/api/runs/<run_id>/note', methods=['POST'])
def run_add_note(run_id):
    """Add a note to a run."""
    data = request.json or {}
    note = data.get('note', '')
    
    success = engine.add_note(run_id, note)
    return jsonify({'success': success})


@web.route('/api/runs/<run_id>/events')
def run_events(run_id):
    """Get events for a run (for polling)."""
    after_seq = int(request.args.get('after', 0))
    events = engine.get_events(run_id, after_seq)
    return jsonify({'events': events})


@web.route('/api/runs/<run_id>/events/stream')
def run_events_stream(run_id):
    """Server-sent events stream for real-time updates."""
    def generate():
        last_seq = int(request.args.get('after', 0))
        while True:
            events = engine.get_events(run_id, last_seq)
            for event in events:
                last_seq = event['seq']
                yield f"data: {json.dumps(event)}\n\n"
            
            import time
            time.sleep(0.5)
    
    return Response(generate(), mimetype='text/event-stream')


@web.route('/runs/<run_id>/export')
def run_export(run_id):
    """Export a run as zip archive."""
    archive_path = engine.export_run(run_id)
    if not archive_path:
        return "Run not found", 404
    
    return send_file(
        archive_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=os.path.basename(archive_path)
    )


@web.route('/api/runs/<run_id>', methods=['DELETE'])
def run_delete(run_id):
    """Delete a run."""
    success = engine.delete_run(run_id)
    return jsonify({'success': success})


@web.route('/runs/<run_id>/artifacts/<path:artifact_path>')
def run_artifact(run_id, artifact_path):
    """Download an artifact."""
    storage = storage_manager.get_run(run_id)
    if not storage:
        return "Run not found", 404
    
    full_path = os.path.join(storage.run_dir, artifact_path)
    if not os.path.exists(full_path):
        return "Artifact not found", 404
    
    return send_file(full_path, as_attachment=True)
