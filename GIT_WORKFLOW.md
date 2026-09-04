# Git workflow

This project keeps production code on `main`. New work is developed and tested on a short-lived `feature/...` branch before it is merged into `main`.

This is simpler than maintaining a permanent `develop` branch while only one person is working on the project. Add a permanent `develop` branch later if several changes need to be tested together before release.

## One-time setup

Confirm that the local repository is connected to GitHub:

```powershell
git remote -v
git switch main
git pull --ff-only origin main
```

## Start a change

Always create the feature branch from the latest production code:

```powershell
git switch main
git pull --ff-only origin main
git switch -c feature/improve-address-matching
```

Make and test the changes on this branch:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Save and publish the feature branch

Review the files before committing. Do not use `git add .` blindly when unexpected files appear.

```powershell
git status
git diff
git add README.md app.py nyc_data.py tests
git commit -m "Improve address matching"
git push -u origin feature/improve-address-matching
```

The files after `git add` are only examples. Add the files that actually belong to the change. The repository's `.gitignore` excludes `.venv`, `.env`, ZIP files, caches, and logs.

## Deploy and test the Dev service

While still on the feature branch, deploy it under the Dev Cloud Run service name:

```powershell
.\deploy_cloud_run.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -Service "get-address-info-dev"
```

Use the Dev URL to test Excel upload, address results, and ZIP/CO downloads. Do not deploy the feature branch to the production service.

## Release to production

After Dev testing succeeds, merge the feature into `main`, rerun the tests, push `main`, and deploy the production service:

```powershell
git switch main
git pull --ff-only origin main
git merge --no-ff feature/improve-address-matching
.\.venv\Scripts\python.exe -m pytest -q
git push origin main

.\deploy_cloud_run.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -Service "get-address-info-prod"
```

Each command must be entered separately. Do not type `git switch main git merge ...` on one line.

## Clean up the feature branch

After production is working:

```powershell
git branch -d feature/improve-address-matching
git push origin --delete feature/improve-address-matching
```

Deleting the feature branch does not delete the merged changes from `main`.

## Optional permanent develop branch

If the project later has multiple developers or several features waiting for combined testing, create `develop` once:

```powershell
git switch main
git pull --ff-only origin main
git switch -c develop
git push -u origin develop
```

Then merge feature branches into `develop`, deploy `develop` to the Dev service, and merge `develop` into `main` only after testing. Do not run `git switch develop` until the branch has been created locally or fetched from GitHub.
