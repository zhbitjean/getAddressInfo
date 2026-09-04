# NYC Property Batch Lookup

FastAPI Web App：同事通过浏览器上传含 Jobsite 列的 Excel，服务器查询 NYC 物业资料并直接返回 ZIP。

## 本地运行

双击 start_windows.bat，然后访问 http://127.0.0.1:8000 。

    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000

## Google Cloud Run 部署

项目包含 Dockerfile、.gcloudignore 和 deploy_cloud_run.ps1。需要先安装 Google Cloud CLI、登录，并创建已启用 Billing 的 Google Cloud Project。

    gcloud auth login
    gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
    .\deploy_cloud_run.ps1 -ProjectId "你的 PROJECT_ID"

脚本采用以下费用与稳定性保护：

- Region：us-east1
- CPU：1
- Memory：1 GiB
- Minimum instances：0
- Maximum instances：1
- Container concurrency：1
- Request timeout：60 分钟
- 地址查询线程：1
- 单批地址上限：100
- 允许未登录用户访问

部署完成后，gcloud 会显示一个 HTTPS run.app 地址。同事只需打开该地址。
## Git 开发与发布流程

正式版本保存在 `main`。每次修改先建立临时 `feature/...` 分支，在 Dev Cloud Run 服务测试通过后再合并到 `main` 并部署 Production。完整的逐步命令和可选 `develop` 分支方案见 [GIT_WORKFLOW.md](GIT_WORKFLOW.md)。 推送后自动测试与部署由 `cloudbuild.yaml` 控制；一次性触发器设置使用 `setup_cloud_build.ps1`。

## 为什么查询保持在同一个请求中

Cloud Run 的 request-based billing 可能在 HTTP 请求结束后暂停 CPU。因此程序不会在响应结束后使用后台 Python 线程。浏览器会保持查询请求，服务器完成 ZIP 后直接返回文件。这让应用可以安全缩容到零并使用免费额度。

## 文件和内存限制

- 上传文件最大 20 MB。
- ZIP 发送完成后立即删除服务器临时文件。
- Cloud Run 文件系统是临时且占用实例内存的；不要把它当永久存储。
- 第一阶段适合小批量内部查询。大量 PDF 或长期保存结果时应接入 Cloud Storage。
- NYC 官方网站可能阻止自动 CO 下载；失败详情仍会写入结果文件。
- 当前公开部署没有登录保护。正式内部使用前建议增加 Google 登录或 Identity-Aware Proxy。

## 环境变量

- LOOKUP_WORKERS：查询并发数，默认 1。
- MAX_ADDRESSES：单批地址上限，默认 100。
- MAX_UPLOAD_MB：上传大小上限，默认 20。
