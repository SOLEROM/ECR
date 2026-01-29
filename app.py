#!/usr/bin/env python3
"""
ECR - Experiment Control & Record

A controller-only experiment orchestration and recording framework
for edge AI field experiments on embedded Linux targets.

Usage:
    python app.py [--host HOST] [--port PORT] [--profiles-dir DIR] [--runs-dir DIR]

The web interface will be available at http://localhost:5000
"""

import argparse
import os
import sys
import shutil

from flask import Flask

from core import ProfileManager, StorageManager, ExperimentEngine
from web.routes import web, init_routes


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_base_dir():
    """Get the base directory for bundled resources."""
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_working_dir():
    """Get the working directory for user data (profiles, runs)."""
    # When running as binary, use current working directory
    # When running as script, use script directory
    if hasattr(sys, '_MEIPASS'):
        return os.getcwd()
    return os.path.dirname(os.path.abspath(__file__))


def setup_user_directories(profiles_dir, runs_dir):
    """Setup user directories, copying sample profiles if needed."""
    os.makedirs(profiles_dir, exist_ok=True)
    os.makedirs(runs_dir, exist_ok=True)
    
    # If profiles dir is empty and we have bundled samples, copy them
    if not os.listdir(profiles_dir):
        bundled_profiles = get_resource_path('profiles')
        if os.path.exists(bundled_profiles):
            for f in os.listdir(bundled_profiles):
                if f.endswith(('.yaml', '.yml')):
                    src = os.path.join(bundled_profiles, f)
                    dst = os.path.join(profiles_dir, f)
                    shutil.copy2(src, dst)
                    print(f"  Copied sample profile: {f}")


def create_app(profiles_dir: str = None, runs_dir: str = None) -> Flask:
    """Create and configure the Flask application."""
    
    # Determine directories
    base_dir = get_base_dir()
    working_dir = get_working_dir()
    
    # Set default directories (in working directory for user data)
    if profiles_dir is None:
        profiles_dir = os.path.join(working_dir, 'profiles')
    if runs_dir is None:
        runs_dir = os.path.join(working_dir, 'runs')
    
    # Setup user directories
    setup_user_directories(profiles_dir, runs_dir)
    
    # Initialize managers
    profile_manager = ProfileManager(profiles_dir)
    storage_manager = StorageManager(runs_dir)
    engine = ExperimentEngine(storage_manager, profile_manager)
    
    # Create Flask app with bundled templates/static
    app = Flask(__name__, 
                template_folder=get_resource_path(os.path.join('web', 'templates')),
                static_folder=get_resource_path(os.path.join('web', 'static')))
    
    # Configuration
    app.config['SECRET_KEY'] = os.urandom(24)
    app.config['PROFILES_DIR'] = profiles_dir
    app.config['RUNS_DIR'] = runs_dir
    
    # Initialize routes with managers (use working_dir for config files access)
    init_routes(engine, profile_manager, storage_manager, base_dir)
    
    # Register blueprint
    app.register_blueprint(web)
    
    # Add managers to app context
    app.engine = engine
    app.profile_manager = profile_manager
    app.storage_manager = storage_manager
    
    return app


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='ECR - Experiment Control & Record',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ecr                                     # Start with defaults (localhost:5000)
    ecr --port 8080                         # Use custom port
    ecr --host 0.0.0.0                      # Allow external connections
    ecr --profiles-dir ~/ecr/profiles --runs-dir ~/ecr/runs
        """
    )
    
    parser.add_argument(
        '--host', 
        default='127.0.0.1',
        help='Host to bind to (default: 127.0.0.1 for localhost only)'
    )
    parser.add_argument(
        '--port', 
        type=int, 
        default=5000,
        help='Port to bind to (default: 5000)'
    )
    parser.add_argument(
        '--profiles-dir',
        help='Directory for target profiles (default: ./profiles)'
    )
    parser.add_argument(
        '--runs-dir',
        help='Directory for run data (default: ./runs)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )
    
    args = parser.parse_args()
    
    # Create app
    app = create_app(
        profiles_dir=args.profiles_dir,
        runs_dir=args.runs_dir
    )
    
    # Print startup info
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   ECR - Experiment Control & Record                          ║
║                                                               ║
║   Web Interface: http://{args.host}:{args.port:<5}                        ║
║                                                               ║
║   Profiles: {app.config['PROFILES_DIR']:<43} ║
║   Runs:     {app.config['RUNS_DIR']:<43} ║
║                                                               ║
║   Press Ctrl+C to stop                                        ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
""")
    
    # Run the server
    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug,
            threaded=True
        )
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)


if __name__ == '__main__':
    main()
