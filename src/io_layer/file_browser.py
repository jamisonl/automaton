"""File system browser for repository selection and exploration."""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class FileNode(BaseModel):
    name: str
    path: str
    is_directory: bool
    size: Optional[int] = None
    modified: Optional[str] = None
    children: Optional[List["FileNode"]] = None
    is_git_repo: bool = False
    is_expandable: bool = False


class RepositoryInfo(BaseModel):
    path: str
    name: str
    is_git_repo: bool
    has_remote: bool
    file_count: int
    directory_count: int
    total_size: int
    main_language: Optional[str] = None
    languages: Dict[str, int] = {}


class FileBrowser:
    """Safe file system browser for repository selection and exploration."""

    def __init__(self, root_path: Optional[str] = None):
        self.root_path = Path(root_path) if root_path else Path.cwd()
        self.allowed_roots = [
            self.root_path,
            Path.home(),
            Path("/tmp") if os.name != "nt" else Path("C:/temp"),
        ]

    def get_directory_listing(
        self, path: str, include_hidden: bool = False, max_depth: int = 1
    ) -> List[FileNode]:
        """Get directory listing with optional recursion."""
        path_obj = Path(path).resolve()

        if not self._is_path_allowed(path_obj):
            raise ValueError(f"Access denied to path: {path}")

        if not path_obj.exists() or not path_obj.is_dir():
            raise ValueError(f"Path is not a valid directory: {path}")

        nodes = []

        try:
            for item in sorted(path_obj.iterdir()):
                if not include_hidden and item.name.startswith("."):
                    continue

                if item.name in [
                    "__pycache__",
                    "node_modules",
                    ".git",
                    ".pytest_cache",
                    ".mypy_cache",
                    ".venv",
                    "venv",
                    ".env",
                ]:
                    if item.name != ".git" or not include_hidden:
                        continue

                try:
                    stat = item.stat()
                    is_git_repo = False
                    is_expandable = False

                    if item.is_dir():
                        if (item / ".git").exists():
                            is_git_repo = True

                        try:
                            is_expandable = any(item.iterdir())
                        except PermissionError:
                            is_expandable = False

                    node = FileNode(
                        name=item.name,
                        path=str(item),
                        is_directory=item.is_dir(),
                        size=stat.st_size if item.is_file() else None,
                        modified=str(stat.st_mtime),
                        is_git_repo=is_git_repo,
                        is_expandable=is_expandable,
                    )

                    if max_depth > 1 and item.is_dir() and is_expandable:
                        try:
                            node.children = self.get_directory_listing(
                                str(item), include_hidden, max_depth - 1
                            )
                        except (PermissionError, ValueError):
                            node.children = []

                    nodes.append(node)

                except (PermissionError, OSError):
                    continue

        except PermissionError:
            raise ValueError(f"Permission denied accessing directory: {path}")

        return nodes

    def get_repository_info(self, path: str) -> RepositoryInfo:
        """Get detailed information about a repository."""
        path_obj = Path(path).resolve()

        if not self._is_path_allowed(path_obj):
            raise ValueError(f"Access denied to path: {path}")

        if not path_obj.exists() or not path_obj.is_dir():
            raise ValueError(f"Path is not a valid directory: {path}")

        is_git_repo = (path_obj / ".git").exists()
        has_remote = False

        if is_git_repo:
            try:
                import git

                repo = git.Repo(path_obj)
                has_remote = len(list(repo.remotes)) > 0
            except Exception:
                has_remote = False

        file_count = 0
        directory_count = 0
        total_size = 0
        languages = {}

        try:
            for item in path_obj.rglob("*"):
                if any(
                    part.startswith(".") for part in item.parts[len(path_obj.parts) :]
                ):
                    continue

                if any(
                    skip in str(item)
                    for skip in [
                        "__pycache__",
                        "node_modules",
                        ".pytest_cache",
                        ".mypy_cache",
                        ".venv",
                        "venv",
                    ]
                ):
                    continue

                try:
                    if item.is_file():
                        file_count += 1
                        stat = item.stat()
                        total_size += stat.st_size

                        extension = item.suffix.lower()
                        if extension:
                            languages[extension] = languages.get(extension, 0) + 1

                    elif item.is_dir():
                        directory_count += 1

                except (PermissionError, OSError):
                    continue

        except Exception as e:
            pass

        main_language = None
        if languages:
            main_language = max(languages.items(), key=lambda x: x[1])[0]

        return RepositoryInfo(
            path=str(path_obj),
            name=path_obj.name,
            is_git_repo=is_git_repo,
            has_remote=has_remote,
            file_count=file_count,
            directory_count=directory_count,
            total_size=total_size,
            main_language=main_language,
            languages=languages,
        )

    def validate_repository_path(self, path: str) -> Dict[str, Any]:
        """Validate if a path is suitable for use as a target repository."""
        path_obj = Path(path).resolve()

        validation_result = {
            "valid": False,
            "path": str(path_obj),
            "exists": False,
            "is_directory": False,
            "is_git_repo": False,
            "is_writable": False,
            "has_source_files": False,
            "latest_commit": None,
            "warnings": [],
            "errors": [],
        }

        if not self._is_path_allowed(path_obj):
            validation_result["errors"].append("Access denied to this path")
            return validation_result

        if not path_obj.exists():
            validation_result["errors"].append("Path does not exist")
            return validation_result

        validation_result["exists"] = True

        if not path_obj.is_dir():
            validation_result["errors"].append("Path is not a directory")
            return validation_result

        validation_result["is_directory"] = True

        try:
            test_file = path_obj / ".temp_write_test"
            test_file.touch()
            test_file.unlink()
            validation_result["is_writable"] = True
        except (PermissionError, OSError):
            validation_result["errors"].append("Directory is not writable")

        if (path_obj / ".git").exists():
            validation_result["is_git_repo"] = True
            try:
                import git

                repo = git.Repo(path_obj)
                if repo.heads:
                    latest_commit = repo.head.commit
                    validation_result["latest_commit"] = {
                        "hexsha": latest_commit.hexsha,
                        "author_name": latest_commit.author.name,
                        "authored_date": latest_commit.authored_datetime.isoformat(),
                        "committer_name": latest_commit.committer.name,
                        "committed_date": latest_commit.committed_datetime.isoformat(),
                        "message": latest_commit.message.strip(),
                    }
            except ImportError:
                validation_result["warnings"].append(
                    "GitPython library not found, cannot fetch commit info."
                )
            except Exception as e:
                validation_result["warnings"].append(
                    f"Could not retrieve git commit info: {str(e)}"
                )
        else:
            validation_result["warnings"].append(
                "Not a git repository - will be initialized automatically"
            )

        source_extensions = {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".java",
            ".cpp",
            ".c",
            ".cs",
            ".go",
            ".rs",
            ".rb",
            ".php",
            ".swift",
            ".kt",
            ".scala",
        }

        has_source = False
        try:
            for item in path_obj.rglob("*"):
                if item.is_file() and item.suffix.lower() in source_extensions:
                    has_source = True
                    break
        except Exception:
            pass

        validation_result["has_source_files"] = has_source
        if not has_source:
            validation_result["warnings"].append("No common source files detected")

        validation_result["valid"] = (
            validation_result["exists"]
            and validation_result["is_directory"]
            and validation_result["is_writable"]
            and len(validation_result["errors"]) == 0
        )

        return validation_result

    def get_recent_repositories(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get a list of recently accessed repositories."""
        # Todo
        # This should read from a config file or database

        common_dirs = [
            Path.home() / "Documents",
            Path.home() / "Projects",
            Path.home() / "Code",
            Path.home() / "Development",
            Path.home() / "src",
            Path.cwd(),
        ]

        recent_repos = []

        for base_dir in common_dirs:
            if not base_dir.exists():
                continue

            try:
                for item in base_dir.iterdir():
                    if item.is_dir() and (item / ".git").exists():
                        try:
                            repo_info = self.get_repository_info(str(item))
                            recent_repos.append(
                                {
                                    "path": str(item),
                                    "name": item.name,
                                    "is_git_repo": True,
                                    "file_count": repo_info.file_count,
                                    "main_language": repo_info.main_language,
                                }
                            )
                        except Exception:
                            continue

                        if len(recent_repos) >= limit:
                            break

            except (PermissionError, OSError):
                continue

            if len(recent_repos) >= limit:
                break

        return recent_repos[:limit]

    def _is_path_allowed(self, path: Path) -> bool:
        """Check if a path is within allowed roots for security."""
        try:
            path = path.resolve()
            for allowed_root in self.allowed_roots:
                try:
                    path.relative_to(allowed_root.resolve())
                    return True
                except ValueError:
                    continue
            return False
        except Exception:
            return False


FileNode.model_rebuild()
