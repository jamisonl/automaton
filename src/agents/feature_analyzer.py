"""Feature analyzer agent that analyzes features and identifies affected files."""

import asyncio
from typing import Dict, Any
from pathlib import Path
import traceback

from agents.base import BaseAgent, AgentConfig, DSPyModule
from core.events import EventType, Event
from core.logger import logger


class FeatureAnalyzerAgent(BaseAgent):
    def __init__(self, config: AgentConfig, shared_event_bus=None):
        super().__init__(config, shared_event_bus)
        self.dspy_module = DSPyModule()

    async def setup_event_subscriptions(self):
        self.event_bus.subscribe(EventType.ANALYZE_FEATURE, self.handle_analyze_feature)
        self.event_bus.subscribe(
            EventType.FEATURE_COMPLETED, self.handle_feature_completed
        )

    async def run(self):
        logger.info(f"Feature Analyzer {self.agent_id} started")

        while self.running:
            await asyncio.sleep(1)

    async def handle_analyze_feature(self, event: Event):
        if event.agent_id == self.agent_id:
            return

        logger.info(f"🔍 Analyzing feature requested by {event.agent_id}")
        logger.info(f"📝 Feature: {event.data.get('feature_specification', 'N/A')}")

        feature_spec = event.data.get("feature_specification")
        repo_structure = event.data.get("repository_structure")

        if not feature_spec or not repo_structure:
            logger.error("❌ Missing feature specification or repository structure")
            return

        try:
            logger.info("🤖 Calling DSPy feature analyzer...")
            analysis_result = self.dspy_module.analyze_feature(
                feature_spec, repo_structure
            )
            logger.info("✅ DSPy analysis completed!")

            logger.info("📤 Publishing FEATURE_ANALYZED event...")
            await self.publish_event(
                EventType.FEATURE_ANALYZED,
                {
                    "files_affected": analysis_result.files_affected,
                    "dependencies": analysis_result.dependencies,
                    "complexity_estimate": analysis_result.complexity_estimate,
                    "description": analysis_result.description,
                },
            )

            logger.info(
                f"✅ Feature analysis completed. Affected files: {len(analysis_result.files_affected)}"
            )

        except Exception as e:
            logger.exception(f"❌ Error analyzing feature:")

    async def handle_feature_completed(self, event: Event):
        logger.info(f"🎉 Feature completed! Shutting down Feature Analyzer...")
        await self.stop()
