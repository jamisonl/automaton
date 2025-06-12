# Automaton

A multi-agent system that intelligently chunks your features into pull requests.

## Installation

### 📦 Download Pre-built Binaries (Recommended)

**Download the latest release from [GitHub Releases](https://github.com/jamisonl/automaton/releases)**

#### Windows

- Download `automaton-gui-v0.0.1-windows.zip` for GUI application
- Download `automaton-cli-v0.0.1-windows.zip` for command line tool

#### macOS

- Download `automaton-gui-v0.0.1-macos.zip` for GUI application (.app bundle)
- Download `automaton-cli-v0.0.1-macos.zip` for command line tool

#### Linux

- Download `automaton-gui-v0.0.1-linux.tar.gz` for GUI application
- Download `automaton-cli-v0.0.1-linux.tar.gz` for command line tool

**Extract and run** - No Python installation required!

### 🛠️ Build from Source

If you prefer to build from source:

```bash
git clone https://github.com/jamisonl/automaton.git
uv sync
uv run python build/build.py all  # Creates executables in dist/
```

## Overview

This system uses multiple coordinated agents to:

1. Analyze feature specifications
2. Plan logical chunks for implementation
3. Generate code changes
4. Create and review pull requests
5. Coordinate merging in dependency order

## Architecture

```
                           User Input
                          "Add feature X"
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            Coordinator Agent                        │
│  • Orchestrates workflow                                            │
│  • Plans chunk dependencies                                         │
│  • Coordinates merging                                              │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
          ┌───────────────────────┐
          │     Event Bus         │
          │  • Publishes events   │
          │  • Routes messages    │
          │  • Tracks progress    │
          └───────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│   Feature   │ │ Chunk Mgmt  │ │PR Generator │
│  Analyzer   │ │& File Locks │ │   Agent     │
│   Agent     │ │             │ │             │
│• Analyze    │ │• Track      │ │• Generate   │
│  codebase   │ │  chunks     │ │  code       │
│• Identify   │ │• Lock files │ │• Create PRs │
│  affected   │ │• Resolve    │ │• Merge PRs  │
│  files      │ │  deps       │ │• Clean up   │
└─────────────┘ └─────────────┘ └─────────────┘
        │             │             │
        └─────────────┼─────────────┘
                      ▼
              ┌───────────────┐
              │    GitHub     │
              │  Repository   │
              └───────────────┘
```

### Agents

- **Coordinator**: Orchestrates the workflow, assigns chunks, coordinates merging
- **Feature Analyzer**: Analyzes feature specs using DSPy to identify affected files and dependencies
- **PR Generator**: Implements chunks, creates branches, generates code, and creates PRs

### Event-Driven Communication

Agents communicate via events:

- `ANALYZE_FEATURE` - Request feature analysis
- `FEATURE_ANALYZED` - Analysis results
- `CHUNKS_PLANNED` - Chunk distribution complete
- `CHUNK_ASSIGNED` - Work assigned to agent
- `CHUNK_COMPLETED` - PR created and ready
- `PR_REVIEWED` - Automated review complete
- `PR_MERGED` - Changes integrated

### File Coordination

File locking prevents conflicts:

- Agents acquire locks before working on files
- Dependency resolution ensures proper merge order
- Progress tracking shows real-time status

## Getting API Keys

Before configuring the application, you'll need to obtain the required API keys:

### GitHub Personal Access Token

1. **Sign in to GitHub** and go to [GitHub Settings > Developer settings > Personal access tokens > Tokens (classic)](https://github.com/settings/tokens)
2. **Click "Generate new token"** and select "Generate new token (classic)"
3. **Add a note** describing what the token is for (e.g., "Automaton PR automation")
4. **Set expiration** (recommended: 90 days or custom)
5. **Select scopes** - You need the following permissions:
   - `repo`
6. **Click "Generate token"**
7. **Copy the token immediately** - you won't be able to see it again
8. **Store it securely** - you'll use this as your `GITHUB_TOKEN`

### Gemini API Key

1. **Go to [Google AI Studio](https://aistudio.google.com/)**
2. **Sign in** with your Google account
3. **Click "Get API key"**
4. **Click "Create API key"**
5. **Select a Google Cloud project** (or create a new one)
6. **Copy the generated API key**
7. **Store it securely** - you'll use this as your `GEMINI_API_KEY`

## Configuration

### .env File (For CLI and development)

Create a `.env` file in the project root:

```bash
# Copy the example file
cp .env.example .env

# Edit with your values
GITHUB_TOKEN=your_github_personal_access_token_here
GITHUB_USERNAME=your_github_username
TARGET_REPO_PATH=./examples/foo
GEMINI_API_KEY=your_gemini_api_key_here
COORDINATION_DB_PATH=coordination.db
```

The system will automatically load the `.env` file if `python-dotenv` is installed.

## Usage

### Desktop GUI Application

To use Automaton through the desktop GUI:

Download the application for your system.

The desktop application provides:

- Interactive project folder selection
- Credential management with validation
- Real-time progress monitoring
- Feature specification input
- Status updates and logging

### Command Line Interface

1. **Download and extract** the CLI application for your platform
2. **Set up environment variables** (see Configuration section below)
3. **Run with your feature specification**:

```bash
# Basic usage (uses TARGET_REPO_PATH env var or current directory)
automaton --feature "Add user authentication with login and logout functionality"

# Specify target repository path directly
automaton --feature "Add user authentication" --target-repo "/path/to/your/project"

# Short form
automaton -f "Add user authentication" -r "../my-project"
```

**Required Environment Variables:**

- `GITHUB_TOKEN` - Your GitHub personal access token
- `GITHUB_USERNAME` - Your GitHub username
- `GEMINI_API_KEY` - Your Google Gemini API key

#### Development Mode

If you're running from source code:

```bash
# Run the CLI directly from source
uv run python -m src.main --feature "Your feature description here"

# Run the desktop GUI from source
uv run python desktop_app.py


```

### Programmatic Usage

```python
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from main import PRAutomationSystem

async def main():
    system = PRAutomationSystem(
        target_repo_path="/path/to/repo",
        github_token="your_token",
        github_username="your_username",
        repo_name="username/repo",
        gemini_api_key="your_api_key"
    )

    # Start all agents
    await system.start()

    # Process a feature
    await system.process_feature(
        "Add user authentication with login and logout functionality"
    )

    # Monitor status
    status = await system.get_status()
    print(f"Total chunks: {status['total_chunks']}")

if __name__ == "__main__":
    asyncio.run(main())
```

## Desktop Application Features

- **Native File Browser**: Cross-platform folder selection
- **Credential Management**: Secure storage and validation
- **Progress Monitoring**: Real-time status updates
- **Settings Dialog**: Model and configuration management
- **Validation**: GitHub and Gemini API key validation

## Example Workflow

1. **Feature Input**: "Add user authentication system"

2. **Analysis**: DSPy analyzes the codebase and identifies:

   - `auth/models.py` - User model changes
   - `auth/views.py` - Login/logout endpoints
   - `templates/login.html` - Login form
   - `requirements.txt` - New dependencies

3. **Chunk Planning**: Creates logical chunks:

   - Chunk 1: User model and database changes
   - Chunk 2: Authentication views and logic
   - Chunk 3: Frontend templates and forms
   - Chunk 4: Integration and configuration

4. **Implementation**: Each chunk becomes a separate PR:

   - PR #1: "Add User model with authentication fields"
   - PR #2: "Implement login/logout views" (depends on PR #1)
   - PR #3: "Add authentication templates" (depends on PR #2)
   - PR #4: "Configure authentication middleware" (depends on PR #3)

5. **Review & Merge**: Automated review and dependency-ordered merging

## Building Executables

### Build Both CLI and GUI Executables

```bash
# Run the build script (creates both CLI and GUI executables)
uv run python build/build.py
```

### Build Individual Executables

```bash
# Build CLI executable only
uv run pyinstaller build/build-cli.spec

# Build GUI executable only
uv run pyinstaller build/build-gui.spec
```

The executables will be created in the `pyinstaller_dist/` directory.

## Limitations

- Currently supports GitHub only
- Basic automated review (no test execution)
- Single repository at a time
- Requires manual conflict resolution for complex dependencies

## Future Enhancements

- GitLab and other Git platform support
- Advanced testing and review automation
- Multi-repository coordination
- Intelligent conflict resolution
- Custom agent plugins

## License

MIT License - see LICENSE file for details.
