import logging
import os
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, cast
from urllib.parse import unquote, urlparse

from quart import abort, current_app, request

from config import (
    CONFIG_AUTH_CLIENT,
    CONFIG_BLOB_CONTAINER_CLIENT,
    CONFIG_SEARCH_CLIENT,
)
from core.authentication import AuthError
from error import error_response

AUTHORIZED_BLOB_PATHS = "_authorized_blob_paths"


def blob_path_from_storage_url(storage_url: Optional[str]) -> Optional[str]:
    if not storage_url:
        return None

    blob_container_client = current_app.config[CONFIG_BLOB_CONTAINER_CLIENT]
    container_name = blob_container_client.container_name
    url_path = unquote(urlparse(storage_url).path).lstrip("/")
    container_prefix = f"{container_name}/"
    if not url_path.startswith(container_prefix):
        return None

    return url_path[len(container_prefix) :]


def add_blob_candidate(candidates: list[str], candidate: Optional[str]) -> None:
    if candidate and candidate not in candidates:
        candidates.append(candidate)


def add_blob_path_candidate(candidates: list[str], path: Optional[str]) -> None:
    if not path or urlparse(path).scheme in ("http", "https"):
        return

    path_without_fragment = path.split("#", 1)[0]
    for candidate in (path_without_fragment, os.path.basename(path_without_fragment)):
        if candidate and candidate not in candidates:
            candidates.append(candidate)


async def authorized_blob_paths_by_search(path: str, auth_claims: dict[str, Any]) -> list[str]:
    access_token = auth_claims.get("access_token")
    if not access_token:
        return []

    search_client = current_app.config[CONFIG_SEARCH_CLIENT]
    path_without_fragment = path.split("#", 1)[0]
    path_candidates = [path_without_fragment, os.path.basename(path_without_fragment)]
    filter_clauses = []
    for candidate in dict.fromkeys(path_candidates):
        escaped_candidate = candidate.replace("'", "''")
        filter_clauses.append(f"sourcefile eq '{escaped_candidate}'")
        filter_clauses.append(f"sourcepage eq '{escaped_candidate}'")

    results = await search_client.search(
        search_text="*",
        top=1,
        filter=" or ".join(filter_clauses),
        x_ms_query_source_authorization=access_token,
    )
    async for page in results.by_page():
        async for document in page:
            candidates: list[str] = []
            storage_url = document.get("storageUrl")
            add_blob_candidate(candidates, storage_url)
            add_blob_path_candidate(candidates, blob_path_from_storage_url(storage_url))
            add_blob_path_candidate(candidates, document.get("sourcefile"))
            add_blob_path_candidate(candidates, document.get("sourcepage"))
            add_blob_path_candidate(candidates, path_without_fragment)
            return candidates
    return []


def authenticated_path(route_fn: Callable[[str, dict[str, Any]], Any]):
    """
    Decorator for routes that request a specific file.
    """

    @wraps(route_fn)
    async def auth_handler(path=""):
        auth_helper = current_app.config[CONFIG_AUTH_CLIENT]
        authorized_blob_paths = [path]
        try:
            auth_claims = await auth_helper.get_auth_claims_if_enabled(request.headers)
            if auth_helper.require_access_control:
                authorized_blob_paths = await authorized_blob_paths_by_search(path, auth_claims)
        except AuthError:
            abort(403)
        except Exception as error:
            logging.exception("Problem checking authentication %s", error)
            return error_response(error, route="/content")

        if not authorized_blob_paths:
            abort(403)

        auth_claims[AUTHORIZED_BLOB_PATHS] = authorized_blob_paths
        return await route_fn(path, auth_claims)

    return auth_handler


_C = TypeVar("_C", bound=Callable[..., Any])


def authenticated(route_fn: _C) -> _C:
    """
    Decorator for routes that might require access control. Unpacks Authorization header information into an auth_claims dictionary
    """

    @wraps(route_fn)
    async def auth_handler(*args, **kwargs):
        auth_helper = current_app.config[CONFIG_AUTH_CLIENT]
        try:
            auth_claims = await auth_helper.get_auth_claims_if_enabled(request.headers)
        except AuthError:
            abort(403)
        except Exception as error:
            logging.exception("Problem checking authentication %s", error)
            return error_response(error, route=request.path)

        return await route_fn(auth_claims, *args, **kwargs)

    return cast(_C, auth_handler)
