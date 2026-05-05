import logging

from azure.core.exceptions import HttpResponseError
from openai import APIError
from quart import jsonify

ERROR_MESSAGE = """The app encountered an error processing your request.
If you are an administrator of the app, view the full error in the logs. See aka.ms/appservice-logs for more information.
Error type: {error_type}
"""
ERROR_MESSAGE_FILTER = """Your message contains content that was flagged by the OpenAI content filter."""

ERROR_MESSAGE_LENGTH = """Your message exceeded the context length limit for this OpenAI model. Please shorten your message or change your settings to retrieve fewer search results."""

ERROR_MESSAGE_SEARCH_DELEGATED_USER_TENANT = """Azure AI Search rejected the signed-in user's delegated access token.
The signed-in account must be a user in the same tenant as the Azure AI Search service for document-level access control to work. Sign out and sign back in with an account from the app's tenant, or ask an administrator to add this user to that tenant.
"""


def is_search_delegated_user_tenant_error(error: Exception) -> bool:
    return isinstance(error, HttpResponseError) and "DelegatedUserEmail is invalid" in str(error)


def error_dict(error: Exception) -> dict:
    if isinstance(error, APIError) and error.code == "content_filter":
        return {"error": ERROR_MESSAGE_FILTER}
    if isinstance(error, APIError) and error.code == "context_length_exceeded":
        return {"error": ERROR_MESSAGE_LENGTH}
    if is_search_delegated_user_tenant_error(error):
        return {"error": ERROR_MESSAGE_SEARCH_DELEGATED_USER_TENANT}
    return {"error": ERROR_MESSAGE.format(error_type=type(error))}


def error_response(error: Exception, route: str, status_code: int = 500):
    logging.exception("Exception in %s: %s", route, error)
    if isinstance(error, APIError) and error.code == "content_filter":
        status_code = 400
    if is_search_delegated_user_tenant_error(error):
        status_code = 403
    return jsonify(error_dict(error)), status_code
