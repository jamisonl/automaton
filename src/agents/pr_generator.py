"""PR generator agent that implements chunks and creates pull requests."""

import asyncio
import os
from typing import Dict, Any, Optional
from pathlib import Path
import git
from github import Github
import traceback
import pathspec

from agents.base import BaseAgent, AgentConfig, DSPyModule, ChunkPlan
from core.events import EventType, Event
from core.coordination import ChunkStatus
from core.logger import logger


class PRGeneratorAgent(BaseAgent):
    def __init__(
        self,
        config: AgentConfig,
        target_repo_path: str,
        github_token: str,
        github_username: str,
        repo_name: str,
        shared_event_bus=None,
    ):
        super().__init__(config, shared_event_bus)
        self.target_repo_path = Path(target_repo_path)
        self.github_token = github_token
        self.github_username = github_username
        self.repo_name = repo_name
        self.dspy_module = DSPyModule()
        self.github_client = Github(github_token)
        self.user = self.github_client.get_user()  
        self.repo = None
        self.target_repo_path.mkdir(parents=True, exist_ok=True)

        try:
            self.git_repo = git.Repo(target_repo_path)
        except git.exc.InvalidGitRepositoryError:
            project_name = Path(target_repo_path).name
            logger.info(
                f"Initializing git repository for '{project_name}' at {target_repo_path}"
            )

            self.git_repo = git.Repo.init(target_repo_path)

            
            try:
                list(self.git_repo.iter_commits())
                has_commits = True
            except (ValueError, StopIteration):
                has_commits = False

            if not has_commits:
                project_path = Path(target_repo_path)
                files_to_add = []

                for file_path in project_path.rglob("*"):
                    if file_path.is_file():
                        
                        relative_path = file_path.relative_to(project_path)
                        if not any(
                            part.startswith(".") for part in relative_path.parts
                        ):
                            files_to_add.append(str(relative_path))

                if files_to_add:
                    for file_path_str in files_to_add:
                        self.git_repo.git.add(file_path_str)
                    self.git_repo.git.commit(
                        "-m",
                        f"Initial commit for {project_name}",
                        "--author",
                        f"{self.github_username} <{self.github_username}@users.noreply.github.com>",
                    )
                else:
                    readme_path = project_path / "README.md"
                    readme_path.write_text(
                        f"# {project_name}\n\nProject initialized by Automaton System.\n"
                    )
                    self.git_repo.git.add("README.md")
                    self.git_repo.git.commit(
                        "-m",
                        f"Initial commit for {project_name}",
                        "--author",
                        f"{self.github_username} <{self.github_username}@users.noreply.github.com>",
                    )

    async def _ensure_github_repository(self):
        try:
            self.repo = self.github_client.get_repo(self.repo_name)
            logger.info(f"✅ Found existing GitHub repository: {self.repo_name}")
        except Exception:
            logger.warning(
                f"GitHub repository {self.repo_name} not found. Attempting to create it..."
            )
            try:
                
                
                if "/" not in self.repo_name:
                    repo_name_to_create = f"{self.user.login}/{self.repo_name}"
                else:
                    repo_name_to_create = self.repo_name

                logger.info(f"Attempting to create repository: {repo_name_to_create}")

                repo_name_only = (
                    repo_name_to_create.split("/")[-1]
                    if "/" in repo_name_to_create
                    else repo_name_to_create
                )

                self.repo = self.user.create_repo(
                    repo_name_only,
                    private=True,
                    auto_init=False,  
                    description="Repository created by Automaton System",
                )
                logger.info(
                    f"✅ Successfully created private GitHub repository: {self.repo.full_name}"
                )
                
                if self.repo_name != self.repo.full_name:
                    logger.info(
                        f"Updating internal repo_name from '{self.repo_name}' to '{self.repo.full_name}'"
                    )
                    self.repo_name = self.repo.full_name

            except Exception as e:
                logger.info(
                    f"Failed to create GitHub repository {repo_name_to_create} (it might already exist): {e}"
                )
                try:
                    self.repo = self.github_client.get_repo(repo_name_to_create)
                    logger.info(
                        f"✅ Found existing GitHub repository after creation attempt: {self.repo.full_name}"
                    )
                    if self.repo_name != self.repo.full_name:
                        self.repo_name = self.repo.full_name
                except Exception as get_e:
                    logger.error(
                        f"❌ Failed to get or create GitHub repository {repo_name_to_create}: {get_e}"
                    )
                    raise

        authenticated_url = f"https://{self.github_username}:{self.github_token}@github.com/{self.repo_name}.git"

        try:
            origin = self.git_repo.remote("origin")
            if origin.url != authenticated_url:
                logger.warning(
                    f"Origin remote URL differs from expected authenticated URL. Updating..."
                )
                origin.set_url(authenticated_url)
                logger.info("✅ Origin remote URL updated with authentication.")
        except ValueError as ve:
            logger.info(
                f"Configuring 'origin' remote with authentication (caught ValueError: {ve})..."
            )
            self.git_repo.create_remote("origin", authenticated_url)
            logger.info("✅ 'origin' remote configured with authentication.")
        except Exception as e:
            logger.error(f"❌ Error configuring git remote 'origin': {e}")
            raise

    async def setup_event_subscriptions(self):
        await self._ensure_github_repository()
        self.event_bus.subscribe(EventType.CHUNK_ASSIGNED, self.handle_chunk_assigned)
        self.event_bus.subscribe(EventType.MERGE_PR, self.handle_merge_pr)
        self.event_bus.subscribe(
            EventType.FEATURE_COMPLETED, self.handle_feature_completed
        )

    async def run(self):
        logger.info(f"PR Generator {self.agent_id} started")

        while self.running:
            await asyncio.sleep(1)

    async def handle_chunk_assigned(self, event: Event):
        assigned_agent = event.data.get("assigned_agent")

        if assigned_agent != self.agent_id and assigned_agent != "pr_generator":
            return

        chunk_id = event.data.get("chunk_id")
        description = event.data.get("description")
        files = event.data.get("files", [])

        logger.info(f"Processing chunk {chunk_id}: {description}")

        try:
            lock_acquired = await self.coordination.acquire_file_locks(
                self.agent_id, chunk_id, files
            )

            if not lock_acquired:
                logger.warning(
                    f"Could not acquire locks for chunk {chunk_id}, files may be in use"
                )
                return

            await self.publish_event(
                EventType.CHUNK_STARTED,
                {
                    "chunk_id": chunk_id,
                    "agent_id": self.agent_id,
                    "description": description,
                    "files": files,
                },
            )

            chunk = await self.coordination.get_chunk(chunk_id)
            if not chunk:
                logger.error(f"Chunk {chunk_id} not found in database")
                return

            pr_number = await self.process_chunk(chunk)

            if pr_number:
                await self.publish_event(
                    EventType.CHUNK_COMPLETED,
                    {
                        "chunk_id": chunk_id,
                        "pr_number": pr_number,
                        "agent_id": self.agent_id,
                    },
                )

                logger.info(f"Chunk {chunk_id} completed with PR #{pr_number}")
            else:
                logger.warning(f"Failed to process chunk {chunk_id}")

        except Exception as e:
            logger.exception(f"Error processing chunk {chunk_id}:")
        finally:
            await self.coordination.release_file_locks(self.agent_id, chunk_id)

    async def process_chunk(self, chunk) -> Optional[int]:
        try:
            branch_name = f"feature/{chunk.chunk_id}"

            try:
                self.git_repo.git.checkout("main")
            except git.exc.GitCommandError as e:
                logger.warning(f"Error checking out 'main' branch: {e}.")
                
                if (
                    "did not match any file(s) known to git" in str(e).lower()
                    or "is not a commit and a branch 'main' cannot be created from it"
                    in str(e).lower()
                ) and not list(self.git_repo.iter_commits()):
                    logger.info(
                        "Attempting to create initial commit on main as it's missing and repo is empty."
                    )
                    readme_path = self.target_repo_path / "README.md"
                    if not readme_path.exists():
                        readme_path.write_text(
                            f"# {project_name}\n\nProject initialized by Automaton System.\n"
                        )
                    self.git_repo.git.add(str(readme_path))
                    self.git_repo.git.commit(
                        "-m",
                        "Initial commit on main",
                        "--author",
                        f"{self.github_username} <{self.github_username}@users.noreply.github.com>",
                    )
                    if (
                        "main" not in self.git_repo.heads
                    ):  
                        self.git_repo.create_head("main")  
                    self.git_repo.git.checkout("main")
                    logger.info("Created and checked out initial 'main' branch.")
                    try:
                        logger.info("Attempting to push new 'main' branch to origin...")
                        self.git_repo.git.push("--set-upstream", "origin", "main")
                        logger.info("Successfully pushed 'main' to origin.")
                    except git.exc.GitCommandError as push_e:
                        logger.warning(
                            f"Could not push initial 'main' branch to origin: {push_e}"
                        )
                else:
                    logger.error(
                        "Please ensure 'main' branch exists and the repository is initialized correctly if not empty."
                    )
                    raise

            try:
                logger.info("Pulling latest changes from origin/main...")
                self.git_repo.git.pull("origin", "main")
                logger.info("Successfully pulled from origin/main.")
            except git.exc.GitCommandError as e:
                logger.warning(
                    f"'git pull origin main' failed: {e}. Proceeding with local 'main' branch. Ensure it's up-to-date if this is unexpected."
                )
                
                if "couldn't find remote ref main" in str(e).lower():
                    try:
                        logger.info(
                            "Main branch doesn't exist on remote. Pushing local main to origin..."
                        )
                        self.git_repo.git.push("--set-upstream", "origin", "main")
                        logger.info("Successfully pushed 'main' to origin.")
                    except git.exc.GitCommandError as push_e:
                        logger.warning(
                            f"Could not push 'main' branch to origin: {push_e}"
                        )

            
            if branch_name in self.git_repo.heads:
                self.git_repo.git.checkout(branch_name)
            else:
                self.git_repo.git.checkout("-b", branch_name)

            existing_code = {}
            all_project_files = (
                self.get_all_project_files()
            )  

            for file_path_str in all_project_files:
                file_path = Path(file_path_str)  
                full_path = self.target_repo_path / file_path
                content = ""
                if full_path.exists() and full_path.is_file():
                    try:
                        content = full_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        logger.warning(
                            f"File {full_path} is not valid UTF-8. Trying latin-1..."
                        )
                        try:
                            content = full_path.read_text(encoding="latin-1")
                        except UnicodeDecodeError:
                            logger.warning(
                                f"File {full_path} is not valid latin-1 either. Reading with errors replaced..."
                            )
                            content = full_path.read_bytes().decode(
                                "utf-8", errors="replace"
                            )
                    except Exception as e:
                        logger.error(f"Error reading file {full_path}: {e}")
                        content = f"Error reading file: {e}"
                elif not full_path.exists():
                    logger.info(
                        f"File {full_path} does not exist, will be treated as new."
                    )

                existing_code[file_path_str] = content

            chunk_plan = ChunkPlan(
                chunk_id=chunk.chunk_id,
                description=chunk.description,
                files=chunk.files,
                dependencies=chunk.dependencies,
                estimated_effort=5,  
            )

            try:
                logger.info(
                    f"🤖 Starting DSPy code generation for chunk {chunk.chunk_id}..."
                )
                logger.info(f"📝 Chunk description: {chunk.description}")
                logger.info(f"📂 Files to modify: {chunk.files}")
                logger.info(f"📊 Existing codebase size: {len(existing_code)} files")

                
                total_chars = sum(len(content) for content in existing_code.values())
                logger.info(f"📏 Total context size: {total_chars:,} characters")

                await self.publish_event(
                    EventType.CODE_GENERATION_STARTED,
                    {
                        "chunk_id": chunk.chunk_id,
                        "description": chunk.description,
                        "files": chunk.files,
                    },
                )

                modified_files, commit_message = self.dspy_module.generate_code(
                    chunk_plan, existing_code
                )
                logger.info(
                    f"✅ DSPy code generation completed for chunk {chunk.chunk_id}"
                )
            except Exception as e:
                logger.exception(
                    f"❌ DSPy code generation failed for chunk {chunk.chunk_id}:"
                )
                
                await self.coordination.update_chunk_status(
                    chunk.chunk_id, ChunkStatus.PLANNED
                )
                return None

            for file_path, content in modified_files.items():
                full_path = self.target_repo_path / file_path

                full_path.parent.mkdir(parents=True, exist_ok=True)

                full_path.write_text(content, encoding="utf-8")

            await self.publish_event(
                EventType.FILES_MODIFIED,
                {
                    "chunk_id": chunk.chunk_id,
                    "modified_files": list(modified_files.keys()),
                },
            )

            self.git_repo.git.add(".")
            self.git_repo.git.commit(
                "-m",
                commit_message,
                "--author",
                f"{self.github_username} <{self.github_username}@users.noreply.github.com>",
            )

            self.git_repo.git.push("--set-upstream", "origin", branch_name)

            
            try:
                self.git_repo.git.fetch("origin", "main")
                logger.info("✅ Main branch exists on remote")
            except git.exc.GitCommandError:
                logger.warning("Main branch doesn't exist on remote, pushing it...")
                try:
                    self.git_repo.git.push("origin", "main")
                    logger.info("✅ Successfully pushed main branch to remote")
                except git.exc.GitCommandError as push_e:
                    logger.error(f"❌ Failed to push main branch: {push_e}")
                    try:
                        self.git_repo.git.checkout("main")
                    except git.exc.GitCommandError:
                        logger.info(
                            "Creating main branch as it could not be checked out or pushed..."
                        )
                        readme_path = self.target_repo_path / "README.md"
                        if not readme_path.exists():
                            readme_path.write_text(
                                f"# {project_name}\n\nProject initialized by Automaton System.\n"
                            )
                        self.git_repo.git.checkout("-b", "main")
                        self.git_repo.git.add(".")
                        self.git_repo.git.commit(
                            "-m",
                            "Initial commit",
                            "--author",
                            f"{self.github_username} <{self.github_username}@users.noreply.github.com>",
                        )
                        self.git_repo.git.push("--set-upstream", "origin", "main")
                        logger.info("✅ Created and pushed main branch")

            pr_title, pr_description = self.dspy_module.generate_pr_description(
                chunk_plan, list(modified_files.keys()), commit_message
            )

            pr = self.repo.create_pull(
                title=pr_title, body=pr_description, head=branch_name, base="main"
            )

            logger.info(f"Created PR #{pr.number}: {pr_title}")

            await self.publish_event(
                EventType.PR_CREATED,
                {
                    "chunk_id": chunk.chunk_id,
                    "pr_number": pr.number,
                    "pr_title": pr_title,
                    "branch_name": branch_name,
                    "url": pr.html_url,
                },
            )

            
            await self.auto_review_pr(pr.number, chunk.chunk_id)

            return pr.number

        except Exception as e:
            logger.exception(f"Error creating PR for chunk {chunk.chunk_id}:")
            
            try:
                self.git_repo.git.checkout("main")
                self.git_repo.git.branch("-D", branch_name)
            except:
                pass
            return None

    async def auto_review_pr(self, pr_number: int, chunk_id: str):
        try:
            
            
            

            await self.publish_event(
                EventType.PR_REVIEWED,
                {
                    "chunk_id": chunk_id,
                    "pr_number": pr_number,
                    "approved": True,
                    "review_comments": [],
                },
            )

            logger.info(f"Auto-review completed for PR #{pr_number}")

        except Exception as e:
            logger.exception(f"Error during auto-review of PR #{pr_number}:")

    async def handle_merge_pr(self, event: Event):
        chunk_id = event.data.get("chunk_id")
        pr_number = event.data.get("pr_number")

        logger.info(f"🔀 Merging PR #{pr_number} for chunk {chunk_id}")

        try:
            pr = self.repo.get_pull(pr_number)

            merge_result = pr.merge(
                commit_title=f"Merge PR #{pr_number}: {pr.title}",
                commit_message=f"Automatically merged PR #{pr_number} for chunk {chunk_id}",
                merge_method="squash",
            )

            if merge_result.merged:
                logger.info(f"✅ Successfully merged PR #{pr_number}")

                await self.publish_event(
                    EventType.PR_MERGED,
                    {
                        "chunk_id": chunk_id,
                        "pr_number": pr_number,
                        "merged": True,
                    },
                )

                try:
                    self.git_repo.git.checkout("main")
                    self.git_repo.git.pull("origin", "main")
                    logger.info(f"✅ Updated local main branch with merged changes")
                except git.exc.GitCommandError as e:
                    logger.warning(f"Could not update local main branch: {e}")

                try:
                    branch_name = f"feature/{chunk_id}"
                    if branch_name in self.git_repo.heads:
                        self.git_repo.git.branch("-D", branch_name)
                        logger.info(f"🗑️ Deleted local feature branch: {branch_name}")
                        await self.publish_event(
                            EventType.BRANCH_DELETED,
                            {
                                "chunk_id": chunk_id,
                                "branch_name": branch_name,
                                "pr_number": pr_number,
                            },
                        )
                except git.exc.GitCommandError as e:
                    logger.warning(
                        f"Could not delete feature branch {branch_name}: {e}"
                    )

            else:
                logger.error(f"❌ Failed to merge PR #{pr_number}")

        except Exception as e:
            logger.exception(f"❌ Error merging PR #{pr_number}:")

    def get_all_project_files(self) -> list[str]:
        project_files = []

        gitignore_file_path = self.target_repo_path / ".gitignore"
        spec = None
        if gitignore_file_path.is_file():
            try:
                with open(gitignore_file_path, "r", encoding="utf-8") as f:
                    gitignore_patterns = f.readlines()
         
                essential_ignores = [
                    ".git/",
                    "*.pyc",
                    "__pycache__/",
                    ".DS_Store",
                    ".env",          
                ]                

                spec = pathspec.PathSpec.from_lines(
                    "gitwildmatch", gitignore_patterns + essential_ignores
                )
                logger.debug(
                    f"Loaded .gitignore patterns from {gitignore_file_path} and essential ignores."
                )
            except Exception as e:
                logger.warning(
                    f"Could not read or parse .gitignore file at {gitignore_file_path}: {e}. Proceeding without .gitignore filtering for this run."
                )
                
                essential_ignores = [
                    ".git/",
                    "*.pyc",
                    "__pycache__/",
                    ".DS_Store",
                    ".env",
                    "node_modules/",
                ]
                spec = pathspec.PathSpec.from_lines("gitwildmatch", essential_ignores)
   
        if spec is None:
            logger.info(
                "No .gitignore found or parsed. Using default hardcoded ignores."
            )
            
            
            default_ignore_patterns = [
                ".git/",
                "__pycache__/",
                "*.pyc",
                "node_modules/",
                ".DS_Store",
                ".env",
                "*.db",
                "*.sqlite",
                "*.sqlite3",
                "*.log",  
            ]
            spec = pathspec.PathSpec.from_lines("gitwildmatch", default_ignore_patterns)

        for file_path_obj in self.target_repo_path.rglob("*"):
            if file_path_obj.is_file():
                
                try:
                    relative_path_for_spec = file_path_obj.relative_to(
                        self.target_repo_path
                    )
                except ValueError:
                    
                    
                    
                    logger.warning(
                        f"Could not make {file_path_obj} relative to {self.target_repo_path}, skipping."
                    )
                    continue

                if spec.match_file(str(relative_path_for_spec)):                
                    continue

                project_files.append(str(relative_path_for_spec))

        return sorted(
            list(set(project_files))
        )  

    async def handle_feature_completed(self, event: Event):
        logger.info(f"🎉 Feature completed! Shutting down PR Generator...")
        await self.stop()
