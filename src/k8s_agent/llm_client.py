"""LLM client for NVIDIA NIM API (OpenAI-compatible)."""

import json
import structlog
from typing import Any, Dict, List, Optional
import requests

from .config import Settings

logger = structlog.get_logger()


class NVIDIAClient:
    """Client for NVIDIA NIM / OmniRoute API (OpenAI-compatible)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        # Prefer OmniRoute API key if configured, otherwise fallback to anthropic_api_key
        self.api_key = settings.omniroute_api_key or settings.anthropic_api_key
        # Prefer OmniRoute base URL if configured, otherwise fallback to NVIDIA integrate endpoint
        self.base_url = settings.omniroute_base_url or "https://integrate.api.nvidia.com/v1"
        self.model = settings.llm.model

        # Warn if using OmniRoute and model string does not contain a provider prefix
        if self.api_key == settings.omniroute_api_key and '/' not in self.model:
            logger.warning(
                "Model string does not contain a provider prefix. "
                "When using OmniRoute, the model string may need a prefix like 'anthropic/claude-...'. "
                "Refer to OmniRoute documentation for the correct format."
            )

        if not self.api_key:
            raise ValueError("API key is required (set OMNIROUTE_API_KEY or ANTHROPIC_API_KEY)")

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Send chat completion request to NVIDIA NIM API."""
        url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }

        # Add optional parameters if provided
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        else:
            # Use default from settings if not overridden
            payload["max_tokens"] = self.settings.llm.max_tokens

        logger.info(
            "Sending LLM request",
            model=self.model,
            message_count=len(messages),
            temperature=temperature or self.settings.llm.temperature,
            max_tokens=payload["max_tokens"],
        )

        try:
            response = requests.post(url, headers=headers, json=payload, stream=stream)
            response.raise_for_status()

            if stream:
                # Handle streaming response
                full_response = ""
                for line in response.iter_lines():
                    if line:
                        line_text = line.decode("utf-8")
                        if line_text.startswith("data: "):
                            data = line_text[6:]  # Remove "data: " prefix
                            if data.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                if "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    full_response += content
                            except json.JSONDecodeError:
                                logger.warning("Failed to parse streaming chunk", chunk=data)
                return {"choices": [{"message": {"content": full_response}}]}
            else:
                result = response.json()
                logger.info(
                    "Received LLM response",
                    tokens_used=result.get("usage", {}).get("total_tokens", 0),
                )
                return result

        except requests.exceptions.RequestException as e:
            logger.error("LLM request failed", error=str(e))
            raise
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response", error=str(e))
            raise


# Factory function for backward compatibility
def create_llm_client(settings: Settings) -> NVIDIAClient:
    """Create LLM client based on settings."""
    return NVIDIAClient(settings)