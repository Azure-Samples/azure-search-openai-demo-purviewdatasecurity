./scripts/load_python_env.ps1

# Setup Entra permissions for Search Service Managed Identity
Write-Host "Setting up Entra permissions for Search Service Managed Identity..."

# Install Microsoft.Entra module if not already installed
if (-not (Get-Module -ListAvailable -Name Microsoft.Entra)) {
    Write-Host "Installing Microsoft.Entra module..."
    Install-Module -Name Microsoft.Entra -AllowClobber -Force
}

# Get Azure environment variables
$subscriptionId = (azd env get-value AZURE_SUBSCRIPTION_ID)
$resourceGroup = (azd env get-value AZURE_SEARCH_SERVICE_RESOURCE_GROUP)
if ([string]::IsNullOrEmpty($resourceGroup)) {
    $resourceGroup = (azd env get-value AZURE_RESOURCE_GROUP)
}
$searchServiceName = (azd env get-value AZURE_SEARCH_SERVICE)
$tenantId = (azd env get-value AZURE_AUTH_TENANT_ID)
if ([string]::IsNullOrEmpty($tenantId)) {
    $tenantId = (azd env get-value AZURE_TENANT_ID)
}

if ([string]::IsNullOrEmpty($subscriptionId) -or [string]::IsNullOrEmpty($resourceGroup) -or [string]::IsNullOrEmpty($searchServiceName) -or [string]::IsNullOrEmpty($tenantId)) {
    Write-Host "Error: Required environment variables not set. Please ensure AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP/AZURE_SEARCH_SERVICE_RESOURCE_GROUP, AZURE_SEARCH_SERVICE, and AZURE_AUTH_TENANT_ID/AZURE_TENANT_ID are configured."
    Exit 1
}

# Connect to Entra
Write-Host "Connecting to Entra..."
Connect-Entra -Scopes 'Application.ReadWrite.All' -TenantId $tenantId -NoWelcome

# Get the managed identity of the search service
$resourceIdWithManagedIdentity = "subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.Search/searchServices/$searchServiceName"
$managedIdentityObjectId = (az resource show --ids "/$resourceIdWithManagedIdentity" --query "identity.principalId" --output tsv)

if ([string]::IsNullOrEmpty($managedIdentityObjectId)) {
    Write-Host "Error: Could not retrieve managed identity for search service. Ensure the search service has a managed identity enabled."
    Exit 1
}

Write-Host "Found managed identity: $managedIdentityObjectId"

# Get Microsoft Information Protection (MIP) service principal and assign role
Write-Host "Assigning MIP role..."
$MIPResourceSP = Get-EntraServicePrincipal -Filter "appId eq '870c4f2e-85b6-4d43-bdda-6ed9a579b725'"
try {
    New-EntraServicePrincipalAppRoleAssignment -ServicePrincipalId $managedIdentityObjectId -PrincipalId $managedIdentityObjectId -ResourceId $MIPResourceSP.Id -Id "8b2071cd-015a-4025-8052-1c0dba2d3f64"
    Write-Host "MIP role assigned successfully"
} catch {
    Write-Host "Warning: MIP role assignment failed or already exists: $_"
}

# Get Azure Rights Management Service (ARMS) service principal and assign role
Write-Host "Assigning ARMS role..."
$ARMSResourceSP = Get-EntraServicePrincipal -Filter "appId eq '00000012-0000-0000-c000-000000000000'"
try {
    New-EntraServicePrincipalAppRoleAssignment -ServicePrincipalId $managedIdentityObjectId -PrincipalId $managedIdentityObjectId -ResourceId $ARMSResourceSP.Id -Id "7347eb49-7a1a-43c5-8eac-a5cd1d1c7cf0"
    Write-Host "ARMS role assigned successfully"
} catch {
    Write-Host "Warning: ARMS role assignment failed or already exists: $_"
}

Write-Host "Entra permissions setup complete"

$venvPythonPath = "./.venv/scripts/python.exe"
if (Test-Path -Path "/usr") {
  # fallback to Linux venv path
  $venvPythonPath = "./.venv/bin/python"
}

Write-Host 'Running "prepdocs.py"'


$cwd = (Get-Location)
$dataArg = "`"$cwd/data/*`""
$additionalArgs = ""
if ($args) {
  $additionalArgs = "$args"
}

$argumentList = "./app/backend/prepdocs.py $dataArg --verbose $additionalArgs"

$argumentList

Start-Process -FilePath $venvPythonPath -ArgumentList $argumentList -Wait -NoNewWindow
