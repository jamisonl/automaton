"""Progress publishing and real-time event streaming."""

import asyncio
import json
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, AsyncIterator, Any
from pydantic import BaseModel
import aiosqlite


class ProgressEventType(str, Enum):
    TASK_STARTED = "task_started"
    FEATURE_ANALYSIS_STARTED = "feature_analysis_started"
    FEATURE_ANALYSIS_COMPLETED = "feature_analysis_completed"
    CHUNKING_STARTED = "chunking_started"
    CHUNKING_COMPLETED = "chunking_completed"
    CHUNK_PROCESSING_STARTED = "chunk_processing_started"
    CHUNK_PROCESSING_COMPLETED = "chunk_processing_completed"
    PR_CREATED = "pr_created"
    PR_MERGED = "pr_merged"
    MERGING_STARTED = "merging_started"
    MERGING_COMPLETED = "merging_completed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"
    ERROR_OCCURRED = "error_occurred"
    AGENT_CODE_GENERATION_STARTED = "agent_code_generation_started"
    AGENT_FILES_MODIFIED = "agent_files_modified"
    AGENT_BRANCH_DELETED = "agent_branch_deleted"


class ProgressEvent(BaseModel):
    event_id: str
    task_id: str
    event_type: ProgressEventType
    timestamp: datetime
    data: Dict[str, Any]
    message: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ChunkProgress(BaseModel):
    chunk_id: str
    status: str
    description: str
    files_affected: List[str]
    pr_number: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class TaskSummary(BaseModel):
    task_id: str
    status: str
    feature_specification: str
    repo_path: str
    total_chunks: Optional[int] = None
    completed_chunks: int = 0
    chunks: List[ChunkProgress] = []
    github_prs: List[int] = []
    progress_percentage: float = 0.0
    current_phase: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    error_message: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class ProgressPublisher:
    """Manages progress events and real-time streaming."""

    def __init__(self, db_path: str = "coordination.db"):
        self.db_path = db_path
        self.subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._initialized = False

    async def initialize(self):
        """Initialize the progress tracking database tables."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS progress_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    data TEXT NOT NULL,
                    message TEXT
                )
            """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_progress_task_id ON progress_events(task_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_progress_timestamp ON progress_events(timestamp)"
            )
            await db.commit()

        self._initialized = True

    async def publish_progress(
        self,
        task_id: str,
        event_type: ProgressEventType,
        data: Dict[str, Any],
        message: Optional[str] = None,
    ) -> str:
        """Publish a progress event."""
        await self.initialize()

        import uuid

        event_id = str(uuid.uuid4())
        timestamp = datetime.now()

        event = ProgressEvent(
            event_id=event_id,
            task_id=task_id,
            event_type=event_type,
            timestamp=timestamp,
            data=data,
            message=message,
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO progress_events (event_id, task_id, event_type, timestamp, data, message)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    event.event_id,
                    event.task_id,
                    event.event_type,
                    event.timestamp.isoformat(),
                    json.dumps(event.data),
                    event.message,
                ),
            )
            await db.commit()

        await self._notify_subscribers(task_id, event)

        return event_id

    async def subscribe_to_progress(self, task_id: str) -> AsyncIterator[ProgressEvent]:
        """Subscribe to progress events for a specific task."""
        queue = asyncio.Queue()

        if task_id not in self.subscribers:
            self.subscribers[task_id] = []
        self.subscribers[task_id].append(queue)

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if task_id in self.subscribers:
                if queue in self.subscribers[task_id]:
                    self.subscribers[task_id].remove(queue)
                if not self.subscribers[task_id]:
                    del self.subscribers[task_id]

    async def subscribe_to_all_progress(self) -> AsyncIterator[ProgressEvent]:
        """Subscribe to all progress events across all tasks."""
        queue = asyncio.Queue()

        global_key = "__ALL__"
        if global_key not in self.subscribers:
            self.subscribers[global_key] = []
        self.subscribers[global_key].append(queue)

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if global_key in self.subscribers:
                if queue in self.subscribers[global_key]:
                    self.subscribers[global_key].remove(queue)
                if not self.subscribers[global_key]:
                    del self.subscribers[global_key]

    async def get_task_events(
        self, task_id: str, limit: Optional[int] = None
    ) -> List[ProgressEvent]:
        """Get progress events for a specific task."""
        await self.initialize()

        query = (
            "SELECT * FROM progress_events WHERE task_id = ? ORDER BY timestamp DESC"
        )
        params = [task_id]

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_event(row) for row in rows]

    async def get_recent_events(self, limit: int = 100) -> List[ProgressEvent]:
        """Get recent progress events across all tasks."""
        await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM progress_events ORDER BY timestamp DESC LIMIT ?",
                [limit],
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_event(row) for row in rows]

    async def get_task_summary(self, task_id: str) -> Optional[TaskSummary]:
        """Get a comprehensive summary of task progress."""
        await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM tasks WHERE task_id = ?", [task_id]
            ) as cursor:
                task_row = await cursor.fetchone()

            if not task_row:
                return None

            chunks = []
            async with db.execute(
                "SELECT * FROM chunks WHERE chunk_id LIKE ?", [f"{task_id}_%"]
            ) as cursor:
                chunk_rows = await cursor.fetchall()

                for chunk_row in chunk_rows:
                    chunk_progress = ChunkProgress(
                        chunk_id=chunk_row[0],
                        status=chunk_row[2],
                        description=chunk_row[1],
                        files_affected=json.loads(chunk_row[4]),
                        pr_number=chunk_row[6],
                    )
                    chunks.append(chunk_progress)

        total_chunks = task_row[8] or len(chunks)
        completed_chunks = task_row[9] or 0
        progress_percentage = (
            (completed_chunks / total_chunks * 100) if total_chunks > 0 else 0
        )

        current_phase = self._determine_current_phase(task_row[3], chunks)

        return TaskSummary(
            task_id=task_row[0],
            status=task_row[3],
            feature_specification=task_row[2],
            repo_path=task_row[1],
            total_chunks=total_chunks,
            completed_chunks=completed_chunks,
            chunks=chunks,
            github_prs=json.loads(task_row[10]) if task_row[10] else [],
            progress_percentage=progress_percentage,
            current_phase=current_phase,
            created_at=datetime.fromisoformat(task_row[4]),
            updated_at=datetime.fromisoformat(task_row[5]),
            error_message=task_row[7],
        )

    async def _notify_subscribers(self, task_id: str, event: ProgressEvent):
        """Notify all subscribers of a new progress event."""
        if task_id in self.subscribers:
            for queue in self.subscribers[task_id]:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

        global_key = "__ALL__"
        if global_key in self.subscribers:
            for queue in self.subscribers[global_key]:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def _row_to_event(self, row) -> ProgressEvent:
        """Convert database row to ProgressEvent object."""
        return ProgressEvent(
            event_id=row[0],
            task_id=row[1],
            event_type=ProgressEventType(row[2]),
            timestamp=datetime.fromisoformat(row[3]),
            data=json.loads(row[4]),
            message=row[5],
        )

    def _determine_current_phase(self, status: str, chunks: List[ChunkProgress]) -> str:
        """Determine the current phase based on task status and chunk states."""
        if status == "queued":
            return "Queued"
        elif status == "analyzing":
            return "Analyzing Feature"
        elif status == "chunking":
            return "Planning Chunks"
        elif status == "processing_chunks":
            completed = sum(1 for c in chunks if c.status in ["complete", "merged"])
            total = len(chunks)
            return f"Processing Chunks ({completed}/{total})"
        elif status == "merging":
            return "Merging Pull Requests"
        elif status == "completed":
            return "Completed"
        elif status == "failed":
            return "Failed"
        elif status == "cancelled":
            return "Cancelled"
        else:
            return "Unknown"
