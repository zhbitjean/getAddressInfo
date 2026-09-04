param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "us-east1",
    [string]$RepositoryOwner = "zhbitjean",
    [string]$GitHubRepository = "getAddressInfo",
    [string]$RepositoryResourceName = "zhbitjean-getAddressInfo",
    [string]$ConnectionName = "github"
)

$ErrorActionPreference = "Stop"
$gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path -LiteralPath $gcloud)) { $gcloud = "gcloud" }

& $gcloud config set project $ProjectId
& $gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

$artifactExists = & $gcloud artifacts repositories describe get-address-info --location=$Region --format="value(name)" 2>$null
if (-not $artifactExists) {
    & $gcloud artifacts repositories create get-address-info --repository-format=docker --location=$Region --description="getAddressInfo Cloud Run images"
}

$serviceAccountName = "cloud-build-deployer"
$buildAccount = "$serviceAccountName@$ProjectId.iam.gserviceaccount.com"
$serviceAccountExists = & $gcloud iam service-accounts describe $buildAccount --format="value(email)" 2>$null
if (-not $serviceAccountExists) {
    & $gcloud iam service-accounts create $serviceAccountName --display-name="Cloud Build deployer"
}
$roles = @(
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/logging.logWriter"
)
foreach ($role in $roles) {
    & $gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$buildAccount" --role=$role --condition=None --quiet
}

$repositoryResource = "projects/$ProjectId/locations/$Region/connections/$ConnectionName/repositories/$RepositoryResourceName"
& $gcloud builds repositories describe $RepositoryResourceName --connection=$ConnectionName --region=$Region *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "GitHub is not connected yet. Open Cloud Build > Repositories, create a GitHub connection named '$ConnectionName', and link $RepositoryOwner/$GitHubRepository with repository ID '$RepositoryResourceName'. Then run this script again."
    exit 2
}

function Ensure-Trigger {
    param([string]$Name, [string]$BranchPattern, [string]$ServiceName)
    $existing = & $gcloud builds triggers describe $Name --region=$Region --format="value(name)" 2>$null
    if ($existing) { Write-Host "Trigger already exists: $Name"; return }
    & $gcloud builds triggers create github `
        --name=$Name `
        --region=$Region `
        --repository=$repositoryResource `
        --branch-pattern=$BranchPattern `
        --build-config=cloudbuild.yaml `
        --service-account="projects/$ProjectId/serviceAccounts/$buildAccount" `
        --substitutions="_REGION=$Region,_SERVICE_NAME=$ServiceName,_ARTIFACT_REPOSITORY=get-address-info" `
        --include-logs-with-status
}

Ensure-Trigger -Name "get-address-info-dev" -BranchPattern "^develop$" -ServiceName "get-address-info-dev"
Ensure-Trigger -Name "get-address-info-prod" -BranchPattern "^main$" -ServiceName "get-address-info-prod"
Write-Host "Continuous deployment is configured: develop -> Dev, main -> Production."
