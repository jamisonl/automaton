# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path
import site

block_cipher = None

# Debug: Find litellm tokenizer files
print("=== DEBUGGING TOKENIZER PATHS (CLI) ===")
try:
    import litellm
    litellm_path = Path(litellm.__file__).parent
    tokenizer_path = litellm_path / 'litellm_core_utils' / 'tokenizers'
    print(f"litellm module path: {litellm_path}")
    print(f"tokenizer directory: {tokenizer_path}")
    print(f"tokenizer exists: {tokenizer_path.exists()}")
    if tokenizer_path.exists():
        print(f"tokenizer files: {list(tokenizer_path.glob('*.json'))}")
except Exception as e:
    print(f"Error importing litellm: {e}")
print("=== END DEBUG (CLI) ===")

# Add src directory to path for PyInstaller
# SPECPATH is a PyInstaller global: the directory containing this spec file.
SPEC_DIR = Path(SPECPATH).resolve()
APP_DIR = SPEC_DIR.parent.resolve() # Project root is parent of 'build' directory
SRC_DIR = APP_DIR / 'src'
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(APP_DIR)) # For main.py itself, if it were at project root

a = Analysis(
    ['../src/main.py'], # Path relative to this spec file (build directory)
    pathex=[str(APP_DIR), str(SRC_DIR)], # Add project root and src to pathex
    binaries=[],
    datas=[
        # Include litellm tokenizer files - use runtime detection
        *[
            (str(tokenizer_path), 'litellm/litellm_core_utils/tokenizers')
            for tokenizer_path in [
                # Runtime detection using litellm module path
                Path(litellm.__file__).parent / 'litellm_core_utils' / 'tokenizers'
            ]
            if tokenizer_path.exists()
        ],
        # Fallback: Include individual tokenizer files using detected path
        *[(str(f), 'litellm/litellm_core_utils/tokenizers') 
          for f in (Path(litellm.__file__).parent / 'litellm_core_utils' / 'tokenizers').glob('*.json') 
          if f.is_file()],
    ],
    hiddenimports=[
        'pydantic_core._pydantic_core',
        'dspy',
        'langchain',
        'langchain_google_genai',
        'google',               # Broader google namespace
        'google.api',           # Common for google APIs
        'google.api_core',      # Often needed
        'google.auth',          # For authentication
        'google.generativeai',  # Keep specific one too
        'aiosqlite',
        'git',
        'dotenv',
        'rich',
        'pathspec',
        'openai',
        # For langchain
        'langchain_core',
        'langchain_community',
        'sqlalchemy',
        'charset_normalizer',
        'asyncio',
        # For PyGithub
        'github',
        'jwt',
        'requests',
        'urllib3',
        'idna',
        'certifi',
        'jsonpatch',
        'jsonpointer',
        # For tiktoken fix
        'tiktoken_ext.openai_public',
        'tiktoken_ext',
        'tiktoken',
        'tiktoken.core',
        'tiktoken.registry',
        'tiktoken._tiktoken',
        # For core modules
        'core.events',
        'core.coordination',
        'core.logger',
        'core.config',
        'src.core.events',
        'src.core.coordination',
        'src.core.logger',
        'src.core.config',
        # For agents modules
        'agents.base',
        'agents.master_coordinator',
        'agents.feature_analyzer',
        'agents.pr_generator',
        'src.agents.base',
        'src.agents.master_coordinator',
        'src.agents.feature_analyzer',
        'src.agents.pr_generator',
        # For io_layer modules
        'io_layer.system_controller',
        'io_layer.task_manager',
        'io_layer.progress_publisher',
        'io_layer.file_browser',
        'io_layer.native_file_browser',
        'src.io_layer.system_controller',
        'src.io_layer.task_manager',
        'src.io_layer.progress_publisher',
        'src.io_layer.file_browser',
        'src.io_layer.native_file_browser',
        # For ui modules
        'ui.settings_dialog',
        'src.ui.settings_dialog',
    ],
    hookspath=['build'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['.env'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='automaton',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True, # CLI application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='../assets/icon.ico' # CLI apps usually don't need an icon
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='automaton-cli',
)
