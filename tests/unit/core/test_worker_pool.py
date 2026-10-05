import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from src.core.worker_pool import pr_worker

@pytest.mark.anyio
async def test_worker_processes_task_successfully():
    queue = asyncio.Queue()
    await queue.put((123, "repo-id", "project-id"))
    
    with patch("src.core.worker_pool.injector") as mock_injector:
        mock_orchestrator = AsyncMock()
        mock_injector.get.return_value = mock_orchestrator
        
        # Start the worker
        worker_task = asyncio.create_task(pr_worker(1, queue))
        
        # Wait for the queue to be empty
        await queue.join()
        
        # Cancel the worker to shut it down
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
            
        mock_orchestrator.process_pr_pipeline.assert_called_once_with(123, "repo-id", "project-id")

@pytest.mark.anyio
async def test_worker_handles_exception_and_continues():
    queue = asyncio.Queue()
    # Put two tasks. The first will fail, the second will succeed.
    await queue.put((1, "repo1", "proj1"))
    await queue.put((2, "repo2", "proj2"))
    
    with patch("src.core.worker_pool.injector") as mock_injector:
        mock_orchestrator = AsyncMock()
        mock_orchestrator.process_pr_pipeline.side_effect = [Exception("Pipeline failed"), None]
        mock_injector.get.return_value = mock_orchestrator
        
        worker_task = asyncio.create_task(pr_worker(2, queue))
        
        await queue.join()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
            
        assert mock_orchestrator.process_pr_pipeline.call_count == 2
        mock_orchestrator.process_pr_pipeline.assert_any_call(1, "repo1", "proj1")
        mock_orchestrator.process_pr_pipeline.assert_any_call(2, "repo2", "proj2")
