param(
  [Parameter(Mandatory=$true)][string]$ProjectId,
  [string]$Region="us-east1",
  [string]$Service="get-address-info"
)
$ErrorActionPreference="Stop"
gcloud config set project $ProjectId
$deployArgs=@(
  "run","deploy",$Service,
  "--source",".",
  "--region",$Region,
  "--allow-unauthenticated",
  "--memory","1Gi",
  "--cpu","1",
  "--concurrency","1",
  "--min","0",
  "--max","1",
  "--timeout","3600",
  "--set-env-vars","LOOKUP_WORKERS=1,MAX_ADDRESSES=100,MAX_UPLOAD_MB=20"
)
& gcloud @deployArgs