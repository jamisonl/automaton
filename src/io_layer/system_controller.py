"""System controller for managing the agent system lifecycle."""

import asyncio
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from pydantic import BaseModel

from src.core.logger import logger
from .task_manager import TaskManager, TaskStatus
from .progress_publisher import ProgressPublisher, ProgressEventType
from agents.master_coordinator import MasterCoordinatorAgent
from agents.feature_analyzer import FeatureAnalyzerAgent
from agents.pr_generator import PRGeneratorAgent
from agents.base import AgentConfig
from core.events import EventBus, Event as CoreEvent, EventType as CoreEventType
from langchain_google_genai import ChatGoogleGenerativeAI


class SystemStatus(BaseModel):
    is_running: bool
    active_tasks: int
    total_agents: int
    agents_running: int
    database_connected: bool
    github_configured: bool
    gemini_configured: bool


class SystemController:
    """Controls the agent system lifecycle and provides unified management."""

    def __init__(
        self,
        db_path: str = "coordination.db",
        github_token: Optional[str] = None,
        github_username: Optional[str] = None,
        gemini_api_key: Optional[str] = None,
    ):
        self.db_path = db_path
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        self.github_username = github_username or os.getenv("GITHUB_USERNAME")
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")

        self.task_manager = TaskManager(db_path)
        self.progress_publisher = ProgressPublisher(db_path)

        self.event_bus = EventBus(db_path)
        self.agents = {}
        self.agent_tasks = {}

        self.is_running = False
        self.current_task_id: Optional[str] = None

    async def initialize(self):
        """Initialize all system components."""
        await self.task_manager.initialize()
        await self.progress_publisher.initialize()
        await self.event_bus.initialize()

    async def start_system(self):
        """Start the agent system.
        If called when `is_running` is already true, it assumes the previous
        task queue processor's event loop might be dormant (due to how desktop_app.py
        manages threads/loops) and starts a new processor on the current event loop.
        """
        current_loop_id = id(asyncio.get_event_loop_policy().get_event_loop())
        logger.info(
            f"SystemController.start_system called. self.is_running: {self.is_running}. Current event loop ID: {current_loop_id}"
        )

        if not self.is_running:
            logger.debug(
                "SystemController.start_system: First-time start or system was previously fully stopped."
            )
            await self.initialize()
            self.is_running = True
            logger.info(
                "SystemController.start_system: self.is_running set to True. Starting new _process_task_queue."
            )
            asyncio.create_task(self._process_task_queue())
            logger.info(
                f"SystemController.start_system: _process_task_queue task created on loop {current_loop_id}."
            )
        else:
            logger.warning(
                f"SystemController.start_system: Called while self.is_running is True. Starting a new _process_task_queue on current event loop {current_loop_id} to ensure responsiveness for new tasks from UI."
            )

            asyncio.create_task(self._process_task_queue())
            logger.info(
                f"SystemController.start_system: Additional _process_task_queue task created on loop {current_loop_id} due to re-entry while is_running=True."
            )

    async def stop_system(self):
        """Stop the agent system."""
        if not self.is_running:
            return

        self.is_running = False

        for agent_id, agent in self.agents.items():
            await agent.stop()

        for task in self.agent_tasks.values():
            if not task.done():
                task.cancel()

        self.agents.clear()
        self.agent_tasks.clear()

    async def submit_feature_task(
        self, repo_path: str, feature_specification: str
    ) -> str:
        """Submit a new feature implementation task."""
        await self.initialize()

        repo_path_obj = Path(repo_path)
        if not repo_path_obj.exists():
            raise ValueError(f"Repository path does not exist: {repo_path}")

        task_id = await self.task_manager.submit_task(repo_path, feature_specification)

        await self.progress_publisher.publish_progress(
            task_id,
            ProgressEventType.TASK_STARTED,
            {
                "repo_path": repo_path,
                "feature_specification": feature_specification,
            },
            message=f"Task submitted: {feature_specification[:100]}...",
        )

        return task_id

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        success = await self.task_manager.cancel_task(task_id)

        if success:
            await self.progress_publisher.publish_progress(
                task_id,
                ProgressEventType.TASK_CANCELLED,
                {},
                message="Task cancelled by user",
            )

        return success

    async def get_system_status(self) -> SystemStatus:
        """Get current system status."""
        active_tasks = await self.task_manager.get_active_tasks()
        agents_running = sum(1 for agent in self.agents.values() if agent.running)

        return SystemStatus(
            is_running=self.is_running,
            active_tasks=len(active_tasks),
            total_agents=len(self.agents),
            agents_running=agents_running,
            database_connected=await self._check_database_connection(),
            github_configured=bool(self.github_token and self.github_username),
            gemini_configured=bool(self.gemini_api_key),
        )

    async def get_task_status(self, task_id: str):
        """Get detailed status for a specific task."""
        task = await self.task_manager.get_task(task_id)
        if not task:
            return None

        summary = await self.progress_publisher.get_task_summary(task_id)
        return summary

    async def get_all_tasks(self, limit: Optional[int] = None):
        """Get all tasks with optional limit."""
        return await self.task_manager.get_tasks(limit=limit)

    async def _process_task_queue(self):
        """Main task processing loop."""
        logger.info("SystemController: _process_task_queue started.")
        while self.is_running:
            task_processed_in_this_iteration = False
            try:
                logger.debug(
                    f"SystemController: Polling for tasks. self.current_task_id: {self.current_task_id}, self.is_running: {self.is_running}"
                )
                if not self.current_task_id:
                    queued_tasks = await self.task_manager.get_tasks(
                        status=TaskStatus.QUEUED, limit=1
                    )
                    if queued_tasks:
                        task_to_process = queued_tasks[0]
                        logger.info(
                            f"SystemController: Picking up task {task_to_process.task_id}. Setting self.current_task_id."
                        )
                        self.current_task_id = task_to_process.task_id
                        task_processed_in_this_iteration = True

                        try:
                            await self._process_single_task(task_to_process)
                            logger.info(
                                f"SystemController: _process_single_task for {task_to_process.task_id} completed successfully."
                            )
                        except Exception as e_single_task:

                            logger.error(
                                f"SystemController: _process_single_task for {task_to_process.task_id} raised an unhandled exception: {e_single_task}",
                                exc_info=True,
                            )
                            if self.current_task_id == task_to_process.task_id:
                                try:
                                    await self.task_manager.update_task_status(
                                        task_to_process.task_id,
                                        TaskStatus.FAILED,
                                        error_message=f"SystemController error during processing: {str(e_single_task)}",
                                    )
                                    await self.progress_publisher.publish_progress(
                                        task_to_process.task_id,
                                        ProgressEventType.TASK_FAILED,
                                        {
                                            "error": f"SystemController error: {str(e_single_task)}"
                                        },
                                        message=f"Task failed due to SystemController error: {str(e_single_task)}",
                                    )
                                except Exception as e_status_update:
                                    logger.error(
                                        f"SystemController: CRITICAL - Failed to update task status to FAILED for {task_to_process.task_id} after _process_single_task error: {e_status_update}"
                                    )
                        finally:

                            if self.current_task_id == task_to_process.task_id:
                                logger.info(
                                    f"SystemController: Clearing self.current_task_id (was {self.current_task_id}) after processing task {task_to_process.task_id}."
                                )
                                self.current_task_id = None
                            else:
                                logger.warning(
                                    f"SystemController: self.current_task_id ({self.current_task_id}) changed during _process_single_task for {task_to_process.task_id} or was already None. This is unexpected."
                                )
                    else:
                        logger.debug("SystemController: No queued tasks found.")
                else:
                    logger.debug(
                        f"SystemController: Already processing task {self.current_task_id} or in cooldown. Waiting."
                    )

                if not task_processed_in_this_iteration or self.current_task_id:
                    await asyncio.sleep(1)

            except Exception as e_queue_loop:
                logger.error(
                    f"SystemController: Unhandled error in outer _process_task_queue loop: {e_queue_loop}",
                    exc_info=True,
                )

                if self.current_task_id:
                    logger.error(
                        f"SystemController: Outer loop error occurred while current_task_id was {self.current_task_id}. This task might be stuck."
                    )
                await asyncio.sleep(5)

        logger.info(
            "SystemController: _process_task_queue loop ended because self.is_running is false."
        )

    async def _process_single_task(self, task):
        """Process a single task through the agent system."""
        try:
            await self.task_manager.update_task_status(
                task.task_id, TaskStatus.ANALYZING
            )

            await self._create_agents_for_task(task)

            for agent_id, agent in self.agents.items():
                await agent.initialize_agent()
                logger.info(
                    f"✅ Agent {agent_id} initialized and event subscriptions set up"
                )

            agent_start_tasks = []
            for agent_id, agent in self.agents.items():
                agent_start_tasks.append(asyncio.create_task(agent.start()))
                self.agent_tasks[agent_id] = agent_start_tasks[-1]

            await asyncio.sleep(0.5)

            master_coordinator = self.agents.get("master_coordinator")
            if master_coordinator:
                await master_coordinator.start_feature_processing(
                    task.feature_specification
                )

                while master_coordinator.running and self.is_running:
                    await asyncio.sleep(1)

                if master_coordinator.running:
                    await self.task_manager.update_task_status(
                        task.task_id, TaskStatus.CANCELLED
                    )
                else:
                    await self.task_manager.update_task_status(
                        task.task_id, TaskStatus.COMPLETED
                    )
                    await self.progress_publisher.publish_progress(
                        task.task_id,
                        ProgressEventType.TASK_COMPLETED,
                        {},
                        message="Feature implementation completed successfully",
                    )

        except Exception as e:
            await self.task_manager.update_task_status(
                task.task_id, TaskStatus.FAILED, error_message=str(e)
            )
            await self.progress_publisher.publish_progress(
                task.task_id,
                ProgressEventType.TASK_FAILED,
                {"error": str(e)},
                message=f"Task failed: {str(e)}",
            )

        finally:
            task_id_for_log = (
                task.task_id if hasattr(task, "task_id") else "UNKNOWN_TASK"
            )
            logger.info(
                f"SystemController: Entering finally block of _process_single_task for task {task_id_for_log}. Calling _cleanup_agents."
            )
            await self._cleanup_agents()
            logger.info(
                f"SystemController: Exiting _process_single_task for task {task_id_for_log} after _cleanup_agents."
            )

    async def _create_agents_for_task(self, task):
        """Create and configure agents for processing a task."""
        await self._cleanup_agents()

        shared_event_bus = EventBus(self.db_path)
        await shared_event_bus.initialize()

        self.agents["master_coordinator"] = MasterCoordinatorAgent(
            AgentConfig(
                agent_id="master_coordinator",
                event_bus_db_path=self.db_path,
                gemini_api_key=self.gemini_api_key,
            ),
            task.repo_path,
            shared_event_bus=shared_event_bus,
        )

        self.agents["feature_analyzer"] = FeatureAnalyzerAgent(
            AgentConfig(
                agent_id="feature_analyzer",
                event_bus_db_path=self.db_path,
                gemini_api_key=self.gemini_api_key,
            ),
            shared_event_bus=shared_event_bus,
        )

        repo_name = os.getenv("GITHUB_REPO_NAME") or Path(task.repo_path).name

        self.agents["pr_generator"] = PRGeneratorAgent(
            AgentConfig(
                agent_id="pr_generator",
                event_bus_db_path=self.db_path,
                gemini_api_key=self.gemini_api_key,
            ),
            task.repo_path,
            self.github_token,
            self.github_username,
            repo_name,
            shared_event_bus=shared_event_bus,
        )

        shared_event_bus.subscribe(
            CoreEventType.CHUNK_STARTED, self._handle_agent_event
        )
        shared_event_bus.subscribe(
            CoreEventType.CODE_GENERATION_STARTED, self._handle_agent_event
        )
        shared_event_bus.subscribe(
            CoreEventType.FILES_MODIFIED, self._handle_agent_event
        )
        shared_event_bus.subscribe(CoreEventType.PR_CREATED, self._handle_agent_event)
        shared_event_bus.subscribe(CoreEventType.PR_MERGED, self._handle_agent_event)
        shared_event_bus.subscribe(
            CoreEventType.BRANCH_DELETED, self._handle_agent_event
        )

        logger.info(
            "SystemController subscribed to core agent events for progress publishing."
        )

    async def _handle_agent_event(self, core_event: CoreEvent):
        """Handles core agent events and publishes them as progress events."""
        if not self.current_task_id:
            logger.warning(
                f"Received agent event {core_event.event_type} but no current_task_id is set. Ignoring."
            )
            return

        logger.debug(
            f"SystemController handling core event: {core_event.event_type} for task {self.current_task_id}"
        )

        progress_event_type: Optional[ProgressEventType] = None
        message: Optional[str] = None
        data = core_event.data.copy()

        if core_event.event_type == CoreEventType.CHUNK_STARTED:
            progress_event_type = ProgressEventType.CHUNK_PROCESSING_STARTED
            msg_parts = [f"Chunk Started: {data.get('chunk_id', 'N/A')}"]
            if data.get("description"):
                msg_parts.append(f"- {data.get('description')}")
            if data.get("files"):
                msg_parts.append(f"\n   Files: {', '.join(data.get('files'))}")
            message = " ".join(msg_parts)

        elif core_event.event_type == CoreEventType.CODE_GENERATION_STARTED:
            progress_event_type = ProgressEventType.AGENT_CODE_GENERATION_STARTED
            message = f"Code Generation Started: Chunk {data.get('chunk_id', 'N/A')}"
            if data.get("description"):
                message += f" - {data.get('description')}"

        elif core_event.event_type == CoreEventType.FILES_MODIFIED:
            progress_event_type = ProgressEventType.AGENT_FILES_MODIFIED
            files_modified = data.get("modified_files", [])
            message = f"Files Modified for chunk {data.get('chunk_id', 'N/A')}: {', '.join(files_modified) if files_modified else 'None'}"

        elif core_event.event_type == CoreEventType.PR_CREATED:
            progress_event_type = ProgressEventType.PR_CREATED
            msg_parts = [f"PR Created: #{data.get('pr_number', 'N/A')}"]
            if data.get("pr_title"):
                msg_parts.append(f"- {data.get('pr_title')}")
            if data.get("url"):
                msg_parts.append(f"\n   URL: {data.get('url')}")
            message = " ".join(msg_parts)

        elif core_event.event_type == CoreEventType.PR_MERGED:
            progress_event_type = ProgressEventType.PR_MERGED
            message = f"PR Merged: #{data.get('pr_number', 'N/A')} for chunk {data.get('chunk_id', 'N/A')}"

        elif core_event.event_type == CoreEventType.BRANCH_DELETED:
            progress_event_type = ProgressEventType.AGENT_BRANCH_DELETED
            message = f"Branch Deleted: {data.get('branch_name', 'N/A')} for PR #{data.get('pr_number', 'N/A')}"

        else:
            logger.debug(
                f"Unhandled core event type by SystemController: {core_event.event_type}"
            )
            return

        if progress_event_type:
            try:
                await self.progress_publisher.publish_progress(
                    task_id=self.current_task_id,
                    event_type=progress_event_type,
                    data=data,
                    message=message,
                )
                logger.info(
                    f"Published progress event {progress_event_type.value} for task {self.current_task_id}"
                )
            except Exception as e:
                logger.error(
                    f"Error publishing progress event from SystemController: {e}",
                    exc_info=True,
                )

    async def _cleanup_agents(self):
        """Clean up all agents and their tasks."""
        for agent in self.agents.values():
            try:
                await agent.stop()
            except Exception as e:
                logger.error(f"Error stopping agent: {e}")

        for task in self.agent_tasks.values():
            if not task.done():
                task.cancel()

        self.agents.clear()
        self.agent_tasks.clear()

    async def _check_database_connection(self) -> bool:
        """Check if database connection is working."""
        try:
            await self.task_manager.initialize()
            return True
        except Exception:
            return False

    async def validate_gemini_api_key(
        self, api_key_to_validate: Optional[str] = None
    ) -> Dict[str, Any]:
        """Validate the Gemini API key by making a test call."""
        key = (
            api_key_to_validate
            if api_key_to_validate is not None
            else self.gemini_api_key
        )

        if not key:
            return {
                "valid": False,
                "message": "Gemini API key is not provided.",
                "status_code": None,
            }

        def sync_validate_with_langchain(api_key: str):
            try:

                llm = ChatGoogleGenerativeAI(
                    model="gemini-2.5-flash-preview-05-20",
                    google_api_key=api_key,
                    temperature=0.0,
                )

                llm.invoke("test")
                return {
                    "valid": True,
                    "message": "Gemini API key is valid.",
                    "status_code": 200,
                }

            except Exception as e:

                key_suffix = api_key[-4:] if len(api_key) >= 4 else "INVALID_LENGTH"
                logger.warning(
                    f"Gemini API key validation failed for key ending ...{key_suffix}: {type(e).__name__} - {str(e)}"
                )

                status_code = getattr(e, "code", getattr(e, "status_code", None))

                user_message = f"Validation failed: {type(e).__name__}."
                if "API key not valid" in str(e) or (
                    hasattr(e, "args")
                    and e.args
                    and "API key not valid" in str(e.args[0])
                ):
                    user_message = "API key not valid. Please check your key."
                elif status_code == 403:
                    user_message = "Permission denied. The API key may not have access to the Gemini API."
                elif status_code == 400:
                    user_message = "Bad request. The API key might be malformed or the model is not accessible with this key."

                return {
                    "valid": False,
                    "message": user_message,
                    "status_code": status_code,
                }

        result = await asyncio.to_thread(sync_validate_with_langchain, key)
        return result

    async def validate_github_credentials(
        self, token: Optional[str] = None, username: Optional[str] = None
    ) -> Dict[str, Any]:
        """Validate GitHub credentials by attempting to get the authenticated user."""
        token_to_validate = token if token is not None else self.github_token
        username_to_validate = (
            username if username is not None else self.github_username
        )

        if not token_to_validate:
            return {"valid": False, "message": "GitHub token is not provided."}
        if not username_to_validate:
            return {"valid": False, "message": "GitHub username is not provided."}

        from github import Github, BadCredentialsException, GithubException

        def sync_validate_github(gh_token: str, gh_username: str):
            try:
                g = Github(gh_token)
                user = g.get_user()

                if user.login.lower() != gh_username.lower():
                    logger.warning(
                        f"Authenticated GitHub user '{user.login}' does not match provided username '{gh_username}'. "
                        f"The token is valid for '{user.login}'."
                    )

                    return {
                        "valid": False,
                        "message": f"Token valid for '{user.login}', not '{gh_username}'.",
                    }

                logger.info(
                    f"Successfully authenticated with GitHub as user: {user.login}"
                )
                return {"valid": True, "message": "GitHub credentials are valid."}
            except BadCredentialsException:
                logger.warning(
                    f"GitHub BadCredentialsException: Invalid token for user '{gh_username}'."
                )
                return {
                    "valid": False,
                    "message": "Invalid GitHub token.",
                }
            except GithubException as e:
                error_message = (
                    e.data.get("message", "Unknown GitHub API error")
                    if hasattr(e, "data") and isinstance(e.data, dict)
                    else str(e)
                )
                logger.error(
                    f"GitHub API error during validation: {e.status} - {error_message}"
                )
                return {
                    "valid": False,
                    "message": f"GitHub API error: {error_message} (Status: {e.status})",
                }
            except Exception as e:
                logger.error(
                    f"Unexpected error during GitHub validation: {e}", exc_info=True
                )
                return {"valid": False, "message": f"Unexpected error: {str(e)}"}

        result = await asyncio.to_thread(
            sync_validate_github, token_to_validate, username_to_validate
        )
        return result
