"""File coordination and locking mechanism."""

from typing import List, Optional, Set
from datetime import datetime
import json
import aiosqlite
from pydantic import BaseModel


class ChunkStatus:
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    MERGED = "merged"


class Chunk(BaseModel):
    chunk_id: str
    description: str
    status: str
    assigned_agent: Optional[str] = None
    files: List[str]
    dependencies: List[str]
    pr_number: Optional[int] = None


class FileLock(BaseModel):
    file_path: str
    agent_id: str
    locked_at: datetime
    chunk_id: str


class CoordinationManager:
    def __init__(self, db_path: str = "coordination.db"):
        self.db_path = db_path

    async def acquire_file_locks(
        self, agent_id: str, chunk_id: str, file_paths: List[str]
    ) -> bool:
        """Atomically acquire locks for multiple files. Returns True if all locks acquired."""
        async with aiosqlite.connect(self.db_path) as db:
            placeholders = ",".join("?" * len(file_paths))
            async with db.execute(
                f"SELECT file_path FROM file_locks WHERE file_path IN ({placeholders})",
                file_paths,
            ) as cursor:
                locked_files = await cursor.fetchall()

            if locked_files:
                return False

            timestamp = datetime.now().isoformat()
            for file_path in file_paths:
                await db.execute(
                    "INSERT INTO file_locks (file_path, agent_id, locked_at, chunk_id) VALUES (?, ?, ?, ?)",
                    (file_path, agent_id, timestamp, chunk_id),
                )

            await db.commit()
            return True

    async def release_file_locks(self, agent_id: str, chunk_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM file_locks WHERE agent_id = ? AND chunk_id = ?",
                (agent_id, chunk_id),
            )
            await db.commit()

    async def get_locked_files(self, agent_id: Optional[str] = None) -> List[FileLock]:
        async with aiosqlite.connect(self.db_path) as db:
            if agent_id:
                query = "SELECT * FROM file_locks WHERE agent_id = ?"
                params = [agent_id]
            else:
                query = "SELECT * FROM file_locks"
                params = []

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

                locks = []
                for row in rows:
                    locks.append(
                        FileLock(
                            file_path=row[0],
                            agent_id=row[1],
                            locked_at=datetime.fromisoformat(row[2]),
                            chunk_id=row[3],
                        )
                    )

                return locks

    async def create_chunk(self, chunk: Chunk):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO chunks (chunk_id, description, status, assigned_agent, files, dependencies, pr_number) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk.chunk_id,
                    chunk.description,
                    chunk.status,
                    chunk.assigned_agent,
                    json.dumps(chunk.files),
                    json.dumps(chunk.dependencies),
                    chunk.pr_number,
                ),
            )
            await db.commit()

    async def update_chunk_status(
        self,
        chunk_id: str,
        status: str,
        assigned_agent: Optional[str] = None,
        pr_number: Optional[int] = None,
    ):
        async with aiosqlite.connect(self.db_path) as db:
            updates = ["status = ?"]
            params = [status]

            if assigned_agent is not None:
                updates.append("assigned_agent = ?")
                params.append(assigned_agent)

            if pr_number is not None:
                updates.append("pr_number = ?")
                params.append(pr_number)

            params.append(chunk_id)

            await db.execute(
                f"UPDATE chunks SET {', '.join(updates)} WHERE chunk_id = ?", params
            )
            await db.commit()

    async def get_chunk(self, chunk_id: str) -> Optional[Chunk]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM chunks WHERE chunk_id = ?", [chunk_id]
            ) as cursor:
                row = await cursor.fetchone()

                if row:
                    return Chunk(
                        chunk_id=row[0],
                        description=row[1],
                        status=row[2],
                        assigned_agent=row[3],
                        files=json.loads(row[4]),
                        dependencies=json.loads(row[5]),
                        pr_number=row[6],
                    )

                return None

    async def get_chunks(self, status: Optional[str] = None) -> List[Chunk]:
        async with aiosqlite.connect(self.db_path) as db:
            if status:
                query = "SELECT * FROM chunks WHERE status = ?"
                params = [status]
            else:
                query = "SELECT * FROM chunks"
                params = []

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

                chunks = []
                for row in rows:
                    chunks.append(
                        Chunk(
                            chunk_id=row[0],
                            description=row[1],
                            status=row[2],
                            assigned_agent=row[3],
                            files=json.loads(row[4]),
                            dependencies=json.loads(row[5]),
                            pr_number=row[6],
                        )
                    )

                return chunks

    async def get_next_available_chunks(self) -> List[Chunk]:
        async with aiosqlite.connect(self.db_path) as db:

            planned_chunks = await self.get_chunks(ChunkStatus.PLANNED)
            locked_files = {lock.file_path for lock in await self.get_locked_files()}

            completed_chunks = await self.get_chunks(ChunkStatus.COMPLETE)
            merged_chunks = await self.get_chunks(ChunkStatus.MERGED)
            completed_ids = {
                chunk.chunk_id for chunk in completed_chunks + merged_chunks
            }

            available_chunks = []
            for chunk in planned_chunks:
                deps_satisfied = all(
                    dep_id in completed_ids for dep_id in chunk.dependencies
                )

                files_available = not any(
                    file_path in locked_files for file_path in chunk.files
                )

                if deps_satisfied and files_available:
                    available_chunks.append(chunk)

            return available_chunks
