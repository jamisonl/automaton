"""Event system for agent coordination."""

from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime
import json
import asyncio
from pydantic import BaseModel
import aiosqlite
from core.logger import logger


class EventType(str, Enum):
    FEATURE_ANALYZED = "feature_analyzed"
    CHUNKS_PLANNED = "chunks_planned"
    FILE_LOCKED = "file_locked"
    FILE_UNLOCKED = "file_unlocked"
    CHUNK_STARTED = "chunk_started"
    CHUNK_COMPLETED = "chunk_completed"
    PR_CREATED = "pr_created"
    PR_REVIEWED = "pr_reviewed"
    PR_MERGED = "pr_merged"
    MERGE_PR = "merge_pr"
    ANALYZE_FEATURE = "analyze_feature"
    CHUNK_ASSIGNED = "chunk_assigned"
    FEATURE_COMPLETED = "feature_completed"
    CODE_GENERATION_STARTED = "code_generation_started"
    FILES_MODIFIED = "files_modified"
    BRANCH_DELETED = "branch_deleted"


class Event(BaseModel):
    event_id: str
    event_type: EventType
    agent_id: str
    data: Dict[str, Any]
    timestamp: datetime


class EventBus:
    def __init__(self, db_path: str = "coordination.db"):
        self.db_path = db_path
        self.listeners: Dict[EventType, List[callable]] = {}
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS file_locks (
                    file_path TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    locked_at TEXT NOT NULL,
                    chunk_id TEXT NOT NULL
                )
            """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_agent TEXT,
                    files TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    pr_number INTEGER
                )
            """
            )

            await db.commit()

        self._initialized = True

    async def publish(
        self, event_type: EventType, agent_id: str, data: Dict[str, Any]
    ) -> str:
        logger.debug(
            f"EventBus.publish called - event_type: {event_type}, agent_id: {agent_id}"
        )

        if not self._initialized:
            logger.debug("EventBus not initialized, calling initialize()...")
            await self.initialize()
            logger.debug("EventBus initialization completed")

        logger.debug("Creating event object...")
        event_id = f"{agent_id}_{event_type}_{datetime.now().isoformat()}"
        event = Event(
            event_id=event_id,
            event_type=event_type,
            agent_id=agent_id,
            data=data,
            timestamp=datetime.now(),
        )
        logger.debug(f"Event object created with ID: {event_id}")

        logger.debug("About to insert event into database...")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO events (event_id, event_type, agent_id, data, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.event_type,
                        event.agent_id,
                        json.dumps(event.data),
                        event.timestamp.isoformat(),
                    ),
                )
                await db.commit()
            logger.debug("Event inserted into database successfully")
        except Exception as e:
            logger.debug(f"Error inserting event into database: {e}")
            raise

        logger.debug(f"Checking for listeners for event type: {event_type}")
        if event_type in self.listeners:
            listener_count = len(self.listeners[event_type])
            logger.debug(f"Found {listener_count} listeners, notifying them...")
            for i, listener in enumerate(self.listeners[event_type]):
                try:
                    logger.debug(f"Calling listener {i+1}/{listener_count}")
                    if asyncio.iscoroutinefunction(listener):
                        await listener(event)
                    else:
                        listener(event)
                    logger.debug(
                        f"Listener {i+1}/{listener_count} completed successfully"
                    )
                except Exception as e:
                    logger.debug(f"Error in event listener {i+1}: {e}")
        else:
            logger.debug(f"No listeners found for event type: {event_type}")

        logger.debug(f"EventBus.publish completing, returning event_id: {event_id}")
        return event_id

    def subscribe(self, event_type: EventType, callback: callable):
        if event_type not in self.listeners:
            self.listeners[event_type] = []
        self.listeners[event_type].append(callback)

    async def get_events(
        self, event_type: Optional[EventType] = None, agent_id: Optional[str] = None
    ) -> List[Event]:
        if not self._initialized:
            await self.initialize()

        query = "SELECT * FROM events"
        params = []
        conditions = []

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)

        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY timestamp DESC"

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

                events = []
                for row in rows:
                    events.append(
                        Event(
                            event_id=row[0],
                            event_type=EventType(row[1]),
                            agent_id=row[2],
                            data=json.loads(row[3]),
                            timestamp=datetime.fromisoformat(row[4]),
                        )
                    )

                return events
