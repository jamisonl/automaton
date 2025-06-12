"""Master coordinator agent that orchestrates the entire workflow."""

import asyncio
import json
from typing import Dict, Any, List
from pathlib import Path
import pathspec
import traceback
from agents.base import BaseAgent, AgentConfig, DSPyModule
from core.events import EventType, Event
from core.coordination import Chunk, ChunkStatus
from core.logger import logger


class MasterCoordinatorAgent(BaseAgent):
    def __init__(
        self, config: AgentConfig, target_repo_path: str, shared_event_bus=None
    ):
        super().__init__(config, shared_event_bus)
        self.target_repo_path = Path(target_repo_path)
        self.dspy_module = DSPyModule()
        self.current_feature_spec = None
        self.current_feature_id = None
        self.chunks_created = False

    async def setup_event_subscriptions(self):
        self.event_bus.subscribe(
            EventType.FEATURE_ANALYZED, self.handle_feature_analyzed
        )
        self.event_bus.subscribe(EventType.CHUNKS_PLANNED, self.handle_chunks_planned)
        self.event_bus.subscribe(EventType.CHUNK_STARTED, self.handle_chunk_started)
        self.event_bus.subscribe(EventType.CHUNK_COMPLETED, self.handle_chunk_completed)
        self.event_bus.subscribe(EventType.PR_REVIEWED, self.handle_pr_reviewed)

    async def run(self):
        """Main coordinator loop."""
        logger.info(f"Coordinator {self.agent_id} started run loop.")

        while self.running:
            available_chunks = await self.get_feature_available_chunks()

            if available_chunks:
                logger.info(
                    f"📋 Found {len(available_chunks)} available chunks to assign"
                )
                for chunk in available_chunks:
                    await self.assign_chunk_to_agent(chunk)
            else:
                if self.current_feature_id:
                    feature_chunks = await self.get_feature_chunks()
                    if feature_chunks:
                        status_summary = {}
                        for chunk in feature_chunks:
                            status_summary[chunk.status] = (
                                status_summary.get(chunk.status, 0) + 1
                            )
                        logger.debug(
                            f"🔍 No available chunks for current feature. Status: {status_summary}"
                        )

            await self.coordinate_merging()

            if await self.is_feature_complete():
                logger.info("🎉 All chunks completed! Feature implementation finished.")
                logger.info("✅ Shutting down all agents...")

                await self.publish_event(
                    EventType.FEATURE_COMPLETED,
                    {
                        "feature_id": self.current_feature_id,
                        "message": "Feature implementation completed successfully",
                    },
                )

                await self.stop()
                break

            await asyncio.sleep(5)

    async def start_feature_processing(self, feature_specification: str):
        logger.debug(f"start_feature_processing called with: {feature_specification}")

        self.current_feature_spec = feature_specification

        import re
        import time

        spec_prefix = "_".join(feature_specification.lower().split()[:5])
        sanitized_prefix = re.sub(r"\W+", "", spec_prefix)
        sanitized_prefix = sanitized_prefix[:30]
        self.current_feature_id = f"{sanitized_prefix}_{int(time.time())}"

        logger.debug(
            f"current_feature_spec set. current_feature_id generated: {self.current_feature_id}"
        )

        logger.debug("About to get repository structure...")
        try:
            repo_structure = self.get_repository_structure()
            logger.debug(
                f"Repository structure obtained, {len(repo_structure.splitlines())} files found"
            )
        except Exception as e:
            logger.error(f"Error getting repository structure: {e}")
            raise

        logger.debug("About to publish ANALYZE_FEATURE event...")
        logger.info(
            f"MasterCoordinator: About to publish ANALYZE_FEATURE for {self.current_feature_id}"
        )
        try:
            logger.debug("Calling self.publish_event...")
            event_id = await self.publish_event(
                EventType.ANALYZE_FEATURE,
                {
                    "feature_specification": feature_specification,
                    "repository_structure": repo_structure,
                },
            )
            logger.debug(
                f"ANALYZE_FEATURE event published successfully with ID: {event_id}"
            )
        except Exception as e:
            logger.exception(f"Error publishing ANALYZE_FEATURE event:")
            raise

        logger.debug("start_feature_processing method completing...")

    def get_repository_structure(self) -> str:
        structure_list = []

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
                    f"Could not read or parse .gitignore file at {gitignore_file_path}: {e}. Proceeding with fallback ignores."
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
                "No .gitignore found or parsed. Using default hardcoded ignores for repository structure."
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

                structure_list.append(str(relative_path_for_spec))

        return "\n".join(sorted(list(set(structure_list))))

    async def handle_feature_analyzed(self, event: Event):
        if event.agent_id == self.agent_id:
            logger.debug(
                "MasterCoordinator ignoring own FEATURE_ANALYZED event (if self-published, though not typical)."
            )
            return

        logger.info(f"Feature analysis completed by {event.agent_id}: {event.data}")

        analysis_data = event.data

        from agents.base import FeatureAnalysisResult

        analysis_result = FeatureAnalysisResult(**analysis_data)

        chunk_plans = self.dspy_module.plan_chunks(analysis_result)

        if not self.current_feature_id:
            logger.error(
                "current_feature_id is not set. Cannot create unique chunk IDs."
            )
            return

        created_chunk_ids = []
        for i, plan in enumerate(chunk_plans):
            unique_chunk_id = f"{self.current_feature_id}_{plan.chunk_id}"

            prefixed_dependencies = []
            for dep in plan.dependencies:
                if dep.startswith(self.current_feature_id):
                    prefixed_dependencies.append(dep)
                else:
                    prefixed_dependencies.append(f"{self.current_feature_id}_{dep}")

            logger.debug(
                f"Creating chunk {unique_chunk_id} with dependencies: {prefixed_dependencies}"
            )

            chunk = Chunk(
                chunk_id=unique_chunk_id,
                description=plan.description,
                status=ChunkStatus.PLANNED,
                files=plan.files,
                dependencies=prefixed_dependencies,
                pr_number=None,
            )
            await self.coordination.create_chunk(chunk)
            created_chunk_ids.append(unique_chunk_id)

        await self.publish_event(
            EventType.CHUNKS_PLANNED,
            {
                "feature_id": self.current_feature_id,
                "total_chunks": len(chunk_plans),
                "chunk_ids": created_chunk_ids,
            },
        )

        self.chunks_created = True

    async def handle_chunks_planned(self, event: Event):
        logger.info(f"Chunks planned: {event.data}")

    async def handle_chunk_started(self, event: Event):
        chunk_id = event.data.get("chunk_id")
        logger.info(f"🚀 Chunk {chunk_id} started processing by {event.agent_id}")

    async def assign_chunk_to_agent(self, chunk: Chunk):

        await self.coordination.update_chunk_status(
            chunk.chunk_id,
            ChunkStatus.IN_PROGRESS,
            assigned_agent="pr_generator",
        )

        await self.publish_event(
            EventType.CHUNK_ASSIGNED,
            {
                "chunk_id": chunk.chunk_id,
                "description": chunk.description,
                "files": chunk.files,
                "assigned_agent": "pr_generator",
            },
        )

        logger.info(f"Assigned chunk {chunk.chunk_id} to pr_generator")

    async def handle_chunk_completed(self, event: Event):
        chunk_id = event.data.get("chunk_id")
        pr_number = event.data.get("pr_number")

        logger.info(f"Chunk {chunk_id} completed with PR #{pr_number}")

        await self.coordination.update_chunk_status(
            chunk_id, ChunkStatus.COMPLETE, pr_number=pr_number
        )

    async def handle_pr_reviewed(self, event: Event):
        chunk_id = event.data.get("chunk_id")
        pr_number = event.data.get("pr_number")
        approved = event.data.get("approved", False)

        if approved:
            logger.info(
                f"PR #{pr_number} for chunk {chunk_id} approved, ready for merge"
            )

    async def coordinate_merging(self):
        if not self.current_feature_id:
            return

        feature_chunks = await self.get_feature_chunks()
        completed_chunks = [
            chunk for chunk in feature_chunks if chunk.status == ChunkStatus.COMPLETE
        ]
        merged_chunks = [
            chunk for chunk in feature_chunks if chunk.status == ChunkStatus.MERGED
        ]
        merged_ids = {c.chunk_id for c in merged_chunks}

        for chunk in completed_chunks:
            deps_merged = all(dep_id in merged_ids for dep_id in chunk.dependencies)

            if deps_merged and chunk.pr_number:
                logger.info(
                    f"Chunk {chunk.chunk_id} ready to merge (dependencies satisfied)"
                )

                await self.merge_pr(chunk.chunk_id, chunk.pr_number)

                await self.coordination.update_chunk_status(
                    chunk.chunk_id, ChunkStatus.MERGED
                )

                merged_ids.add(chunk.chunk_id)

    async def merge_pr(self, chunk_id: str, pr_number: int):

        await self.publish_event(
            EventType.MERGE_PR,
            {
                "chunk_id": chunk_id,
                "pr_number": pr_number,
            },
        )

    async def get_feature_chunks(self) -> List[Chunk]:
        if not self.current_feature_id:
            return []

        all_chunks = await self.coordination.get_chunks()
        return [
            chunk
            for chunk in all_chunks
            if chunk.chunk_id.startswith(self.current_feature_id)
        ]

    async def get_feature_available_chunks(self) -> List[Chunk]:
        if not self.current_feature_id:
            logger.debug("No current_feature_id set")
            return []

        feature_chunks = await self.get_feature_chunks()
        logger.debug(f"Found {len(feature_chunks)} feature chunks")

        planned_chunks = [
            chunk for chunk in feature_chunks if chunk.status == ChunkStatus.PLANNED
        ]
        logger.debug(f"Found {len(planned_chunks)} planned chunks")

        locked_files = {
            lock.file_path for lock in await self.coordination.get_locked_files()
        }
        logger.debug(f"Found {len(locked_files)} locked files")

        completed_chunks = [
            chunk
            for chunk in feature_chunks
            if chunk.status in [ChunkStatus.COMPLETE, ChunkStatus.MERGED]
        ]
        completed_ids = {chunk.chunk_id for chunk in completed_chunks}
        logger.debug(
            f"Found {len(completed_ids)} completed chunk IDs: {list(completed_ids)}"
        )

        available_chunks = []
        for chunk in planned_chunks:
            logger.debug(
                f"Checking chunk {chunk.chunk_id} with dependencies: {chunk.dependencies}"
            )

            deps_satisfied = all(
                dep_id in completed_ids for dep_id in chunk.dependencies
            )
            logger.debug(
                f"Dependencies satisfied for {chunk.chunk_id}: {deps_satisfied}"
            )

            files_available = not any(
                file_path in locked_files for file_path in chunk.files
            )
            logger.debug(f"Files available for {chunk.chunk_id}: {files_available}")

            if not deps_satisfied:
                missing_deps = [
                    dep for dep in chunk.dependencies if dep not in completed_ids
                ]
                logger.debug(
                    f"🔍 Chunk {chunk.chunk_id} waiting for dependencies: {missing_deps}"
                )
                logger.debug(f"   Available completed IDs: {list(completed_ids)}")

            if deps_satisfied and files_available:
                logger.debug(f"Adding {chunk.chunk_id} to available chunks")
                available_chunks.append(chunk)
            else:
                logger.debug(
                    f"Skipping {chunk.chunk_id} - deps_satisfied={deps_satisfied}, files_available={files_available}"
                )

        logger.debug(f"Returning {len(available_chunks)} available chunks")
        return available_chunks

    async def is_feature_complete(self) -> bool:
        if not self.current_feature_id or not self.chunks_created:
            return False

        feature_chunks = await self.get_feature_chunks()
        if not feature_chunks:
            return False

        merged_chunks = [
            chunk for chunk in feature_chunks if chunk.status == ChunkStatus.MERGED
        ]

        all_merged = len(merged_chunks) == len(feature_chunks)

        if all_merged:
            logger.info(
                f"🎯 Feature complete: {len(merged_chunks)}/{len(feature_chunks)} chunks merged"
            )

        return all_merged

    async def get_status(self) -> Dict[str, Any]:

        all_chunks = await self.coordination.get_chunks()

        status_counts = {}
        for chunk in all_chunks:
            status_counts[chunk.status] = status_counts.get(chunk.status, 0) + 1

        return {
            "total_chunks": len(all_chunks),
            "status_breakdown": status_counts,
            "chunks_created": self.chunks_created,
            "current_feature_active": self.current_feature_id is not None,
            "current_feature_spec": (
                self.current_feature_spec if self.current_feature_id else None
            ),
        }
