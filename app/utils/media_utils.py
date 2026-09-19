"""
Turning a RunPod worker's output into real bytes.

Serverless workers usually return a generated media file in one of two ways:

    1. base64 encoded inside the JSON response, e.g. {"audio_base64": "...."}
    2. a URL to a temporary file, e.g. {"video_url": "https://..."}

Which key is used depends entirely on the worker handler you deploy, so this
helper accepts a list of candidate keys and handles both shapes.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Iterable

import httpx

from app.utils.validation import PipelineError

logger = logging.getLogger(__name__)


def _find_first(output: Any, keys: Iterable[str]) -> Any:
    """Look for the first matching key, including one level of nesting."""
    if not isinstance(output, dict):
        return None
    for key in keys:
        if output.get(key):
            return output[key]
    # Some workers wrap everything in {"output": {...}} or {"result": {...}}
    for wrapper in ("output", "result", "data"):
        nested = output.get(wrapper)
        if isinstance(nested, dict):
            found = _find_first(nested, keys)
            if found:
                return found
    return None


async def _download(url: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as exc:
        logger.exception("Could not download worker result from %s", url)
        raise PipelineError("The generated file could not be downloaded from the GPU worker.") from exc


async def result_to_bytes(
    output: Any,
    *,
    base64_keys: Iterable[str],
    url_keys: Iterable[str],
    what: str = "file",
) -> bytes:
    """
    Extract the generated file from a worker output object.

    `what` is only used in the error message, e.g. "audio" or "video".
    """
    if isinstance(output, str):
        # A worker that returns a bare string is either a URL or base64.
        output = {"url": output} if output.startswith("http") else {"base64": output}

    encoded = _find_first(output, base64_keys)
    if encoded:
        if isinstance(encoded, str) and encoded.startswith("data:"):
            encoded = encoded.split(",", 1)[-1]
        try:
            return base64.b64decode(encoded)
        except (binascii.Error, ValueError) as exc:
            raise PipelineError(f"The generated {what} could not be decoded.") from exc

    url = _find_first(output, url_keys)
    if url and isinstance(url, str):
        return await _download(url)

    logger.error("Unrecognised worker output shape: %s", str(output)[:500])
    raise PipelineError(
        f"The GPU worker returned a {what} in an unexpected format. "
        "Update the adapter in app/services to match your worker's output."
    )
