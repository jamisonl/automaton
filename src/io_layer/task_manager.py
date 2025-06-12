"""Task management for the agent system."""

import asyncio
import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, AsyncIterator
from pydantic import BaseModel
import aiosqlite


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    CHUNKING = "chunking"
    PROCESSING_CHUNKS = "processing_chunks"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(BaseModel):
    task_id: str
    repo_path: str
    feature_specification: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    total_chunks: Optional[int] = None
    completed_chunks: int = 0
    github_prs: List[int] = []

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class TaskManager:
    """Manages task lifecycle, queuing, and status tracking."""

    def __init__(self, db_path: str = "coordination.db"):
        self.db_path = db_path
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self._initialized = False

    async def initialize(self):
        """Initialize the task management database tables."""
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    repo_path TEXT NOT NULL,
                    feature_specification TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_message TEXT,
                    total_chunks INTEGER,
                    completed_chunks INTEGER DEFAULT 0,
                    github_prs TEXT DEFAULT '[]'
                )
            """
            )
            await db.commit()

        self._initialized = True

    async def submit_task(self, repo_path: str, feature_specification: str) -> str:
        """Submit a new task for processing."""
        await self.initialize()

        task_id = str(uuid.uuid4())
        now = datetime.now()

        task = Task(
            task_id=task_id,
            repo_path=repo_path,
            feature_specification=feature_specification,
            status=TaskStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO tasks (
                    task_id, repo_path, feature_specification, status,
                    created_at, updated_at, completed_at, error_message,
                    total_chunks, completed_chunks, github_prs
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    task.task_id,
                    task.repo_path,
                    task.feature_specification,
                    task.status,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.error_message,
                    task.total_chunks,
                    task.completed_chunks,
                    json.dumps(task.github_prs),
                ),
            )
            await db.commit()

        return task_id

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_message: Optional[str] = None,
        total_chunks: Optional[int] = None,
        completed_chunks: Optional[int] = None,
        github_prs: Optional[List[int]] = None,
    ):
        """Update task status and related fields."""
        await self.initialize()

        now = datetime.now()
        updates = ["status = ?", "updated_at = ?"]
        params = [status, now.isoformat()]

        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if total_chunks is not None:
            updates.append("total_chunks = ?")
            params.append(total_chunks)

        if completed_chunks is not None:
            updates.append("completed_chunks = ?")
            params.append(completed_chunks)

        if github_prs is not None:
            updates.append("github_prs = ?")
            params.append(json.dumps(github_prs))

        if status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            updates.append("completed_at = ?")
            params.append(now.isoformat())

        params.append(task_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?", params
            )
            await db.commit()

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a specific task by ID."""
        await self.initialize()

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM tasks WHERE task_id = ?", [task_id]
            ) as cursor:
                row = await cursor.fetchone()

                if row:
                    return self._row_to_task(row)
                return None

    async def get_tasks(
        self, status: Optional[TaskStatus] = None, limit: Optional[int] = None
    ) -> List[Task]:
        """Get tasks, optionally filtered by status."""
        await self.initialize()

        query = "SELECT * FROM tasks"
        params = []

        if status:
            query += " WHERE status = ?"
            params.append(status)

        query += " ORDER BY created_at ASC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_task(row) for row in rows]

    async def get_active_tasks(self) -> List[Task]:
        """Get currently active (non-terminal) tasks."""
        active_statuses = [
            TaskStatus.QUEUED,
            TaskStatus.ANALYZING,
            TaskStatus.CHUNKING,
            TaskStatus.PROCESSING_CHUNKS,
            TaskStatus.MERGING,
        ]

        await self.initialize()

        placeholders = ",".join("?" * len(active_statuses))
        query = f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at ASC"

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, active_statuses) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_task(row) for row in rows]

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a task if it's not already completed."""
        task = await self.get_task(task_id)
        if not task:
            return False

        if task.status in [
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ]:
            return False

        if task_id in self.active_tasks:
            self.active_tasks[task_id].cancel()
            del self.active_tasks[task_id]

        await self.update_task_status(task_id, TaskStatus.CANCELLED)
        return True

    def _row_to_task(self, row) -> Task:
        """Convert database row to Task object."""
        return Task(
            task_id=row[0],
            repo_path=row[1],
            feature_specification=row[2],
            status=TaskStatus(row[3]),
            created_at=datetime.fromisoformat(row[4]),
            updated_at=datetime.fromisoformat(row[5]),
            completed_at=datetime.fromisoformat(row[6]) if row[6] else None,
            error_message=row[7],
            total_chunks=row[8],
            completed_chunks=row[9] or 0,
            github_prs=json.loads(row[10]) if row[10] else [],
        )
