"""Native file browser dialog integration."""

from src.core.logger import logger
import tkinter as tk
from tkinter import filedialog
import os
from pathlib import Path
from typing import Optional


class NativeFileBrowser:
    """Native file browser using tkinter filedialog."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def browse_for_directory(
        self, title: str = "Select Repository Directory", initial_dir: str = None
    ) -> Optional[str]:
        """
        Open a native directory browser dialog.

        Args:
            title: Dialog window title
            initial_dir: Initial directory to show

        Returns:
            Selected directory path or None if cancelled
        """
        if initial_dir is None:
            initial_dir = os.getcwd()

        try:
            directory = filedialog.askdirectory(title=title, initialdir=initial_dir)

            return directory if directory else None

        except Exception as e:
            logger.error(f"Error opening file browser: {e}")
            return None

    def browse_for_file(
        self,
        title: str = "Select File",
        file_types: list = None,
        initial_dir: str = None,
    ) -> Optional[str]:
        """
        Open a native file browser dialog.

        Args:
            title: Dialog window title
            file_types: List of tuples like [("Text files", "*.txt"), ("All files", "*.*")]
            initial_dir: Initial directory to show

        Returns:
            Selected file path or None if cancelled
        """
        if initial_dir is None:
            initial_dir = os.getcwd()

        if file_types is None:
            file_types = [("All files", "*.*")]

        try:
            file_path = filedialog.askopenfilename(
                title=title, initialdir=initial_dir, filetypes=file_types
            )

            return file_path if file_path else None

        except Exception as e:
            logger.error(f"Error opening file browser: {e}")
            return None

    def get_directory_info(self, path: str) -> dict:
        """
        Get detailed information about a directory.

        Args:
            path: Directory path to analyze

        Returns:
            Dictionary with directory information
        """
        path_obj = Path(path)

        info = {
            "path": str(path_obj.absolute()),
            "exists": path_obj.exists(),
            "is_directory": path_obj.is_dir() if path_obj.exists() else False,
            "is_readable": os.access(path, os.R_OK) if path_obj.exists() else False,
            "is_writable": os.access(path, os.W_OK) if path_obj.exists() else False,
            "is_git_repo": False,
            "file_count": 0,
            "folder_count": 0,
            "total_size": 0,
        }

        if info["exists"] and info["is_directory"]:

            git_dir = path_obj / ".git"
            info["is_git_repo"] = git_dir.exists()

            try:
                for item in path_obj.iterdir():
                    if item.is_file():
                        info["file_count"] += 1
                        try:
                            info["total_size"] += item.stat().st_size
                        except (OSError, FileNotFoundError):
                            pass
                    elif item.is_dir():
                        info["folder_count"] += 1
            except (PermissionError, OSError):
                info["file_count"] = -1
                info["folder_count"] = -1
                info["total_size"] = -1

        return info

    def close(self):
        """Clean up the tkinter root window."""
        try:
            self.root.destroy()
        except Exception:
            pass


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    if size_bytes == -1:
        return "Unknown"

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"
