import argparse
import subprocess
import sys
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
BUILD_DIR = Path(__file__).parent.resolve()
DIST_DIR = PROJECT_ROOT / "dist"
PYINSTALLER_DIST_DIR = PROJECT_ROOT / "pyinstaller_dist"
PYINSTALLER_BUILD_DIR = PROJECT_ROOT / "pyinstaller_build"


def run_pyinstaller(spec_file_name: str, app_name: str):
    spec_file_path = BUILD_DIR / spec_file_name
    if not spec_file_path.exists():
        print(f"Error: Spec file {spec_file_path} not found.")
        sys.exit(1)

    print(f"Building {app_name} using {spec_file_name}...")

    if PYINSTALLER_DIST_DIR.exists():
        shutil.rmtree(PYINSTALLER_DIST_DIR)
    if PYINSTALLER_BUILD_DIR.exists():
        shutil.rmtree(PYINSTALLER_BUILD_DIR)

    PYINSTALLER_DIST_DIR.mkdir(parents=True, exist_ok=True)
    PYINSTALLER_BUILD_DIR.mkdir(parents=True, exist_ok=True)

    command = [
        "uv",
        "run",
        "python",
        "-m",
        "PyInstaller",
        str(spec_file_path),
        "--distpath",
        str(PYINSTALLER_DIST_DIR),
        "--workpath",
        str(PYINSTALLER_BUILD_DIR),
        "--noconfirm",  # Overwrite output directory without asking
    ]

    try:
        process = subprocess.Popen(command, cwd=PROJECT_ROOT)
        process.wait()  # Wait for PyInstaller to complete
        if process.returncode != 0:
            print(
                f"Error: PyInstaller failed for {app_name} with exit code {process.returncode}."
            )
            sys.exit(process.returncode)
        print(f"Successfully built {app_name}.")

        # GUI spec creates 'automaton-gui' folder, CLI spec creates 'automaton-cli' folder
        if app_name == "automaton-gui":
            source_folder_name = "automaton-gui"  # GUI spec COLLECT name
            target_folder_name = "gui"
        else:  # automaton-cli
            source_folder_name = "automaton-cli"  # CLI spec COLLECT name
            target_folder_name = "cli"

        source_output_path = PYINSTALLER_DIST_DIR / source_folder_name
        target_output_path = DIST_DIR / target_folder_name

        if target_output_path.exists():
            print(f"Cleaning up old version at {target_output_path}")
            shutil.rmtree(target_output_path)

        DIST_DIR.mkdir(parents=True, exist_ok=True)  # Ensure DIST_DIR exists

        # Move and rename in one operation
        shutil.move(str(source_output_path), str(target_output_path))
        print(f"Moved build output to {target_output_path}")

    except FileNotFoundError:
        print("Error: PyInstaller command not found. Is it installed and in your PATH?")
        print("Try: uv pip install pyinstaller")
        sys.exit(1)
    except Exception as e:
        print(
            f"An unexpected error occurred during PyInstaller execution for {app_name}: {e}"
        )
        sys.exit(1)
    finally:
        # Clean up PyInstaller's temporary build and dist directories
        if PYINSTALLER_BUILD_DIR.exists():
            shutil.rmtree(PYINSTALLER_BUILD_DIR)
        if PYINSTALLER_DIST_DIR.exists():  # Should be empty if move succeeded
            try:
                PYINSTALLER_DIST_DIR.rmdir()
            except OSError:  # Might not be empty if something went wrong
                pass


def main():
    parser = argparse.ArgumentParser(description="Build script for Automaton.")
    parser.add_argument(
        "target",
        choices=["gui", "cli", "all"],
        help="Which application to build: 'gui', 'cli', or 'all'.",
    )
    args = parser.parse_args()

    if not DIST_DIR.exists():
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Created distribution directory: {DIST_DIR}")

    if args.target == "gui" or args.target == "all":
        run_pyinstaller("build-gui.spec", "automaton-gui")
        # For macOS, PyInstaller creates Automaton.app inside gui folder
        # We might want to move Automaton.app directly to DIST_DIR for macOS
        if sys.platform == "darwin":
            macos_app_bundle_source = DIST_DIR / "gui" / "Automaton.app"
            macos_app_bundle_target = DIST_DIR / "Automaton.app"
            if macos_app_bundle_source.exists():
                if macos_app_bundle_target.exists():
                    shutil.rmtree(macos_app_bundle_target)
                shutil.move(str(macos_app_bundle_source), str(DIST_DIR))
                # Clean up the now empty gui folder if it only contained the .app
                try:
                    (DIST_DIR / "gui").rmdir()
                except OSError:
                    pass  # Folder might not be empty if other files were there
                print(f"Moved macOS app bundle to {macos_app_bundle_target}")

    if args.target == "cli" or args.target == "all":
        run_pyinstaller("build-cli.spec", "automaton-cli")

    print("Build process completed.")
    print(f"Binaries are located in: {DIST_DIR}")


if __name__ == "__main__":
    main()
