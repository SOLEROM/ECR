"""
Flask routes for ECR web interface.
"""

import os
import json
import markdown
from flask import (
    Blueprint, render_template, request, jsonify, 
    redirect, url_for, send_file, Response
)
from datetime import datetime

# Will be set by app.py
engine = None
profile_manager = None
storage_manager = None
app_root = None

web = Blueprint('web', __name__)


def init_routes(eng, prof_mgr, stor_mgr, root_dir):
    """Initialize routes with engine and managers."""
    global engine, profile_manager, storage_manager, app_root
    engine = eng
    profile_manager = prof_mgr
    storage_manager = stor_mgr
    app_root = root_dir


# ============ Dashboard ============

@web.route('/')
def dashboard():
    """Main dashboard showing all runs."""
    runs = storage_manager.list_runs()
    profiles = profile_manager.list_profiles()
    return render_template('dashboard.html', runs=runs, profiles=profiles)


# ============ Manual ============

@web.route('/manual')
def manual():
    """Display the configuration guide."""
    config_path = os.path.join(app_root, 'configuration_yaml.md')
    content = ""
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            md_content = f.read()
        content = markdown.markdown(md_content, extensions=['tables', 'fenced_code'])
    else:
        content = "<p>Configuration guide not found. Create a configuration_yaml.md file in the ECR root directory.</p>"
    return render_template('manual.html', content=content)


# ============ Profiles ============

@web.route('/profiles')
def profiles_list():
    """List all profiles."""
    profiles = []
    for pname in profile_manager.list_profiles():
        profile = profile_manager.load_profile(pname)
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
    
    template = '''name: new-target
description: "Description of the target device"

connection:
  host: "192.168.1.100"
  port: 22
  user: "root"
  key_file: "~/.ssh/id_rsa"
  timeout: 30

commands:
  local_check:
    description: "Check local environment"
    command: "echo 'Running on controller' && pwd"
    
  target_info:
    description: "Get target system info"
    command: "uname -a && uptime"
    run: target
    timeout: 30

background_collectors:
  system_stats:
    command: "uptime && free -m"
    run: target
    interval: 60
    timeout: 10
'''
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
        
        parameters = {}
        param_keys = request.form.getlist('param_key[]')
        param_values = request.form.getlist('param_value[]')
        for k, v in zip(param_keys, param_values):
            if k.strip():
                parameters[k.strip()] = v
        
        selected_commands = request.form.getlist('selected_commands[]')
        
        run_id = engine.create_run(
            profile_name=profile_name,
            name=run_name,
            parameters=parameters,
            selected_commands=selected_commands if selected_commands else None
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
    
    active_ctx = engine._active_runs.get(run_id)
    active_collectors = []
    if active_ctx:
        active_collectors = [
            cname for cname, c in active_ctx.collectors.items() if c.running
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
    pname = data.get('name')
    value = data.get('value', '')
    
    if not pname:
        return jsonify({'success': False, 'error': 'No parameter name specified'}), 400
    
    success = engine.set_parameter(run_id, pname, value)
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


@web.route('/runs/<run_id>/save')
def run_save(run_id):
    """Save/download a run as zip archive."""
    archive_path = engine.export_run(run_id)
    if not archive_path:
        return "Run not found", 404
    
    return send_file(
        archive_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=os.path.basename(archive_path)
    )


@web.route('/runs/<run_id>/export')
def run_export(run_id):
    """Export a run as HTML report."""
    ctx = engine.get_run_context(run_id)
    if not ctx:
        return "Run not found", 404
    
    events = engine.get_events(run_id)
    html = generate_html_report(ctx, events)
    
    report_path = os.path.join(ctx.storage.run_dir, f'report_{ctx.run_id}.html')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    return send_file(
        report_path,
        mimetype='text/html',
        as_attachment=True,
        download_name=f'ecr_report_{ctx.run_id}.html'
    )


def generate_html_report(ctx, events):
    """Generate a standalone HTML report for a run."""
    manifest = ctx.manifest
    
    # Build terminal-style event log (chronological order - old to new)
    event_html = []
    for e in events:  # events are already in chronological order
        if e['type'] == 'command_started':
            loc = e['data'].get('run_location', 'host')
            cmd = e['data'].get('command', e['data'].get('command_name', ''))
            event_html.append(f'''
                <div class="term-prompt">
                    <span class="term-time">{e["timestamp"][11:19]}</span>
                    <span class="term-loc" style="background:{'#d29922' if loc=='target' else '#238636'}">{loc}</span>
                    <span class="term-cmd">$ {cmd}</span>
                </div>''')
        elif e['type'] in ('command_completed', 'command_failed'):
            is_error = e['type'] == 'command_failed'
            stdout = e['data'].get('stdout', '')
            stderr = e['data'].get('stderr', '')
            exit_code = e['data'].get('exit_code', 0)
            duration = e['data'].get('duration', 0)
            event_html.append(f'''
                <div class="term-output {'term-error' if is_error else ''}">
                    {f'<pre>{stdout}</pre>' if stdout else ''}
                    {f'<pre class="stderr">{stderr}</pre>' if stderr else ''}
                    <div class="term-status">{'✗' if is_error else '✓'} exit {exit_code} ({duration:.2f}s)</div>
                </div>''')
        elif e['type'] == 'collector_output':
            event_html.append(f'''
                <div class="term-collector">
                    <span class="term-time">{e["timestamp"][11:19]}</span>
                    <span class="term-badge">{e['data'].get('collector', '')}</span>
                    <pre>{e['data'].get('stdout', '')}</pre>
                </div>''')
        elif e['type'] == 'note':
            event_html.append(f'''
                <div class="term-note">
                    <span class="term-time">{e["timestamp"][11:19]}</span>
                    📝 {e['data'].get('text', '')}
                </div>''')
        else:
            css = ''
            if 'started' in e['type']: css = 'info'
            elif 'completed' in e['type'] or 'pulled' in e['type']: css = 'success'
            elif 'failed' in e['type'] or 'error' in e['type']: css = 'error'
            detail = e['data'].get('command_name', '') or e['data'].get('error', '')
            event_html.append(f'''
                <div class="term-event term-{css}">
                    <span class="term-time">{e["timestamp"][11:19]}</span>
                    <span class="term-type">{e['type']}</span>
                    {f'<span class="term-detail">{detail}</span>' if detail else ''}
                </div>''')
    
    artifacts_html = "<p>No artifacts collected.</p>"
    if manifest.artifacts:
        artifacts_html = "<ul>" + "".join(
            f'<li>{a.get("local_path", "unknown")} (from {a.get("remote_path", "unknown")})</li>'
            for a in manifest.artifacts
        ) + "</ul>"
    
    params_html = "<p>No parameters set.</p>"
    if manifest.parameters:
        params_html = "<table><tr><th>Name</th><th>Value</th></tr>" + "".join(
            f'<tr><td>{k}</td><td><code>{v}</code></td></tr>'
            for k, v in manifest.parameters.items()
        ) + "</table>"
    
    status_class = 'success' if manifest.status == 'completed' else 'warning'
    completed_at = manifest.completed_at[:19].replace('T', ' ') if manifest.completed_at else 'N/A'
    
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>ECR Report - {manifest.name}</title>
    <style>
        :root {{ --bg:#0d1117; --bg-card:#161b22; --border:#30363d; --text:#e6edf3; --text-muted:#8b949e; --green:#3fb950; --red:#f85149; --blue:#58a6ff; }}
        * {{ box-sizing:border-box; margin:0; padding:0; }}
        body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif; background:var(--bg); color:var(--text); line-height:1.6; padding:24px; }}
        .container {{ max-width:1200px; margin:0 auto; }}
        h1 {{ font-size:28px; margin-bottom:8px; }}
        h2 {{ font-size:18px; margin:24px 0 12px; color:var(--blue); }}
        .subtitle {{ color:var(--text-muted); margin-bottom:24px; }}
        .card {{ background:var(--bg-card); border:1px solid var(--border); border-radius:8px; padding:16px; margin-bottom:16px; }}
        .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }}
        .badge {{ display:inline-block; padding:4px 8px; border-radius:12px; font-size:12px; }}
        .badge-success {{ background:rgba(63,185,80,0.2); color:var(--green); }}
        .badge-warning {{ background:rgba(210,153,34,0.2); color:#d29922; }}
        table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
        th,td {{ padding:8px 12px; text-align:left; border-bottom:1px solid var(--border); }}
        th {{ color:var(--text-muted); font-size:12px; text-transform:uppercase; }}
        code {{ background:var(--bg); padding:2px 6px; border-radius:4px; }}
        ul {{ padding-left:20px; }}
        .timestamp {{ font-size:12px; color:var(--text-muted); }}
        
        /* Terminal styles */
        .terminal {{ font-family:'SF Mono',Monaco,'Consolas',monospace; font-size:12px; background:#0d1117; border-radius:6px; padding:12px; }}
        .term-prompt {{ display:flex; align-items:center; gap:8px; color:#58a6ff; padding:4px 0; }}
        .term-time {{ color:#6e7681; font-size:11px; min-width:60px; }}
        .term-loc {{ color:#fff; padding:1px 6px; border-radius:3px; font-size:10px; text-transform:uppercase; }}
        .term-cmd {{ color:#c9d1d9; }}
        .term-output {{ margin-left:68px; padding:8px 12px; background:#161b22; border-left:3px solid var(--green); border-radius:0 4px 4px 0; margin-bottom:8px; }}
        .term-output.term-error {{ border-left-color:var(--red); }}
        .term-output pre {{ margin:0; color:#c9d1d9; white-space:pre-wrap; word-break:break-all; background:none; padding:0; }}
        .term-output pre.stderr {{ color:var(--red); }}
        .term-status {{ margin-top:8px; font-size:11px; color:#8b949e; }}
        .term-collector {{ display:flex; align-items:flex-start; gap:8px; padding:4px 0; color:#8b949e; }}
        .term-collector pre {{ margin:0; flex:1; color:#8b949e; background:none; padding:0; }}
        .term-badge {{ background:#6e40c9; color:#fff; padding:1px 6px; border-radius:3px; font-size:10px; }}
        .term-note {{ padding:8px 12px; background:#1c2128; border-left:3px solid var(--blue); border-radius:0 4px 4px 0; color:#c9d1d9; margin-bottom:8px; }}
        .term-event {{ display:flex; align-items:center; gap:8px; padding:4px 0; color:#8b949e; }}
        .term-type {{ font-weight:500; }}
        .term-info .term-type {{ color:var(--blue); }}
        .term-success .term-type {{ color:var(--green); }}
        .term-error .term-type {{ color:var(--red); }}
        .term-detail {{ color:#6e7681; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>ECR Experiment Report</h1>
        <p class="subtitle">{manifest.name} - {manifest.profile_name}</p>
        <div class="grid">
            <div class="card"><strong>Status</strong><br><span class="badge badge-{status_class}">{manifest.status.upper()}</span></div>
            <div class="card"><strong>Created</strong><br><span class="timestamp">{manifest.created_at[:19].replace("T", " ")}</span></div>
            <div class="card"><strong>Completed</strong><br><span class="timestamp">{completed_at}</span></div>
        </div>
        <h2>Parameters</h2>
        <div class="card">{params_html}</div>
        <h2>Artifacts</h2>
        <div class="card">{artifacts_html}</div>
        <h2>Event Log ({len(events)} events)</h2>
        <div class="card">
            <div class="terminal">{''.join(event_html)}</div>
        </div>
        <p class="timestamp" style="margin-top:24px; text-align:center;">Generated by ECR - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    </div>
</body>
</html>'''


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
