"""Orchestrator for the diagnosis engine."""

import structlog
from ..types import Symptom, Diagnosis
from ..config import Settings
from .rules import RuleMatcher
from .llm_fallback import LLMDiagnoser

logger = structlog.get_logger()


class DiagnosisEngine:
    """Coordinates fast rule-based matching and LLM-assisted fallback diagnosis."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.rule_matcher = RuleMatcher()
        self.llm_diagnoser = LLMDiagnoser(settings)

    def diagnose(self, symptom: Symptom) -> Diagnosis | None:
        """Diagnose a symptom, checking rules first, falling back to LLM."""
        logger.info(
            "Diagnosing symptom",
            pod=symptom.pod_name,
            failure_type=symptom.failure_type,
        )

        # 1. Fast Path: Rule Engine
        diagnosis = self.rule_matcher.diagnose(symptom)
        if diagnosis:
            logger.info(
                "Symptom diagnosed by rule matcher",
                pod=symptom.pod_name,
                root_cause=diagnosis.root_cause,
            )
            return diagnosis

        # 2. Slow/Detailed Path: LLM Fallback
        logger.info(
            "No rule matches found. Falling back to LLM diagnosis.",
            pod=symptom.pod_name,
        )
        diagnosis = self.llm_diagnoser.diagnose(symptom)
        if diagnosis:
            logger.info(
                "Symptom diagnosed by LLM fallback",
                pod=symptom.pod_name,
                root_cause=diagnosis.root_cause,
            )
            return diagnosis

        logger.warning("Could not diagnose symptom", pod=symptom.pod_name)
        return None
