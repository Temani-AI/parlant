# Copyright 2025 Emcie Co Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import re
from typing import Any

import jsonfinder
from json_repair import repair_json

from parlant.core.loggers import Logger
from parlant.core.meter import Counter, Meter


def normalize_json_output(raw_output: str) -> str:
    json_start = raw_output.find("```json")

    if json_start != -1:
        json_start = json_start + 7
    else:
        json_start = 0

    json_end = raw_output[json_start:].rfind("```")

    if json_end == -1:
        json_end = len(raw_output[json_start:])

    return raw_output[json_start : json_start + json_end].strip()


def repair_and_parse_json(
    raw_content: str,
    logger: Logger,
    model_name: str = "LLM",
) -> dict[str, Any]:
    """
    Parse JSON from LLM response with multiple fallback layers.

    This function implements a progressive repair strategy to handle malformed
    JSON responses from LLMs. It tries multiple parsing methods in order of
    increasing complexity:

    1. Standard json.loads() - fastest, for valid JSON
    2. jsonfinder - handles JSON with extra text/markdown
    3. json_repair - fixes malformed JSON, control characters, missing quotes
    4. Aggressive repair - extracts first JSON object, strips control chars

    Args:
        raw_content: Raw string response from LLM
        logger: Logger instance for debugging
        model_name: Name of the model (for logging)

    Returns:
        Parsed JSON as dictionary

    Raises:
        ValueError: If all parsing attempts fail
    """

    # Normalize: Remove markdown code blocks
    normalized = normalize_json_output(raw_content)

    # Layer 1: Try standard JSON parsing (fastest)
    try:
        return json.loads(normalized)
    except json.JSONDecodeError as e:
        logger.debug(f"Layer 1 failed (json.loads): {e}")

    # Layer 2: Try jsonfinder (handles extra text)
    try:
        json_content = jsonfinder.only_json(normalized)[2]
        logger.info("Layer 2 succeeded (jsonfinder)")
        return json_content
    except (ValueError, IndexError) as e:
        logger.debug(f"Layer 2 failed (jsonfinder): {e}")

    # Layer 3: Try json_repair (handles malformed JSON)
    try:
        repaired = repair_json(
            normalized,
            return_objects=True,  # Return dict directly
            ensure_ascii=False,   # Preserve non-Latin characters
        )
        logger.warning("Layer 3 succeeded (json_repair)")
        return repaired  # type: ignore
    except Exception as e:
        logger.debug(f"Layer 3 failed (json_repair): {e}")

    # Layer 4: Aggressive repair (last resort)
    try:
        # Strip control characters manually
        cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', normalized)

        # Try to extract first JSON object
        # Find first { and matching }
        start = cleaned.find('{')
        if start == -1:
            raise ValueError("No JSON object found")

        # Count braces to find matching closing brace
        brace_count = 0
        end = start
        for i, char in enumerate(cleaned[start:], start=start):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = i + 1
                    break

        first_json = cleaned[start:end]

        # Try json_repair on the extracted object
        repaired = repair_json(
            first_json,
            return_objects=True,
            skip_json_loads=True,  # Skip validation, just repair
            ensure_ascii=False,
        )
        logger.warning("Layer 4 succeeded (aggressive repair)")
        return repaired  # type: ignore

    except Exception as e:
        logger.error(f"Layer 4 failed (aggressive repair): {e}")

    # All layers failed
    logger.error(f"All JSON parsing layers failed for {model_name}")
    logger.error(f"Raw content:\n{raw_content}")
    raise ValueError(f"Failed to parse JSON from {model_name} response after all repair attempts")


_INPUT_TOKENS_COUNTER: Counter
_OUTPUT_TOKENS_COUNTER: Counter
_CACHED_TOKENS_COUNTER: Counter
_COUNTERS_INITIALIZED = False


async def record_llm_metrics(
    meter: Meter,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> None:
    global _COUNTERS_INITIALIZED
    global _INPUT_TOKENS_COUNTER
    global _OUTPUT_TOKENS_COUNTER
    global _CACHED_TOKENS_COUNTER

    if not _COUNTERS_INITIALIZED:
        _INPUT_TOKENS_COUNTER = meter.create_counter(
            name="input_tokens",
            description="Number of input tokens sent to a LLM model",
        )
        _OUTPUT_TOKENS_COUNTER = meter.create_counter(
            name="output_tokens",
            description="Number of output tokens received from a LLM model",
        )
        _CACHED_TOKENS_COUNTER = meter.create_counter(
            name="cached_input_tokens",
            description="Number of input tokens served from cache for a LLM model",
        )

        _COUNTERS_INITIALIZED = True

    await _INPUT_TOKENS_COUNTER.increment(
        input_tokens,
        {"model_name": model_name},
    )

    await _OUTPUT_TOKENS_COUNTER.increment(
        output_tokens,
        {"model_name": model_name},
    )

    await _CACHED_TOKENS_COUNTER.increment(
        cached_input_tokens,
        {"model_name": model_name},
    )
