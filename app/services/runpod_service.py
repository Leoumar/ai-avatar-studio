"""
RunPod Serverless client.

This module knows how to talk to the RunPod Serverless REST API, and nothing
about CosyVoice or SoulX-Flash. The model-specific payloads live in
cosyvoice_service.py and soulxflash_service.py.

RunPod Serverless works like this:

    POST {base}/{endpoint_id}/run            -> returns a job id
    GET  {base}/{endpoint_id}/status/{job}   -> IN_QUEUE / IN_PROGRESS / COMPLETED / FAILED / TIMED_OUT
    POST {base}/{endpoint_id}/cancel/{job}   -> cancel a running job

The shape of "input" and "output" is decided by YOUR worker handler, not by
RunPod. That is why this file never assumes what is inside them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

import httpx

from app.config import settings
from app.utils.validation import PipelineError

logger = logging.getLogger(__name__)

# RunPod job states
IN_QUEUE = "IN_QUEUE"
IN_PROGRESS = "IN_PROGRESS"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
TIMED_OUT = "TIMED_OUT"
CANCELLED = "CANCELLED"

TERMINAL_STATES = {COMPLETED, FAILED, TIMED_OUT, CANCELLED}


def _headers() -> dict[str, str]:
    if not settings.RUNPOD_API_KEY:
        raise PipelineError(
            "RunPod is not configured. Add RUNPOD_API_KEY to your .env file, "
            "or set MOCK_MODE=true to test without a GPU."
        )
    return {
        "Authorization": f"Bearer {settings.RUNPOD_API_KEY}",
        "Content-Type": "application/json",
    }


def _endpoint_url(endpoint_id: str, suffix: str) -> str:
    if not endpoint_id:
        raise PipelineError(
            "A RunPod endpoint id is missing. Check COSYVOICE_ENDPOINT_ID and "
            "SOULXFLASH_ENDPOINT_ID in your .env file."
        )
    return f"{settings.RUNPOD_BASE_URL}/{endpoint_id}/{suffix}"


async def submit_job(endpoint_id: str, payload: dict[str, Any]) -> str:
    """
    Start an asynchronous job and return its RunPod job id.

    `payload` is passed straight through as the worker's "input" object.
    """
    url = _endpoint_url(endpoint_id, "run")
    logger.info("Submitting RunPod job to endpoint %s", endpoint_id)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(url, headers=_headers(), json={"input": payload})
    except httpx.HTTPError as exc:
        logger.exception("Network error contacting RunPod")
        raise PipelineError("RunPod is currently unavailable. Please try again.") from exc

    if response.status_code >= 400:
        logger.error("RunPod rejected the job: %s %s", response.status_code, response.text[:500])
        raise PipelineError("RunPod rejected the generation job. Check the server logs.")

    data = response.json()
    job_id = data.get("id")
    if not job_id:
        logger.error("RunPod response had no job id: %s", data)
        raise PipelineError("RunPod did not return a job id.")
    return job_id


async def get_job_status(endpoint_id: str, job_id: str) -> dict[str, Any]:
    """Fetch the raw status document for a job."""
    url = _endpoint_url(endpoint_id, f"status/{job_id}")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url, headers=_headers())
    except httpx.HTTPError as exc:
        logger.exception("Network error while polling RunPod")
        raise PipelineError("Lost connection to RunPod while checking the job.") from exc

    if response.status_code >= 400:
        logger.error("RunPod status error: %s %s", response.status_code, response.text[:500])
        raise PipelineError("Could not read the job status from RunPod.")

    return response.json()


async def cancel_job(endpoint_id: str, job_id: str) -> None:
    """Best-effort cancel. Never raises."""
    try:
        url = _endpoint_url(endpoint_id, f"cancel/{job_id}")
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(url, headers=_headers())
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not cancel RunPod job %s: %s", job_id, exc)


async def wait_for_job(
    endpoint_id: str,
    job_id: str,
    *,
    timeout_seconds: int | None = None,
    poll_interval: int | None = None,
    on_poll: Callable[[str, float], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """
    Poll a job until it finishes and return the worker's "output" object.

    on_poll(status, elapsed_seconds) is called after every poll so the caller
    can update the progress bar while waiting.
    """
    timeout_seconds = timeout_seconds or settings.RUNPOD_TIMEOUT_SECONDS
    poll_interval = poll_interval or settings.RUNPOD_POLL_INTERVAL
    started = time.monotonic()

    while True:
        document = await get_job_status(endpoint_id, job_id)
        status = (document.get("status") or "").upper()
        elapsed = time.monotonic() - started

        if on_poll is not None:
            await on_poll(status, elapsed)

        if status == COMPLETED:
            output = document.get("output")
            if output is None:
                raise PipelineError("The GPU job finished but returned no result.")
            return output

        if status == FAILED:
            logger.error("RunPod job %s failed: %s", job_id, document.get("error"))
            raise PipelineError("The GPU job failed. Please try again.")

        if status == TIMED_OUT:
            raise PipelineError("The generation timed out on the GPU worker.")

        if status == CANCELLED:
            raise PipelineError("The generation was cancelled.")

        if elapsed > timeout_seconds:
            await cancel_job(endpoint_id, job_id)
            raise PipelineError(
                f"The generation timed out after {int(elapsed)} seconds. "
                "Try a shorter script or a lower quality."
            )

        await asyncio.sleep(poll_interval)


async def run_and_wait(
    endpoint_id: str,
    payload: dict[str, Any],
    *,
    on_job_id: Callable[[str], Awaitable[None]] | None = None,
    on_poll: Callable[[str, float], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper: submit a job, remember its id, wait for the result."""
    job_id = await submit_job(endpoint_id, payload)
    if on_job_id is not None:
        await on_job_id(job_id)
    return await wait_for_job(endpoint_id, job_id, on_poll=on_poll)
