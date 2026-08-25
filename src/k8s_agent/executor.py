"""Remediation executor for applying fixes to Kubernetes resources."""

import structlog
from kubernetes import client
from datetime import datetime

from .types import Diagnosis, RemediationResult, RemediationType
from .config import Settings

logger = structlog.get_logger()


class Executor:
    """Executes remediation actions against Kubernetes cluster."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def execute(self, diagnosis: Diagnosis, dry_run: bool = None) -> RemediationResult:
        """Execute the remediation action specified in diagnosis."""
        # Override with global dry-run setting if not specified
        if dry_run is None:
            dry_run = self.settings.policy.dry_run_only

        logger.info(
            "Executing remediation",
            pod=diagnosis.symptom.pod_name,
            remediation=diagnosis.remediation_type.value,
            dry_run=dry_run,
        )

        try:
            match diagnosis.remediation_type:
                case RemediationType.RESTART_POD:
                    output = self._restart_pod(diagnosis, dry_run)
                case RemediationType.INCREASE_MEMORY:
                    output = self._increase_memory(diagnosis, dry_run)
                case RemediationType.ROLLBACK_DEPLOYMENT:
                    output = self._rollback_deployment(diagnosis, dry_run)
                case RemediationType.NO_ACTION:
                    output = "No action required (diagnosis suggests manual intervention)"
                case _:
                    output = f"Remediation type {diagnosis.remediation_type.value} not implemented"

            return RemediationResult(
                diagnosis=diagnosis,
                executed=not dry_run,
                success=True,
                dry_run=dry_run,
                output=output,
            )

        except Exception as e:
            logger.error(
                "Remediation failed",
                pod=diagnosis.symptom.pod_name,
                error=str(e),
            )
            return RemediationResult(
                diagnosis=diagnosis,
                executed=False,
                success=False,
                dry_run=dry_run,
                output="",
                error=str(e),
            )

    def _restart_pod(self, diagnosis: Diagnosis, dry_run: bool) -> str:
        """Restart a pod by deleting it (will be recreated by controller)."""
        symptom = diagnosis.symptom

        if dry_run:
            return f"[DRY RUN] Would delete pod {symptom.pod_name} in namespace {symptom.namespace}"

        self.v1.delete_namespaced_pod(
            name=symptom.pod_name,
            namespace=symptom.namespace,
        )

        return f"Deleted pod {symptom.pod_name} (will be recreated by controller)"

    def _increase_memory(self, diagnosis: Diagnosis, dry_run: bool) -> str:
        """Increase memory limits for a container in a deployment."""
        symptom = diagnosis.symptom
        factor = diagnosis.remediation_params.get("memory_increase_factor", 1.5)

        # Find the deployment controlling this pod
        pod = self.v1.read_namespaced_pod(
            name=symptom.pod_name,
            namespace=symptom.namespace,
        )

        owner_refs = pod.metadata.owner_references
        if not owner_refs:
            return "Cannot increase memory: pod has no owner (not managed by deployment/statefulset)"

        # Find deployment from ReplicaSet owner
        deployment_name = None
        for ref in owner_refs:
            if ref.kind == "ReplicaSet":
                rs = self.apps_v1.read_namespaced_replica_set(
                    name=ref.name,
                    namespace=symptom.namespace,
                )
                if rs.metadata.owner_references:
                    for rs_ref in rs.metadata.owner_references:
                        if rs_ref.kind == "Deployment":
                            deployment_name = rs_ref.name
                            break

        if not deployment_name:
            return "Cannot increase memory: deployment not found"

        # Read deployment
        deployment = self.apps_v1.read_namespaced_deployment(
            name=deployment_name,
            namespace=symptom.namespace,
        )

        # Find container and update memory
        containers = deployment.spec.template.spec.containers
        target_container = None
        for container in containers:
            if container.name == symptom.container_name:
                target_container = container
                break

        if not target_container:
            return f"Container {symptom.container_name} not found in deployment"

        if not target_container.resources or not target_container.resources.limits:
            return "Cannot increase memory: no memory limit set on container"

        current_memory = target_container.resources.limits.get("memory")
        if not current_memory:
            return "Cannot increase memory: no memory limit defined"

        # Parse memory value (e.g., "512Mi", "1Gi")
        new_memory = self._scale_memory(current_memory, factor)

        if dry_run:
            return (
                f"[DRY RUN] Would update deployment {deployment_name} "
                f"container {symptom.container_name} memory limit: {current_memory} -> {new_memory}"
            )

        # Update deployment
        target_container.resources.limits["memory"] = new_memory
        if target_container.resources.requests and "memory" in target_container.resources.requests:
            target_container.resources.requests["memory"] = new_memory

        self.apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=symptom.namespace,
            body=deployment,
        )

        return (
            f"Updated deployment {deployment_name} container {symptom.container_name} "
            f"memory limit: {current_memory} -> {new_memory}"
        )

    def _rollback_deployment(self, diagnosis: Diagnosis, dry_run: bool) -> str:
        """Rollback deployment to previous revision."""
        symptom = diagnosis.symptom

        # Find deployment (similar logic as increase_memory)
        pod = self.v1.read_namespaced_pod(
            name=symptom.pod_name,
            namespace=symptom.namespace,
        )

        deployment_name = self._find_deployment_name(pod, symptom.namespace)
        if not deployment_name:
            return "Cannot rollback: deployment not found"

        if dry_run:
            return f"[DRY RUN] Would rollback deployment {deployment_name} to previous revision"

        # Trigger rollback via kubectl (simpler than manual revision management)
        # In production, you'd use client-go's proper rollback mechanism
        import subprocess

        result = subprocess.run(
            ["kubectl", "rollout", "undo", f"deployment/{deployment_name}", "-n", symptom.namespace],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise Exception(f"Rollback failed: {result.stderr}")

        return f"Rolled back deployment {deployment_name} to previous revision"

    def _find_deployment_name(self, pod, namespace: str) -> str | None:
        """Helper to find deployment name from pod owner references."""
        owner_refs = pod.metadata.owner_references
        if not owner_refs:
            return None

        for ref in owner_refs:
            if ref.kind == "ReplicaSet":
                rs = self.apps_v1.read_namespaced_replica_set(
                    name=ref.name,
                    namespace=namespace,
                )
                if rs.metadata.owner_references:
                    for rs_ref in rs.metadata.owner_references:
                        if rs_ref.kind == "Deployment":
                            return rs_ref.name
        return None

    def _scale_memory(self, current: str, factor: float) -> str:
        """Scale memory string by factor (e.g., '512Mi' * 1.5 = '768Mi')."""
        import re

        match = re.match(r"(\d+)(.*)", current)
        if not match:
            raise ValueError(f"Cannot parse memory value: {current}")

        value = int(match.group(1))
        unit = match.group(2)

        new_value = int(value * factor)
        return f"{new_value}{unit}"
