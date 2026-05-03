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
