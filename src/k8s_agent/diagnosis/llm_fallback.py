"""LLM-assisted diagnosis fallback using NVIDIA NIM API (OpenAI-compatible)."""

import json
import structlog
from typing import Dict, Any
from ..types import Symptom, Diagnosis, FailureType, RemediationType, RiskLevel
from ..config import Settings
from ..llm_client import NVIDIAClient

logger = structlog.get_logger()


class LLMDiagnoser:
    """Fallback diagnoser that queries NVIDIA NIM API to diagnose complex failures."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = NVIDIAClient(settings)

    def diagnose(self, symptom: Symptom) -> Diagnosis | None:
        """Query NVIDIA NIM API to diagnose the Kubernetes failure symptom."""
        logger.info(
            "Querying LLM for fallback diagnosis",
            pod=symptom.pod_name,
            failure_type=symptom.failure_type,
        )

        prompt = self._build_prompt(symptom)

        # Update prompt to request JSON output
        json_prompt = prompt + """

    Provide your response as a valid JSON object with the following fields:
    - root_cause: string
    - remediation_type: one of ["restart_pod", "increase_memory", "update_image_tag", "rollback_deployment", "no_action"]
    - remediation_params: object (parameters for the remediation)
    - risk_level: one of ["low", "medium", "high"]
    - confidence: number between 0.0 and 1.0
    - reasoning: string
    - requires_approval: boolean

    Do not include any additional text outside the JSON object.
    """

        try:
            response = self.client.chat_completion(
                messages=[{"role": "user", "content": json_prompt}],
                temperature=self.settings.llm.temperature,
                max_tokens=self.settings.llm.max_tokens,
                stream=False,
            )

            # Extract the content from the response
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                logger.error("LLM returned empty content")
                return None

            # Parse the JSON content
            try:
                result_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse LLM response as JSON", error=str(e), content=content[:200])
                return None

            # Validate required fields
            required_fields = ["root_cause", "remediation_type", "remediation_params", "risk_level", "confidence", "reasoning", "requires_approval"]
            for field in required_fields:
                if field not in result_data:
                    logger.error(f"Missing required field in LLM response: {field}")
                    return None

            logger.info("Received diagnosis from LLM", root_cause=result_data.get("root_cause"))

            return Diagnosis(
                symptom=symptom,
                root_cause=result_data["root_cause"],
                remediation_type=RemediationType(result_data["remediation_type"]),
                remediation_params=result_data["remediation_params"],
                risk_level=RiskLevel(result_data["risk_level"]),
                confidence=result_data["confidence"],
                reasoning=result_data["reasoning"],
                requires_approval=result_data["requires_approval"],
                diagnosed_by="llm",
            )

        except Exception as e:
            logger.error("LLM diagnosis failed", error=str(e))
            return None

    def _build_prompt(self, symptom: Symptom) -> str:
        """Construct prompt detailing pod state, events, and logs."""
        prompt = f"""Please analyze the following Kubernetes pod failure and diagnose the root cause.

## Pod Context
- **Namespace:** {symptom.namespace}
- **Pod Name:** {symptom.pod_name}
- **Container Name:** {symptom.container_name}
- **Observed State status:** {symptom.failure_type.value}
- **Message:** {symptom.message}
- **Restart Count:** {symptom.restart_count}

## Last Container State Details
```json
{json.dumps(symptom.last_state, indent=2)}
```

## Recent Pod Event Log
```json
{json.dumps(symptom.events, indent=2)}
```

## Pod Tail Logs (last 50 lines)
```
{symptom.logs or "No logs available."}
```
"""
        return prompt
