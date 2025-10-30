# ZotWatcher

ZotWatcher 是一个基于 Zotero 数据构建个人兴趣画像，并持续监测学术信息源的新文献推荐流程。它每日在 GitHub Actions 上运行，将最新候选文章生成 RSS/HTML 报告，必要时也可在本地手动执行。

## 功能概览
- **Zotero 同步**：通过 Zotero Web API 获取文库条目，增量更新本地画像。
- **画像构建**：对条目向量化，提取高频作者/期刊，并记录近期热门期刊。
- **候选抓取**：拉取 Crossref、arXiv、bioRxiv/medRxiv（可选）等数据源，并对热门期刊做额外精准抓取。
- **去重打分**：结合语义相似度、时间衰减、引用/Altmetric、SJR 期刊指标及白名单加分生成推荐列表。
- **输出发布**：生成 `reports/feed.xml` 供 RSS 订阅，并通过 GitHub Pages 发布；同样可生成 HTML 报告或推送回 Zotero。

## 快速开始
1. Fork 或在 GitHub 上新建一个空仓库，将本项目的代码推送到自己的仓库。
2. 在 GitHub 仓库的 Settings → Secrets and variables → Actions 中添加以下 Repository secrets：
   - `ZOTERO_API_KEY`，登录 Zotero 网站的[个人账户](https://www.zotero.org/settings/)后，在 Settings - Security - Applications 处点击 Create new private key，其中 Personal Library 给予 Allow library access，Default Group Permissions 给予 Read Only 权限后保存获得。本 API 用于获取 Zotero 数据库中现有的文章信息，用于生成兴趣画像。
   - `ZOTERO_USER_ID`，该 ID 可从上述 Settings - Security - Applications 处 Create new private key 按钮下方一行 `User ID: Your user ID for use in API calls is ******` 获取。
   - （可选）`ALTMETRIC_KEY`，暂时未调试 ALTMETRIC 来源的热门文章信息。
   - （可选）`OPENALEX_MAILTO`、`CROSSREF_MAILTO`，用于部分网站 API 请求时需要。

3. 确保 `.github/workflows/daily_watch.yml` 中根据需求调整 `python -m src.cli watch` 的参数（例如 `--top`、`--report`）。
4. 推送代码到仓库后，GitHub Actions 会每日 UTC 06:00 自动运行，并在仓库的 Reports 中生成 RSS/HTML。若需立即运行，可在 Actions 页面手动触发 `Daily Watch & RSS` 工作流。可能需要在仓库页面的 Setting - Pages 中设置 Build and deployment 来源为 GitHub Actions。
5. 生成的 GitHub Pages 地址需要在末尾加上`/feed.xml`，该地址可以导入 Zotero 的 RSS 订阅，或者直接导入 RSS 阅读器。


## GitHub Actions 部署
1. **初始化 Git 仓库**
   ```bash
   git init
   git add .
   git commit -m "Initial ZotWatcher setup"
   git branch -M main
   git remote add origin <your-github-repo-url>
   git push -u origin main
   ```

2. **配置 Secrets**（仓库 Settings → Secrets and variables → Actions）
   - `ZOTERO_API_KEY`：您的 Zotero API 密钥
   - `ZOTERO_USER_ID`：您的 Zotero 用户 ID
   - （可选）`ALTMETRIC_KEY`、`OPENALEX_MAILTO`、`CROSSREF_MAILTO`

3. **启用 GitHub Pages**
   - Settings → Pages → Source 选择 "GitHub Actions"

Workflow 文件 `.github/workflows/daily_watch.yml` 中的关键命令：
```yaml
- run: python -m src.cli watch --rss --report --top 100
```
可根据需求添加或修改参数。

Workflow 的触发条件：
- 每天 **UTC 06:00** 定时运行（北京时间 14:00）
- 当 `main` 分支有新的 push
- 手动 `workflow_dispatch`

> 注：流水线会使用 GitHub Actions 缓存保存 `data/profile.sqlite` / `data/faiss.index` / `data/profile.json`。缓存键按年月 (`YYYYMM`) 生成，首次命中前或跨月后会自动执行 `python -m src.cli profile --full` 重新构建画像。

## 本地运行
1. **克隆仓库并准备环境**
   ```bash
   git clone <your-repo-url>
   cd ZotWatcher
   
   # 使用 conda/mamba
   conda create -n ZotWatcher python=3.10
   conda activate ZotWatcher
   pip install -r requirements.txt
   
   # 或使用 venv
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **配置环境变量**
   在仓库根目录创建 `.env` 文件（参考 `.env.example`），至少包含：
   ```
   ZOTERO_API_KEY=your_api_key
   ZOTERO_USER_ID=your_user_id
   ```

3. **本地运行**
   ```bash
   # 首次全量画像构建
   python -m src.cli profile --full

   # 日常监测（生成 RSS + HTML）
   python -m src.cli watch --rss --report --top 50
   ```

## 目录结构
```
ZotWatcher/
├─ .github/
│  └─ workflows/
│     └─ daily_watch.yml    # GitHub Actions 工作流
├─ src/
│  ├─ __init__.py
│  ├─ cli.py                # 命令行接口
│  ├─ profile.py            # 用户画像构建
│  ├─ watcher.py            # 文献监测
│  └─ utils.py              # 工具函数
├─ config/
│  ├─ zotero.yaml           # Zotero API 配置
│  ├─ sources.yaml          # 数据源配置
│  └─ scoring.yaml          # 评分权重配置
├─ data/                    # 画像数据（不纳入版本控制）
│  ├─ profile.sqlite        # 用户画像数据库
│  ├─ faiss.index           # 向量索引
│  ├─ profile.json          # 画像统计信息
│  └─ cache/                # 候选文章缓存
├─ reports/                 # 生成的报告（不纳入版本控制）
│  ├─ feed.xml              # RSS feed
│  └─ index.html            # HTML 报告
├─ .env.example             # 环境变量示例
├─ .gitignore
├─ requirements.txt         # Python 依赖
├─ README.md
└─ LICENSE
```

## 自定义配置
- `config/zotero.yaml`：Zotero API 参数（`user_id` 可写 `${ZOTERO_USER_ID}`，将由 `.env`/Secrets 注入）。
- `config/sources.yaml`：各数据源开关、分类、窗口大小（默认 7 天）。
- `config/scoring.yaml`：相似度、期刊质量等权重；并提供手动白名单支持。

## 常见问题
- **缓存过旧**：候选列表默认缓存 12 小时，可删除 `data/cache/candidate_cache.json` 强制刷新。
- **未找到热门期刊补抓**：确保已运行过 `profile --full` 生成 `data/profile.json`。
- **推荐为空**：检查是否所有候选都超出 7 天窗口或预印本比例被限制；可调节 CLI 的 `--top`、`_filter_recent` 的天数或 `max_ratio`。
- **导入错误**：确保使用 `python -m src.cli` 而非直接运行 `python src/cli.py`。

## 许可证
本项目采用 [MIT License](LICENSE)。
