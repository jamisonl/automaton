"""Base agent class and DSPy integration."""

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import dspy
from pydantic import BaseModel
import sys
import traceback

from core.events import EventBus, EventType, Event
from core.coordination import CoordinationManager
from core.logger import logger
from core.config import get_model_name
import threading


_dspy_global_config_lock = threading.Lock()
_dspy_globally_configured = False


def ensure_dspy_globally_configured(api_key: str, model_name: str):
    global _dspy_globally_configured

    if _dspy_globally_configured:
        logger.debug(
            f"DSPy already globally configured. Thread {threading.get_ident()} skipping."
        )
        return

    with _dspy_global_config_lock:
        if not _dspy_globally_configured:
            logger.info(
                f"Attempting to configure DSPy globally in thread {threading.get_ident()}."
            )
            if not api_key:
                logger.error(
                    "DSPy Global Setup: GEMINI_API_KEY is missing. Cannot configure DSPy."
                )
                raise ValueError("GEMINI_API_KEY is required for DSPy global setup.")

            try:
                lm = dspy.LM(
                    model=f"gemini/{model_name}",
                    api_key=api_key,
                    temperature=0,
                    max_tokens=65535,
                )
                dspy.settings.configure(lm=lm)
                _dspy_globally_configured = True
                logger.info(
                    f"DSPy globally configured successfully by thread {threading.get_ident()} with model {model_name}."
                )
            except Exception as e:
                logger.error(
                    f"DSPy Global Setup: Failed to configure DSPy globally: {e}",
                    exc_info=True,
                )
                raise RuntimeError(f"Failed to configure DSPy globally: {e}")


class AgentConfig(BaseModel):
    agent_id: str
    event_bus_db_path: str = "coordination.db"
    gemini_api_key: Optional[str] = None
    model_name: Optional[str] = None


class BaseAgent(ABC):
    def __init__(
        self, config: AgentConfig, shared_event_bus: Optional[EventBus] = None
    ):
        self.config = config
        self.agent_id = config.agent_id
        self.event_bus = shared_event_bus or EventBus(config.event_bus_db_path)
        self.coordination = CoordinationManager(config.event_bus_db_path)
        self.running = False

        if not config.gemini_api_key:
            logger.error(
                f"Agent {self.agent_id}: GEMINI_API_KEY missing in config. Agent cannot be initialized."
            )
            raise ValueError(
                f"Agent {self.agent_id}: GEMINI_API_KEY is required for functionality."
            )

        model_name = config.model_name or get_model_name()
        ensure_dspy_globally_configured(config.gemini_api_key, model_name)

    async def initialize_agent(self):
        """Initializes the agent, event bus, and subscriptions."""
        await self.event_bus.initialize()
        await self.setup_event_subscriptions()
        self.running = True
        # logger.debug(f"Agent {self.agent_id} initialized and subscriptions set up.")

    async def start(self):
        """Start the agent's main run loop."""
        if not self.running:
            logger.debug(
                f"Agent {self.agent_id} was not pre-initialized. Initializing now before starting run loop."
            )
            await self.initialize_agent()

        if self.running:
            await self.run()
        else:
            logger.error(
                f"Agent {self.agent_id} failed to set running flag during initialization."
            )

    async def stop(self):
        self.running = False

    @abstractmethod
    async def setup_event_subscriptions(self):
        """Setup event subscriptions specific to this agent."""
        pass

    @abstractmethod
    async def run(self):
        """Main agent loop."""
        pass

    async def publish_event(self, event_type: EventType, data: Dict[str, Any]) -> str:
        """Publish an event to the event bus."""
        logger.debug(
            f"BaseAgent.publish_event called - event_type: {event_type}, agent_id: {self.agent_id}"
        )
        try:
            logger.debug("About to call self.event_bus.publish...")
            result = await self.event_bus.publish(event_type, self.agent_id, data)
            logger.debug(f"self.event_bus.publish completed, result: {result}")
            return result
        except Exception as e:
            logger.exception(f"Error in publish_event:")
            raise

    async def handle_event(self, event: Event):
        pass


class FeatureAnalysisResult(BaseModel):
    files_affected: list[str]
    dependencies: dict[str, list[str]]
    complexity_estimate: int
    description: str


class ChunkPlan(BaseModel):
    chunk_id: str
    description: str
    files: list[str]
    dependencies: list[str]
    estimated_effort: int


class FeatureAnalyzer(dspy.Signature):
    """Analyze a feature specification to identify affected files and dependencies."""

    feature_specification: str = dspy.InputField(
        desc="The feature specification to analyze"
    )
    repository_structure: str = dspy.InputField(
        desc="The current repository file structure"
    )

    files_affected: str = dspy.OutputField(
        desc="JSON list of files that will be affected by this feature"
    )
    dependencies: str = dspy.OutputField(
        desc="JSON dict mapping files to their dependencies"
    )
    complexity_estimate: int = dspy.OutputField(
        desc="Complexity estimate on scale 1-10"
    )
    description: str = dspy.OutputField(
        desc="Clear description of what this feature does"
    )


class ChunkPlanner(dspy.Signature):
    """Plan how to divide a feature into logical chunks for separate PRs."""

    feature_analysis: str = dspy.InputField(
        desc="JSON analysis of the feature including files and dependencies"
    )

    chunks: str = dspy.OutputField(
        desc="JSON list of chunk plans with chunk_id, description, files, dependencies, and estimated_effort (integer on a scale of 1-10)"
    )


class CodeGenerator(dspy.Signature):
    """Generate code changes for a specific chunk of a feature."""

    chunk_description: str = dspy.InputField(
        desc="Description of the chunk to implement"
    )
    files_to_modify: str = dspy.InputField(
        desc="JSON list of files that need to be modified in this chunk"
    )
    existing_codebase: str = dspy.InputField(
        desc="JSON dict mapping ALL file paths to their current content (for context and interface consistency)"
    )

    modified_files: str = dspy.OutputField(
        desc="JSON dict mapping ONLY the files_to_modify paths to their new content. Must maintain compatibility with existing interfaces."
    )
    commit_message: str = dspy.OutputField(
        desc="Clear, descriptive commit message for these changes"
    )


class PRDescriptionGenerator(dspy.Signature):
    """Generate a pull request description for a chunk implementation."""

    chunk_description: str = dspy.InputField(desc="Description of what was implemented")
    files_changed: str = dspy.InputField(desc="List of files that were modified")
    commit_message: str = dspy.InputField(desc="The commit message used")

    pr_title: str = dspy.OutputField(desc="Clear, concise PR title")
    pr_description: str = dspy.OutputField(
        desc="Detailed PR description with context and changes"
    )


class DSPyModule:
    def __init__(self):
        self.feature_analyzer = dspy.ChainOfThought(FeatureAnalyzer)
        self.chunk_planner = dspy.ChainOfThought(ChunkPlanner)
        self.code_generator = dspy.ChainOfThought(CodeGenerator)
        self.pr_description_generator = dspy.ChainOfThought(PRDescriptionGenerator)

    def analyze_feature(
        self, feature_spec: str, repo_structure: str
    ) -> FeatureAnalysisResult:
        result = self.feature_analyzer(
            feature_specification=feature_spec, repository_structure=repo_structure
        )

        import json

        return FeatureAnalysisResult(
            files_affected=json.loads(result.files_affected),
            dependencies=json.loads(result.dependencies),
            complexity_estimate=result.complexity_estimate,
            description=result.description,
        )

    def plan_chunks(self, feature_analysis: FeatureAnalysisResult) -> list[ChunkPlan]:
        import json

        analysis_json = json.dumps(
            {
                "files_affected": feature_analysis.files_affected,
                "dependencies": feature_analysis.dependencies,
                "complexity_estimate": feature_analysis.complexity_estimate,
                "description": feature_analysis.description,
            }
        )

        result = self.chunk_planner(feature_analysis=analysis_json)

        chunks_data = json.loads(result.chunks)
        return [ChunkPlan(**chunk) for chunk in chunks_data]

    def generate_code(
        self, chunk: ChunkPlan, existing_code: dict[str, str]
    ) -> dict[str, str]:
        import json

        logger.debug(f"DSPyModule.generate_code called for chunk: {chunk.chunk_id}")
        logger.debug(f"Chunk description: {chunk.description}")
        logger.debug(f"Files to modify: {json.dumps(chunk.files)}")

        existing_codebase_json = json.dumps(existing_code)
        if len(existing_codebase_json) > 10000:  # Log first 10k chars if too large
            logger.debug(
                f"Existing codebase (first 10k chars): {existing_codebase_json[:10000]}..."
            )
            logger.debug(
                f"Full existing codebase size: {len(existing_codebase_json)} chars"
            )
        else:
            logger.debug(f"Existing codebase: {existing_codebase_json}")

        current_lm_settings = dspy.settings.lm
        max_tokens_val = "N/A"
        temperature_val = "N/A"
        if hasattr(current_lm_settings, "kwargs"):
            max_tokens_val = current_lm_settings.kwargs.get("max_tokens", "Not Set")
            temperature_val = current_lm_settings.kwargs.get("temperature", "Not Set")

        logger.debug(f"DSPy LM max_tokens: {max_tokens_val}")
        logger.debug(f"DSPy LM temperature: {temperature_val}")

        result = self.code_generator(
            chunk_description=chunk.description,
            files_to_modify=json.dumps(chunk.files),
            existing_codebase=existing_codebase_json,
        )

        logger.debug(f"Raw result from code_generator for chunk {chunk.chunk_id}:")

        raw_modified_files_output = str(result.modified_files)
        if len(raw_modified_files_output) > 10000:
            logger.debug(
                f"Raw result.modified_files (first 10k chars): {raw_modified_files_output[:10000]}..."
            )
            logger.debug(
                f"Full raw result.modified_files size: {len(raw_modified_files_output)} chars"
            )

        else:
            logger.debug(f"Raw result.modified_files: {raw_modified_files_output}")
        logger.debug(f"Raw result.commit_message: {result.commit_message}")

        if result.modified_files is None:
            logger.critical("🚨 API response truncated - quota exhausted!")
            logger.critical("💥 SYSTEM MUST STOP - Cannot continue without API access!")
            sys.exit(1)

        try:
            modified_files = json.loads(result.modified_files)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON decode error in modified_files: {e}")
            logger.error(f"Raw modified_files content: {result.modified_files}")
            raise
        except TypeError as e:
            if "NoneType" in str(e):
                logger.critical("🚨 API response is None - quota exhausted!")
                logger.critical(
                    "💥 SYSTEM MUST STOP - Cannot continue without API access!"
                )
                sys.exit(1)
            raise

        return modified_files, result.commit_message

    def generate_pr_description(
        self, chunk: ChunkPlan, files_changed: list[str], commit_message: str
    ) -> tuple[str, str]:
        import json

        result = self.pr_description_generator(
            chunk_description=chunk.description,
            files_changed=json.dumps(files_changed),
            commit_message=commit_message,
        )

        return result.pr_title, result.pr_description
