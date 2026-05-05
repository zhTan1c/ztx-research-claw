# 📋 工作记录 (Work Record)

## 2026-05-02 — 核心代码实现

### 完成内容

从零完成全部 7 个 Agent + main.py + models.py 的编写与测试，共 4422 行代码。

### 详细过程

#### 1. models.py（277 行）
- 设计 10 个 dataclass 作为 Agent 间的数据契约
- 包含：Paper, Chapter, Section, SearchResult, ReadingNotes, MethodEntry, MethodAnalysis, Citation, ChapterDraft, AgentCheckpoint
- 所有 dataclass 支持 to_dict()/from_dict() 序列化
- Citation 支持 to_bibtex() 直接输出 BibTeX 格式
- AgentCheckpoint 支持 to_json()/from_json()/save()/load() 完整持久化

#### 2. agents/__init__.py（22 行）
- 统一导出 7 个 Agent 类

#### 3. agents/outline_parser.py（249 行）
- 纯解析，无 LLM 调用
- 正则匹配 Markdown heading 结构
- 实测：解析 outline.md 得到 7 章 25 个子节

#### 4. agents/literature_searcher.py（612 行）
- 异步实现，三源并行检索（Semantic Scholar / OpenAlex / arXiv）
- 跨源去重（SequenceMatcher 阈值 0.85）
- 加权评分 + 年份加权引用过滤
- httpx.AsyncClient + asyncio.Semaphore 控流
- tenacity 重试机制

#### 5. agents/pdf_downloader.py（303 行）
- 异步实现，并发下载（默认 5 并发）
- OA 链接级联：S2 → arXiv → Unpaywall
- 下载后验证 %PDF 文件头 + 最小体积
- 同步生成 preliminary_bib 供 citation_formatter 使用

#### 6. agents/paper_reader.py（963 行）
- MiMo V2.5 Pro 调用，三种阅读模式
- 全文模式：pdfplumber 提取 → 8000 字分块 → 逐块 LLM → 合并
- 输出结构化 JSON → ReadingNotes

#### 7. agents/methodology_analyst.py（402 行）
- DeepSeek V4 Pro 调用
- 输入：全部 ReadingNotes
- 输出：方法分类图谱 + 演进链 + 章节映射
- 大输入自动截断至 60K 字符

#### 8. agents/writer.py（678 行）
- DeepSeek V4 Pro 调用
- 按 pipeline 顺序逐章写作
- [cite:paper_id] 占位符标记引用位置
- polish 终稿润色阶段（reasoning_effort=max）

#### 9. agents/citation_formatter.py（524 行）
- 全局引用编号（跨章节一致）
- 优先使用 preliminary_bib，缺失时从 Paper 字段构造
- 可选 LLM 校验/补全 BibTeX 字段

#### 10. main.py（392 行）
- 7 阶段流水线编排
- config.yaml 加载 + ${VAR} 环境变量解析
- pickle 宏观 checkpoint + --resume 断点续跑
- 异步支持（literature_searcher, pdf_downloader）
- 进度条 + 优雅退出（Ctrl+C）

### 自检与测试

- 10 个 Python 文件语法检查：全部通过 ✓
- models.py 序列化/反序列化测试：全部通过 ✓
- 7 个 Agent 实例化测试：全部通过 ✓
- OutlineParser.run() 实测：7 章 25 子节 ✓
- async/sync 方法检查：符合设计 ✓
- config 加载 + 环境变量解析：通过 ✓
- 类型不匹配修复（Path vs str）：已修复 ✓

### 发现并修复的问题

1. **main.py 中 pdf_dir 和 bib_path 类型不匹配** — Agent 方法期望 str，main.py 传的是 Path 对象。已修复。
2. **outline.md 第 3.3 节格式问题** — 3.3 和 3.3.1 写在同一行，用户已自行修复。
3. **requirements.txt 重复依赖** — python-dotenv 出现两次，用户已自行修复。
4. **config.yaml 中 Unpaywall/api_key 字段** — Unpaywall 不需要 key，只需 email，用户已自行修复。

### 依赖安装

```bash
pip install openai pyyaml pdfplumber httpx aiohttp aiofiles tenacity python-dotenv
```


## 2026-05-05 — 代码审查与问题修复

### 完成内容

对项目进行全面审查，修复代理支持、checkpoint 解析、搜索相关性等多个问题。

### 详细过程

#### 1. 代理支持修复（6 个文件，8 处改动）

**问题：** httpx 和 OpenAI SDK 默认不读取系统代理环境变量，导致在国内网络环境下无法访问学术 API。

**修改内容：**
- `agents/literature_searcher.py`：3 处 httpx.AsyncClient 添加 `trust_env=True`
- `agents/pdf_downloader.py`：2 处 httpx.AsyncClient 添加 `trust_env=True`
- `agents/paper_reader.py`：添加 httpx 导入，创建带 `trust_env=True` 的 http_client 传给 OpenAI
- `agents/methodology_analyst.py`：同上
- `agents/writer.py`：同上
- `agents/citation_formatter.py`：同上

#### 2. Checkpoint 空文件解析修复

**问题：** `outputs/checkpoints/` 下的空 JSON 文件导致 `json.loads("")` 报错。

**修改内容：**
- `models.py` 第 252-258 行：`AgentCheckpoint.load()` 方法添加空文件检查，空文件返回默认实例。

#### 3. SOCKS5 代理依赖

**问题：** 系统环境变量包含 `ALL_PROXY=socks5://...`，httpx 需要 `socksio` 包。

**解决方案：** `pip install httpx[socks]` 或 `pip install socksio`

#### 4. arXiv API URL 修复

**问题：** config.yaml 中 arXiv 使用 `http://` 导致 301 重定向。

**修改内容：** `config.yaml` 中 `http://export.arxiv.org` → `https://export.arxiv.org`

#### 5. OpenAlex filter_topics 修复

**问题：** filter_topics 使用概念名称而非概念 ID，导致 400 Bad Request。

**修改内容：** 清空 filter_topics 列表，避免无效过滤。

#### 6. 搜索相关性修复

**问题：** 用中文章节标题搜索英文论文库，返回大量不相关论文（如英语教学、基因表达等）。

**修改内容：**
- `config.yaml`：更新 keywords 为更精确的英文关键词
- `agents/literature_searcher.py`：
  - 重写 `_generate_queries()` 方法，强制使用英文主题关键词
  - 新增 `_is_english_query()` 方法检测查询语言
  - 新增 `_filter_by_relevance()` 方法过滤不相关论文
  - 在 `run()` 方法中调用相关性过滤

#### 7. .gitignore 更新

**新增忽略项：**
- `outputs/papers/` — 下载的论文 PDF
- `outputs/drafts/` — 生成的草稿
- `outputs/checkpoints/` — checkpoint 文件
- `outputs/references.bib` — 生成的参考文献
- `.conda/` / `miniconda3/` — conda 环境

### 依赖安装

```bash
pip install httpx[socks]  # 新增：SOCKS5 代理支持
```

---