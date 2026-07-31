# NYC Property Batch Lookup

本地网页工具：上传含 `Jobsite` 列的 Excel，批量查询 NYC 物业资料并下载 ZIP。

## 安装和运行（Windows）

最简单的方法：双击 `start_windows.bat`。第一次运行会自动创建独立环境并安装依赖，然后打开浏览器。

也可以在 PowerShell 中运行：

```powershell
cd C:\repo\getAddressInfo
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py app.py
```

浏览器打开 <http://127.0.0.1:5000>。

## ZIP 内容

- `property_results.xlsx`
  - `Results`：一行一个地址，方便筛选。
  - `Report View`：一个地址一列，接近参考截图。
  - `Errors`：查询错误和需要人工复核的项目。
  - `About`：数据来源和免责声明。
- `documents/<地址>/`：程序能够直接取得的 I-Card 和旧版 BIS CO PDF。

## 数据与限制

- 地址匹配：NYC Planning GeoSearch。
- 物业字段：NYC MapPLUTO。
- HPD/I-Card：HPD Open Data 和 HPD Online 历史 PDF。
- CO：旧版 BIS PDF，以及 DOB NOW Certificate of Occupancy Open Data。
- DOB NOW 的新 CO 页面目前没有稳定的公开批量 PDF 直链。程序会记录存在状态并提供 DOB 链接，必要时需人工打开并打印。
- NYC 网站结构可能变化；下载失败不会阻止其余地址出表，会写入 `Errors`/`Notes`。
- 默认同时查询 4 个地址。可通过环境变量 `LOOKUP_WORKERS` 调整为 1–8；批量过大时不建议设置过高。
- 输出仅供研究，不替代 NYC 主管部门出具的法律占用证明。
