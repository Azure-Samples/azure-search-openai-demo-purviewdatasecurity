import os

DEFAULT_SEARCH_API_VERSION = "2025-11-01-preview"
PURVIEW_SENSITIVITY_LABEL_FIELD = "sensitivityLabel"


def get_search_api_version() -> str:
    return os.getenv("AZURE_SEARCH_API_VERSION", DEFAULT_SEARCH_API_VERSION)
