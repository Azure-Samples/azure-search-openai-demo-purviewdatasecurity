# Microsoft Entra app architecture and permissions

This sample uses Microsoft Entra ID for two related purposes when `AZURE_USE_AUTHENTICATION=true`:

1. Sign users in to the web application.
2. Preserve the signed-in user's identity when querying Azure AI Search, so Purview sensitivity-label authorization can evaluate whether that user can access each search result.

The setup is created by the `preprovision` hook in `azure.yaml`, which runs `scripts/auth_init.py`. After deployment, the `postprovision` hook runs `scripts/auth_update.py` to add the deployed redirect URI.

## App registrations

`scripts/auth_init.py` creates or updates two Microsoft Entra app registrations:

| App registration | Purpose |
| --- | --- |
| Client app, named `Azure Search OpenAI Chat Client App <id>` | Represents the browser-facing web experience. Azure App Service authentication or Azure Container Apps authentication uses this app's client ID and secret to sign users in and issue tokens for the web app. |
| Server app, named `Azure Search OpenAI Chat Server App <id>` | Represents the backend API. It exposes the `api://<server-app-id>/access_as_user` scope and lists the client app as a known client application so combined consent works for the frontend-to-backend flow. |

The deployed host is configured with the client app ID, the client secret, and the server app audience `api://<server-app-id>`. Its login scopes include `api://<server-app-id>/.default`, `openid`, `profile`, `email`, and `offline_access`. The `.default` scope is intentionally first so the platform auth integration can request combined consent for the server app scopes.

## Request flow

1. The user opens the web app and signs in through Microsoft Entra ID.
2. The platform auth layer validates the sign-in using the client app registration and stores tokens in the platform token store. Azure Container Apps stores tokens in the deployment's `tokens` blob container through the backend managed identity.
3. The frontend calls the backend API. The token audience is the server app (`api://<server-app-id>`), so the backend can trust that the request was issued for this API.
4. When access control is enforced, the backend uses the signed-in user's delegated context to query Azure AI Search.
5. Azure AI Search and Purview evaluate the sensitivity labels on indexed content against the user's identity, so the answer is grounded only in documents the user is allowed to access.

## Why the permissions are required

The server app requests the permissions needed by the backend and by the delegated Search query path:

| Permission | Type | Why it is needed |
| --- | --- | --- |
| `User.Read` | Microsoft Graph delegated scope | Reads basic signed-in user information used by the authentication flow. |
| `openid`, `profile`, `email` | Microsoft Graph delegated scopes | Requests OpenID Connect identity claims for sign-in and user display information. |
| `offline_access` | Microsoft Graph delegated scope | Allows refresh tokens so the platform token store can maintain the user's session and request downstream tokens without prompting on every request. |
| `user_impersonation` for Azure AI Search | Delegated scope | Lets the backend request a delegated Azure AI Search token for the signed-in user. This is what lets Search and Purview evaluate document access as the user instead of as the application. |
| `SensitivityLabels.Read.All` | Delegated scope | Lets the delegated flow read Purview sensitivity label information needed for label-aware authorization. |
| `SensitivityLabels.Read.All` | Microsoft Graph application role | Lets the backend resolve sensitivity label metadata in app-only scenarios where delegated label metadata is not enough. |
| `SensitivityLabel.Evaluate.All` | Microsoft Graph application role | Lets the backend call label evaluation APIs required to determine how Purview labels apply during authorization. |

The client app only requests the backend API's `access_as_user` scope and Graph `User.Read`, because it should not directly call Purview or Azure AI Search. Keeping those permissions on the server app centralizes downstream access in the backend and avoids granting broad data permissions to browser code.

## Admin consent

After `azd up` creates the app registrations, a tenant administrator must grant admin consent on the server app registration. Without admin consent, users may be able to sign in but the delegated Search token or Purview label calls can fail, which prevents sensitivity-label-enforced search from working.

The setup script also assigns the Microsoft Graph application roles for Purview label resolution to the server app's service principal. Those app roles are still tenant-wide application permissions, so they should be reviewed and approved by an administrator before production use.

## Azure AI Search managed identity permissions

During document ingestion on Windows, `scripts/prepdocs.ps1` assigns Entra app roles to the Azure AI Search service managed identity:

| Resource service principal | Why it is needed |
| --- | --- |
| Microsoft Information Protection | Lets Azure AI Search work with Microsoft Purview Information Protection label metadata while indexing protected content. |
| Azure Rights Management Services | Lets Azure AI Search process rights-managed protected content that is encrypted by Purview labels. |

These permissions are separate from the user-facing client and server app registrations. They are for the Search service managed identity that performs ingestion and indexing, not for end-user sign-in.
