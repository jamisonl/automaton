import asyncio
import os
import argparse
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.live import Live
import signal


try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))

from agents.base import AgentConfig
from agents.coordinator import CoordinatorAgent
from agents.feature_analyzer import FeatureAnalyzerAgent
from agents.pr_generator import PRGeneratorAgent
from core.events import EventBus
from core.logger import logger

console = Console()


class PRAutomationSystem:
    def __init__(
        self,
        target_repo_path: str,
        github_token: str,
        github_username: str,
        repo_name: str,
        gemini_api_key: Optional[str] = None,
        db_path: str = "coordination.db",
    ):
        self.target_repo_path = target_repo_path
        self.github_token = github_token
        self.repo_name = repo_name
        self.gemini_api_key = gemini_api_key
        self.db_path = db_path

        self.shared_event_bus = EventBus(db_path)

        self.coordinator = CoordinatorAgent(
            AgentConfig(
                agent_id="coordinator",
                event_bus_db_path=db_path,
                gemini_api_key=gemini_api_key,
            ),
            target_repo_path,
            shared_event_bus=self.shared_event_bus,
        )

        self.feature_analyzer = FeatureAnalyzerAgent(
            AgentConfig(
                agent_id="feature_analyzer",
                event_bus_db_path=db_path,
                gemini_api_key=gemini_api_key,
            ),
            shared_event_bus=self.shared_event_bus,
        )

        self.pr_generator = PRGeneratorAgent(
            AgentConfig(
                agent_id="pr_generator",
                event_bus_db_path=db_path,
                gemini_api_key=gemini_api_key,
            ),
            target_repo_path,
            github_token,
            github_username,
            repo_name,
            shared_event_bus=self.shared_event_bus,
        )

        self.agents = [
            self.coordinator,
            self.feature_analyzer,
            self.pr_generator,
        ]

        self.running = False

    async def start(self):
        logger.info("Starting Automaton System...")

        self.running = True

        init_tasks = []
        for agent in self.agents:

            init_tasks.append(agent.initialize_agent())

        results = await asyncio.gather(*init_tasks, return_exceptions=True)
        all_initialized_successfully = True
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"Error initializing agent {self.agents[i].agent_id}: {result}"
                )
                all_initialized_successfully = False

        if not all_initialized_successfully:
            logger.error("One or more agents failed to initialize. Stopping system.")
            raise RuntimeError("Agent initialization failed.")

        logger.info("All agents initialized successfully.")

        agent_run_tasks = []
        for agent in self.agents:

            agent_run_tasks.append(asyncio.create_task(agent.start()))

        logger.info("All agent run loops started.")
        return agent_run_tasks

    async def stop(self):
        logger.warning("Stopping all agents...")

        self.running = False

        for agent in self.agents:
            await agent.stop()

        logger.info("All agents stopped.")

    async def process_feature(self, feature_specification: str):
        logger.info(f"Processing feature: {feature_specification}")

        logger.debug("About to call start_feature_processing...")
        await self.coordinator.start_feature_processing(feature_specification)
        logger.debug("start_feature_processing completed!")

    async def get_status(self):
        return await self.coordinator.get_status()

    def create_status_table(self, status):
        table = Table(title="PR Automation System Status")

        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Total Chunks", str(status.get("total_chunks", 0)))
        table.add_row("Chunks Created", str(status.get("chunks_created", False)))
        table.add_row("Feature Active", str(status.get("current_feature", False)))

        breakdown = status.get("status_breakdown", {})
        for status_name, count in breakdown.items():
            table.add_row(f"  {status_name.title()}", str(count))

        return table


async def monitor_system(system: PRAutomationSystem):
    def update_display():
        status = asyncio.create_task(system.get_status())
        try:
            status_data = asyncio.get_event_loop().run_until_complete(status)
            return system.create_status_table(status_data)
        except:
            return Table(title="Status Unavailable")

    with Live(update_display(), refresh_per_second=1) as live:
        while system.running:
            live.update(update_display())
            await asyncio.sleep(1)


async def main():
    parser = argparse.ArgumentParser(description="Automaton System")
    parser.add_argument(
        "-f",
        "--feature",
        type=str,
        help="Feature specification to process.",
    )
    parser.add_argument(
        "-r",
        "--target-repo",
        type=str,
        help="Path to the target repository to analyze (overrides TARGET_REPO_PATH env var).",
    )
    args = parser.parse_args()

    target_repo = args.target_repo or os.getenv("TARGET_REPO_PATH", ".")
    github_token = os.getenv("GITHUB_TOKEN")
    github_username = os.getenv("GITHUB_USERNAME")
    repo_name = os.getenv("GITHUB_REPO_NAME")
    gemini_api_key = os.getenv("GEMINI_API_KEY")

    if not github_token:
        logger.error("Error: GITHUB_TOKEN environment variable is required")
        return

    if not github_username:
        logger.error("Error: GITHUB_USERNAME environment variable is required")
        return

    if not repo_name:
        from pathlib import Path

        project_name = Path(target_repo).name
        repo_name = project_name
        logger.warning(
            f"No GITHUB_REPO_NAME provided, using project directory name: {repo_name}"
        )

    system = PRAutomationSystem(
        target_repo_path=target_repo,
        github_token=github_token,
        github_username=github_username,
        repo_name=repo_name,
        gemini_api_key=gemini_api_key,
    )

    def signal_handler(signum, frame):
        logger.warning("\nReceived shutdown signal...")
        asyncio.create_task(system.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        agent_tasks = await system.start()

        monitor_task = asyncio.create_task(monitor_system(system))

        feature_to_process = args.feature
        logger.info(f"Processing feature from CLI arg: {feature_to_process}")

        await system.process_feature(feature_to_process)

        while system.running:
            if not system.coordinator.running:
                logger.info("🎉 Feature processing completed!")
                break
            await asyncio.sleep(1)

        await system.stop()

    except KeyboardInterrupt:
        logger.warning("\nReceived keyboard interrupt...")
    finally:
        await system.stop()
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
