# -*- mode: python ; coding: utf-8 -*-
# ECR PyInstaller spec file
# Build with: pyinstaller ecr.spec

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all data files
datas = [
    ('web/templates', 'web/templates'),
    ('web/static', 'web/static'),
    ('profiles', 'profiles'),
    ('configuration_yaml.md', '.'),
    ('robot.md', '.'),
    ('README.md', '.'),
]

# Hidden imports for Flask and dependencies
hiddenimports = [
    'flask',
    'jinja2',
    'markupsafe',
    'werkzeug',
    'click',
    'itsdangerous',
    'paramiko',
    'cryptography',
    'bcrypt',
    'nacl',
    'yaml',
    'markdown',
    'markdown.extensions.tables',
    'markdown.extensions.fenced_code',
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ecr',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
