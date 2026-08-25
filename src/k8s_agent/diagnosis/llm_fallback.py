"""LLM-assisted diagnosis fallback using Claude."""

import json
import structlog
import anthropic
from ..types import Symptom, Diagnosis, FailureType, RemediationType, RiskLevel
from ..config import Settings

logger = structlog.get_logger()


class LLMDiagnoser:
    """Fallback diagnoser that queries Claude to diagnose complex failures."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def diagnose(self, symptom: Symptom) -> Diagnosis | None:
        """Query Claude to diagnose the Kubernetes failure symptom."""
        logger.info(
            "Querying LLM for fallback diagnosis",
            pod=symptom.pod_name,
            failure_type=symptom.failure_type,
        )

        prompt = self._build_prompt(symptom)

        # Definition of diagnosis tool to force structured output
        tools = [
            {
                "name": "report_diagnosis",
                "description": "Report the diagnosis and remediation steps for the container failure",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "root_cause": {
                            "type": "string",
                            "description": "Clear explanation of why the container failed/crashed.",
                        },
                        "remediation_type": {
                            "type": "string",
                            "enum": [member.value for member in RemediationType],
                            "description": "Recommended remediation type.",
                        },
                        "remediation_params": {
                            "type": "object",
                            "description": "Arguments/params for remediation (e.g. memory_increase_factor, target_image_tag). Keys depend on remediation_type.",
                        },
                        "risk_level": {
                            "type": "string",
                            "enum": [member.value for member in RiskLevel],
                            "description": "Level of operational risk associated with this remediation.",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "Confidence score in this diagnosis (0.0 to 1.0).",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "Step-by-step reasoning explaining why the root cause and remediation were selected.",
                        },
                        "requires_approval": {
                            "type": "boolean",
                            "description": "True if executing this remediation requires manual human approval, False if it can be automated.",
                        },
                    },
                    "required": [
                        "root_cause",
                        "remediation_type",
                        "remediation_params",
                        "risk_level",
                        "confidence",
                        "reasoning",
                        "requires_approval",
                    ],
                },
            }
        ]

        try:
            response = self.client.messages.create(
                model=self.settings.llm.model,
                max_tokens=self.settings.llm.max_tokens,
                temperature=self.settings.llm.temperature,
                system=(
                    "You are an expert Kubernetes reliability engineer. Your job is to analyze log "
                    "dumps, status reports, and pod event logs to determine the root cause of a pod failure "
                    "and recommend safe remediation steps. You must invoke the 'report_diagnosis' tool "
                    "to report your findings."
                ),
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
                tool_choice={"type": "tool", "name": "report_diagnosis"},
                timeout=self.settings.llm.timeout_seconds,
            )

            # Extract structured tool call
            tool_use = next(
                (block for block in response.content if block.type == "tool_use"), None
            )
            if not tool_use:
                logger.error("LLM did not invoke the diagnosis tool")
                return None

            result_data = tool_use.input
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
