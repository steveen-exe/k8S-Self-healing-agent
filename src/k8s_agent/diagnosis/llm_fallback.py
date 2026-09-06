"""LLM-assisted diagnosis fallback using NVIDIA NIM API (OpenAI-compatible)."""

import json
import structlog
from typing import Dict, Any, Optional
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

        prompt = self._build_enhanced_prompt(symptom)

        # Update prompt to request JSON output
        json_prompt = prompt + """

    Provide your response as a valid JSON object with the following fields:
    - root_cause: string (detailed explanation of the underlying issue)
    - remediation_type: one of ["restart_pod", "increase_memory", "update_image_tag", "rollback_deployment", "scale_deployment", "add_replica", "no_action"]
    - remediation_params: object (specific parameters for the remediation action)
    - risk_level: one of ["low", "medium", "high"]
    - confidence: number between 0.0 and 1.0
    - reasoning: string (step-by-step explanation of how you arrived at this diagnosis)
    - requires_approval: boolean
    - suggested_verification_steps: array of strings (steps to verify the fix worked)
    - prevention_recommendations: array of strings (recommendations to prevent recurrence)

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

            # Validate enum values before construction
            try:
                remediation_type = RemediationType(result_data["remediation_type"])
            except ValueError as e:
                logger.error(f"Invalid remediation_type from LLM: {result_data['remediation_type']}")
                return None

            try:
                risk_level = RiskLevel(result_data["risk_level"])
            except ValueError as e:
                logger.error(f"Invalid risk_level from LLM: {result_data['risk_level']}")
                return None

            # Validate confidence is in valid range
            confidence = result_data.get("confidence", 0.5)
            if not (0.0 <= confidence <= 1.0):
                logger.error(f"Invalid confidence from LLM: {confidence} (must be 0.0-1.0)")
                return None

            # Validate remediation_params for known remediation types
            if remediation_type == RemediationType.INCREASE_MEMORY:
                factor = result_data["remediation_params"].get("memory_increase_factor", 1.5)
                if not isinstance(factor, (int, float)) or factor <= 0 or factor > 10:
                    logger.error(f"Invalid memory_increase_factor: {factor} (must be 0 < factor <= 10)")
                    return None

            return Diagnosis(
                symptom=symptom,
                root_cause=result_data["root_cause"],
                remediation_type=remediation_type,
                remediation_params=result_data["remediation_params"],
                risk_level=risk_level,
                confidence=confidence,
                reasoning=result_data["reasoning"],
                requires_approval=result_data["requires_approval"],
                diagnosed_by="llm_enhanced",
                suggested_verification_steps=result_data.get("suggested_verification_steps", []),
                prevention_recommendations=result_data.get("prevention_recommendations", []),
            )

        except Exception as e:
            logger.error("LLM diagnosis failed", error=str(e))
            return None

    def _build_enhanced_prompt(self, symptom: Symptom) -> str:
        """Construct enhanced prompt with detailed context and troubleshooting guidance."""
        prompt = f"""You are an expert Kubernetes administrator with deep knowledge of cluster troubleshooting. Analyze the following pod failure and provide a comprehensive diagnosis.

## Pod Context
- **Namespace:** {symptom.namespace}
- **Pod Name:** {symptom.pod_name}
- **Container Name:** {symptom.container_name or 'Not specified'}
- **Observed State:** {symptom.failure_type.value}
- **Message:** {symptom.message}
- **Restart Count:** {symptom.restart_count}
- **First Seen:** {symptom.timestamp.isoformat() if hasattr(symptom.timestamp, 'isoformat') else str(symptom.timestamp)}

## Last Container State Details
```json
{json.dumps(symptom.last_state, indent=2)}
```

## Recent Pod Event Log (Last 10 events)
```json
{json.dumps(symptom.events, indent=2)}
```

## Pod Tail Logs (last 50 lines)
```
{symptom.logs or "No logs available."}
```

## Analysis Instructions:
1. **Identify the root cause**: Look beyond the surface symptom to understand what's truly happening
2. **Consider recent changes**: Check for recent deployments, config changes, or resource pressure
3. **Evaluate patterns**: Look for recurring issues or correlations with restart count
4. **Assess impact**: Determine the blast radius and potential user impact
5. **Recommend appropriate remediation**: Suggest the most effective and least disruptive fix
6. **Assess risk**: Honestly evaluate the risk of the proposed remediation
7. **Provide verification steps**: How to confirm the fix worked
8. **Prevention recommendations**: How to avoid this issue in the future

## Remediation Options:
- **restart_pod**: Simple restart - lowest risk, good for transient issues
- **increase_memory**: For OOMKilled or memory pressure issues
- **update_image_tag**: For image-related failures (ImagePullBackOff, ErrImagePull)
- **rollback_deployment**: For deployment-related issues after recent updates
- **scale_deployment**: For resource exhaustion or performance issues
- **add_replica**: For availability improvements
- **no_action**: When manual intervention is needed or issue is transient

## Risk Levels:
- **low**: Safe, reversible, minimal impact (e.g., restart pod)
- **medium**: Some impact, may cause brief disruption (e.g., update resources)
- **high**: Significant impact, potential data loss or extended downtime (e.g., rollback with data migration)

Now provide your expert analysis in JSON format as specified above."""

        return prompt