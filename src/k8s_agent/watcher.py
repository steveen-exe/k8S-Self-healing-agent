"""Kubernetes cluster state watcher."""

import structlog
from kubernetes import client, config as k8s_config, watch
from typing import Iterator

from .types import Symptom, FailureType
from .config import Settings

logger = structlog.get_logger()


class PodWatcher:
    """Watches Kubernetes pods for failure symptoms."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._setup_k8s_client()

    def _setup_k8s_client(self) -> None:
        """Initialize Kubernetes client."""
        if self.settings.kubeconfig_path:
            k8s_config.load_kube_config(config_file=self.settings.kubeconfig_path)
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                logger.warning("Not running in cluster, loading local kubeconfig")
                k8s_config.load_kube_config()

        self.v1 = client.CoreV1Api()

    def watch_pods(self) -> Iterator[Symptom]:
        """Watch pods and yield symptoms when failures detected."""
        logger.info("Starting pod watcher", namespaces=self.settings.watcher.namespaces)

        for namespace in self.settings.watcher.namespaces:
            try:
                pods = self.v1.list_namespaced_pod(namespace=namespace)

                for pod in pods.items:
                    symptom = self._check_pod_health(pod, namespace)
                    if symptom:
                        yield symptom

            except Exception as e:
                logger.error("Error watching namespace", namespace=namespace, error=str(e))

    def _check_pod_health(self, pod: client.V1Pod, namespace: str) -> Symptom | None:
        """Check if pod has failure symptoms worth diagnosing."""
        if not pod.status or not pod.status.container_statuses:
            return None

        for container_status in pod.status.container_statuses:
            # Skip healthy containers
            if container_status.ready:
                continue

            # Check restart count threshold
            restart_count = container_status.restart_count or 0
            if restart_count < self.settings.watcher.min_restart_count_threshold:
                continue

            waiting_state = container_status.state.waiting if container_status.state else None
            if not waiting_state:
                continue

            failure_type = self._classify_failure(waiting_state.reason)
            if failure_type == FailureType.UNKNOWN:
                continue

            # Get recent logs
            logs = self._get_container_logs(pod.metadata.name, namespace, container_status.name)

            # Get recent events
            events = self._get_pod_events(pod.metadata.name, namespace)

            return Symptom(
                namespace=namespace,
                pod_name=pod.metadata.name,
                container_name=container_status.name,
                failure_type=failure_type,
                message=waiting_state.message or waiting_state.reason,
                logs=logs,
                restart_count=restart_count,
                last_state=self._serialize_container_state(container_status.last_state),
                events=events,
            )

        return None

    def _classify_failure(self, reason: str | None) -> FailureType:
        """Map Kubernetes waiting reason to failure type."""
        if not reason:
            return FailureType.UNKNOWN

        reason_map = {
            "CrashLoopBackOff": FailureType.CRASH_LOOP_BACKOFF,
            "ImagePullBackOff": FailureType.IMAGE_PULL_BACKOFF,
            "ErrImagePull": FailureType.IMAGE_PULL_BACKOFF,
            "OOMKilled": FailureType.OOM_KILLED,
        }

        return reason_map.get(reason, FailureType.UNKNOWN)

    def _get_container_logs(
        self, pod_name: str, namespace: str, container_name: str, tail_lines: int = 50
    ) -> str:
        """Fetch recent container logs."""
        try:
            logs = self.v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container_name,
                tail_lines=tail_lines,
            )
            return logs
        except Exception as e:
            logger.warning(
                "Failed to fetch logs",
                pod=pod_name,
                container=container_name,
                error=str(e),
            )
            return ""

    def _get_pod_events(self, pod_name: str, namespace: str) -> list[dict]:
        """Fetch recent events for the pod."""
        try:
            events = self.v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}",
            )

            return [
                {
                    "type": event.type,
                    "reason": event.reason,
                    "message": event.message,
                    "count": event.count,
                    "first_timestamp": event.first_timestamp.isoformat() if event.first_timestamp else None,
                    "last_timestamp": event.last_timestamp.isoformat() if event.last_timestamp else None,
                }
                for event in sorted(
                    events.items,
                    key=lambda e: e.last_timestamp or e.first_timestamp,
                    reverse=True,
                )[:10]  # Last 10 events
            ]
        except Exception as e:
            logger.warning("Failed to fetch events", pod=pod_name, error=str(e))
            return []

    def _serialize_container_state(self, state) -> dict:
        """Convert container state to serializable dict."""
        if not state:
            return {}

        if state.terminated:
            return {
                "state": "terminated",
                "exit_code": state.terminated.exit_code,
                "reason": state.terminated.reason,
                "message": state.terminated.message,
            }
        elif state.waiting:
            return {
                "state": "waiting",
                "reason": state.waiting.reason,
                "message": state.waiting.message,
            }
        elif state.running:
            return {
                "state": "running",
                "started_at": state.running.started_at.isoformat() if state.running.started_at else None,
            }

        return {}
