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

from flask import Flask

from core import ProfileManager, StorageManager, ExperimentEngine
from web.routes import web, init_routes


def create_app(profiles_dir: str = None, runs_dir: str = None) -> Flask:
    """Create and configure the Flask application."""
    
    # Determine base directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Set default directories
    if profiles_dir is None:
        profiles_dir = os.path.join(base_dir, 'profiles')
    if runs_dir is None:
        runs_dir = os.path.join(base_dir, 'runs')
    
    # Ensure directories exist
    os.makedirs(profiles_dir, exist_ok=True)
    os.makedirs(runs_dir, exist_ok=True)
    
    # Initialize managers
    profile_manager = ProfileManager(profiles_dir)
    storage_manager = StorageManager(runs_dir)
    engine = ExperimentEngine(storage_manager, profile_manager)
    
    # Create Flask app
    app = Flask(__name__, 
                template_folder=os.path.join(base_dir, 'web', 'templates'),
                static_folder=os.path.join(base_dir, 'web', 'static'))
    
    # Configuration
    app.config['SECRET_KEY'] = os.urandom(24)
    app.config['PROFILES_DIR'] = profiles_dir
    app.config['RUNS_DIR'] = runs_dir
    
    # Initialize routes with managers
    init_routes(engine, profile_manager, storage_manager)
    
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
    python app.py                          # Start with defaults (localhost:5000)
    python app.py --port 8080              # Use custom port
    python app.py --profiles-dir ~/ecr/profiles --runs-dir ~/ecr/runs
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
