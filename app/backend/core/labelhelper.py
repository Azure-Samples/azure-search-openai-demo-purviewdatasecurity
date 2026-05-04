"""
Sensitivity Label Helper for Microsoft Purview Integration
Handles extraction, inheritance, and display of sensitivity labels from search results.
"""

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp
from msal import ConfidentialClientApplication


class LabelError(Exception):
    """Base exception for label-related errors"""

    pass


@dataclass
class LabelConfig:
    """Configuration constants for label processing"""

    # Cache settings
    CACHE_DURATION_SECONDS: int = 2 * 60 * 60  # 2 hours
    CACHE_MAX_SIZE: int = 1000  # Maximum number of labels to cache

    # API settings
    API_TIMEOUT_SECONDS: float = 10.0
    CREDENTIAL_TIMEOUT_SECONDS: int = 60
    GRAPH_API_SCOPE: str = "https://graph.microsoft.com/.default"
    GRAPH_LABEL_URL_TEMPLATE: str = (
        "https://graph.microsoft.com/v1.0/security/dataSecurityAndGovernance/sensitivityLabels/{label_id}"
    )

    # Default colors (hex values)
    DEFAULT_COLOR: str = "#808080"  # Gray
    FALLBACK_COLOR: str = "#FFA500"  # Orange
    STRING_LABEL_COLOR: str = "#FFA500"  # Orange

    # Default icons
    DEFAULT_ICON: str = "Info"
    SUCCESS_ICON: str = "Shield"
    WARNING_ICON: str = "Warning"

    # Fallback text
    UNKNOWN_SOURCE: str = "unknown"


@dataclass
class SensitivityLabel:
    """Represents a sensitivity label with metadata"""

    id: str
    name: str
    display_name: Optional[str] = None
    color: str = LabelConfig.DEFAULT_COLOR
    priority: int = 0
    icon: str = LabelConfig.DEFAULT_ICON


@dataclass
class DocumentLabel:
    """Label information for a specific document"""

    document_id: str
    source_file: str
    label: SensitivityLabel


@dataclass
class ResponseSensitivity:
    """Overall response sensitivity computed from document labels"""
    overall_label: SensitivityLabel
    document_labels: list[DocumentLabel]


class LabelHelper:
    def __init__(
        self,
        config: Optional[LabelConfig] = None,
        tenant_id: Optional[str] = None,
        server_app_id: Optional[str] = None,
        server_app_secret: Optional[str] = None,
    ):
        self._config = config or LabelConfig()
        self._label_cache: dict[str, tuple[Optional[SensitivityLabel], float]] = {}
        self._cache_duration_seconds = self._config.CACHE_DURATION_SECONDS
        self._credential = None
        self._tenant_id = tenant_id or os.getenv("AZURE_AUTH_TENANT_ID") or os.getenv("AZURE_TENANT_ID")
        self._server_app_id = server_app_id or os.getenv("AZURE_SERVER_APP_ID")
        self._server_app_secret = server_app_secret or os.getenv("AZURE_SERVER_APP_SECRET")
        self._app_graph_token: Optional[tuple[str, float]] = None

    def _get_cached_label(self, label_id: str) -> Optional[SensitivityLabel]:
        """Retrieve a label from cache if it exists and is still valid."""
        try:
            if label_id not in self._label_cache:
                return None

            cached_label, timestamp = self._label_cache[label_id]
            if (time.time() - timestamp) < self._cache_duration_seconds:
                return cached_label

            # Remove expired entry
            del self._label_cache[label_id]
            return None
        except KeyError:
            return None

    def _cache_label(self, label_id: str, label: Optional[SensitivityLabel]) -> None:
        """Store a label in cache with current timestamp. If cache is full, remove oldest entries"""
        # cache eviction
        if len(self._label_cache) >= self._config.CACHE_MAX_SIZE:
            # Remove expired entries first
            now = time.time()
            expired_keys = [
                key for key, (_, timestamp) in self._label_cache.items()
                if (now - timestamp) >= self._cache_duration_seconds
            ]
            for key in expired_keys:
                del self._label_cache[key]

            # If still at capacity, remove oldest entry
            if len(self._label_cache) >= self._config.CACHE_MAX_SIZE:
                oldest_key = min(self._label_cache.items(), key=lambda x: x[1][1])[0]
                del self._label_cache[oldest_key]

        self._label_cache[label_id] = (label, time.time())

    def _get_app_graph_token(self) -> Optional[str]:
        """Acquire an app-only Graph token for tenant-wide Purview label metadata."""
        if not self._tenant_id or not self._server_app_id or not self._server_app_secret:
            return None

        if self._app_graph_token:
            token, expires_at = self._app_graph_token
            if time.time() < expires_at - 300:
                return token

        authority = f"https://login.microsoftonline.com/{self._tenant_id}"
        client = ConfidentialClientApplication(
            self._server_app_id,
            authority=authority,
            client_credential=self._server_app_secret,
        )
        token_result = client.acquire_token_for_client(scopes=[self._config.GRAPH_API_SCOPE])
        if "access_token" not in token_result:
            logging.warning("Failed to acquire app-only Graph token for Purview labels: %s", token_result.get("error"))
            return None

        expires_in = int(token_result.get("expires_in", 3600))
        access_token = token_result["access_token"]
        self._app_graph_token = (access_token, time.time() + expires_in)
        return access_token

    async def _get_label_data(self, label_id: str, access_token: str, token_source: str) -> Optional[dict[str, Any]]:
        url = self._config.GRAPH_LABEL_URL_TEMPLATE.format(label_id=label_id)
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "User-Agent": "Purview-Python-Client",
            }

            async with session.get(url, headers=headers, timeout=self._config.API_TIMEOUT_SECONDS) as response:
                if response.status == 200:
                    return await response.json()

                response_text = await response.text()
                logging.warning(
                    "Failed to resolve Purview label %s with %s Graph token. Status: %s. Response: %s",
                    label_id,
                    token_source,
                    response.status,
                    response_text,
                )
                return None

    async def _resolve_purview_label(
        self,
        label_id: str,
        access_token: Optional[str] = None,
        visited: Optional[set[str]] = None,
    ) -> Optional[SensitivityLabel]:
        """
        Resolve a Purview label GUID to a SensitivityLabel using Microsoft Graph API.
        Results are cached for 2 hours to reduce API calls.
        """
        if cached_label := self._get_cached_label(label_id):
            return cached_label
        visited = visited or set()
        if label_id in visited:
            logging.warning("Detected circular sensitivity label hierarchy for %s", label_id)
            return None
        visited.add(label_id)

        try:
            token_candidates = []
            if access_token:
                token_candidates.append(("delegated", access_token))

            app_access_token = self._get_app_graph_token()
            if app_access_token and app_access_token != access_token:
                token_candidates.append(("app-only", app_access_token))

            for token_source, candidate_token in token_candidates:
                label_data = await self._get_label_data(label_id, candidate_token, token_source)
                if label_data:
                    label = await self._create_resolved_label(label_id, label_data, candidate_token, visited)
                    self._cache_label(label_id, label)
                    return label

        except Exception:
            logging.warning("Failed to resolve label: %s", label_id, exc_info=True)
        finally:
            visited.discard(label_id)
        return None

    async def _create_resolved_label(
        self,
        label_id: str,
        label_data: dict[str, Any],
        access_token: str,
        visited: set[str],
    ) -> SensitivityLabel:
        label_name_raw = label_data.get("name") or label_data.get("displayName")
        label_name = (
            label_name_raw.strip()
            if isinstance(label_name_raw, str) and label_name_raw.strip()
            else f"Label-{label_id[:8]}"
        )
        raw_segment_display = label_data.get("displayName") or label_data.get("name")
        segment_display_name = (
            raw_segment_display.strip()
            if isinstance(raw_segment_display, str) and raw_segment_display.strip()
            else label_name
        )
        full_display_name = await self._build_full_label_display_name(
            label_id,
            label_data,
            segment_display_name,
            access_token,
            visited,
        )

        return SensitivityLabel(
            id=label_id,
            name=label_name,
            display_name=full_display_name,
            color=label_data.get("color", self._config.DEFAULT_COLOR),
            priority=label_data.get("priority", 0),
            icon=self._config.SUCCESS_ICON,
        )

    async def _build_full_label_display_name(
        self,
        current_label_id: str,
        label_data: dict,
        segment_display_name: str,
        access_token: Optional[str],
        visited: set[str],
    ) -> str:
        """Construct the hierarchical display name for a label by walking its parent chain."""
        parent_id = self._extract_parent_id(label_data)

        if not parent_id or parent_id == current_label_id:
            return segment_display_name

        parent_label = await self._resolve_purview_label(parent_id, access_token, visited)
        if parent_label:
            parent_display = parent_label.display_name or parent_label.name
            if parent_display:
                return f"{parent_display}\\{segment_display_name}"

        return segment_display_name

    @staticmethod
    def _extract_parent_id(label_data: dict) -> Optional[str]:
        """Get the parent label id from custom settings if present."""
        settings = label_data.get("customSettings") or []
        for setting in settings:
            if setting.get("name", "").lower() == "parentid":
                parent_id = setting.get("value")
                if parent_id:
                    return parent_id
        return None

    async def extract_labels_from_search_results(
        self, search_results, user_access_token: Optional[str] = None
    ) -> list[DocumentLabel]:
        """Extract sensitivity labels from search results"""
        document_labels = []

        for i, result in enumerate(search_results):
            doc_id = result.id or f"unknown_{i}"
            source_file = result.sourcefile or result.sourcepage or self._config.UNKNOWN_SOURCE
            sensitivity_label = result.sensitivity_label

            if not sensitivity_label:
                continue

            # Try to resolve as GUID first, then fallback to string label
            label = None
            if self._is_guid(sensitivity_label):
                label = await self._resolve_purview_label(sensitivity_label, user_access_token)
                if not label:
                    # Create fallback GUID label if resolution failed or returned None
                    label = SensitivityLabel(
                        id=sensitivity_label,
                        name=f"Purview Label ({sensitivity_label[:8]}...)",
                        display_name=f"Purview Label (ID: {sensitivity_label[:8]}...)",
                        color=self._config.FALLBACK_COLOR,
                        priority=0,
                        icon=self._config.WARNING_ICON,
                    )
            else:
                label = self._create_label_from_string(sensitivity_label)

            document_labels.append(DocumentLabel(document_id=doc_id, source_file=source_file, label=label))

        return document_labels

    def _is_guid(self, value: str) -> bool:
        """Check if a string is a valid GUID"""
        try:
            uuid.UUID(value)
            return True
        except ValueError:
            return False

    def _create_label_from_string(self, label_name: str) -> SensitivityLabel:
        """Create a SensitivityLabel from a label name string"""
        return SensitivityLabel(
            id=label_name.lower().replace(" ", "-"),
            name=label_name,
            display_name=label_name,
            color=self._config.STRING_LABEL_COLOR,
            priority=0,
            icon=self._config.DEFAULT_ICON,
        )

    async def compute_label_inheritance(self, document_labels: list[DocumentLabel]) -> ResponseSensitivity:
        """Compute the overall sensitivity label for a response based on document labels."""
        if not document_labels:
            return None

        # Find highest priority label, or use first document
        priority_labels = [dl for dl in document_labels if dl.label.priority > 0]
        chosen_label = (
            max(priority_labels, key=lambda dl: dl.label.priority).label
            if priority_labels else document_labels[0].label
        )

        return ResponseSensitivity(overall_label=chosen_label, document_labels=document_labels)
