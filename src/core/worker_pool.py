import asyncio
from typing import Tuple

from ..core.logger import get_logger
from ..composition import injector
from ..services.orchestrator import PipelineOrchestrator

log = get_logger("WorkerPool")

# Queue stores tuples of (pr_id, repo_id, project)
# We remove the global instantiation to avoid asyncio Event Loop cross-contamination during tests.


async def pr_worker(worker_id: int, pr_queue: asyncio.Queue) -> None:
    """
    Background worker that continuously pulls PR tasks from the queue
    and executes them concurrently up to the maximum number of workers.
    """
    log.info("Worker %d started and waiting for PR tasks.", worker_id)
    while True:
        try:
            pr_id, repo_id, project = await pr_queue.get()
            log.info("Worker %d picked up PR #%s (Project: %s)", worker_id, pr_id, project)
            
            # Dynamically resolve orchestrator from the DI container for each task
            orchestrator = injector.get(PipelineOrchestrator)
            
            # Execute the orchestrated pipeline directly (now fully async)
            await orchestrator.process_pr_pipeline(pr_id, repo_id, project)
            
            log.info("Worker %d completed processing for PR #%s", worker_id, pr_id)
        except asyncio.CancelledError:
            log.info("Worker %d shutting down.", worker_id)
            break
        except Exception as exc:
            # We don't have PR ID available if the exception happens inside `pr_queue.get()`
            pr_context = f"PR #{pr_id}" if 'pr_id' in locals() else "unknown PR"
            log.error("Worker %d encountered an error processing %s: %s", worker_id, pr_context, exc, exc_info=True)
        finally:
            # Ensure task_done is called if we picked up an item
            if 'pr_id' in locals():
                pr_queue.task_done()
