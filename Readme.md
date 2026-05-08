# 🎯 ztx-research-claw 架构设计
```plain
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ztx-research-claw 完整 Agent 工作流                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Phase 1: 解析综述框架 (outline_parser)                              │   │
│  │  [MiMo V2.5 Pro]                                                    │   │
│  │    └── 读取 data/outline.md                                          │   │
│  │    └── 提取 7 章结构、每章主题、方法类别标签、预期引用类型               │   │
│  │    └── ▶ Checkpoint: outputs/checkpoints/outline_parser.json         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Phase 2: 智能文献检索 (literature_searcher)                         │   │
│  │  [MiMo V2.5 Pro]                                                    │   │
│  │    └── 解析种子论文 (data/paper_example.md → Paper 列表)             │   │
│  │    └── S2 enrichment: 补全种子元数据 (abstract, citations, authors)   │   │
│  │    └── Citation expansion: 种子的 references + citations             │   │
│  │    └── LLM 推荐论文: 根据每章提示词推荐应引用的论文标题               │   │
│  │    └── 精确搜索: 用论文标题 → Semantic Scholar / arXiv               │   │
│  │    └── 去重 + PDF 链接修正 (优先 arXiv 直链)                         │   │
│  │    └── 硬件过滤 + 综述过滤 + 相关性评分 → Top 500                    │   │
│  │    └── ▶ Checkpoint: outputs/checkpoints/literature_searcher.json    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Phase 3: PDF 下载与引用采集 (pdf_downloader)                        │   │
│  │  [MiMo V2.5 Pro]                                                    │   │
│  │    └── 优先级: S2 OA链接 → arXiv直链 → Unpaywall → arXiv标题搜索     │   │
│  │    └── 下载 PDF 至 outputs/papers/                                   │   │
│  │    └── 同步提取引用元数据 → preliminary_bib (供 citation_formatter)   │   │
│  │    └── 下载失败 → 生成 failed_downloads.md + 暂停等待人工下载         │   │
│  │    └── ▶ Checkpoint: outputs/checkpoints/pdf_downloader.json         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Phase 4: 深度阅读 (paper_reader)                                    │   │
│  │  [MiMo V2.5 Pro | 1M 上下文]                                         │   │
│  │    └── 模式1: 摘要初筛 (abstract_only)                               │   │
│  │    └── 模式2: 全文深读 (fulltext_deep)                               │   │
│  │              提取: intro / related_work / methodology / experiments   │   │
│  │    └── 模式3: 跨文献对比 (cross_paper_compare)                       │   │
│  │              一次塞入多篇，做方法、指标、结论横向对比                    │   │
│  │    └── ▶ Checkpoint: outputs/checkpoints/paper_reader.json           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Phase 5: 方法演进分析 (methodology_analyst)                         │   │
│  │  [DeepSeek V4 Pro | Think High]                                      │   │
│  │    └── 输入: paper_reader 的跨文献对比笔记                             │   │
│  │    └── 任务: 方法分类 → 演进脉络梳理 → 技术路线图谱生成                 │   │
│  │    └── 输出: taxonomy + evolution_chain (供 writer & citation)       │   │
│  │    └── ▶ Checkpoint: outputs/checkpoints/methodology_analyst.json    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Phase 6: 章节写作 (writer)                                          │   │
│  │  [DeepSeek V4 Pro | Think High/Max]                                  │   │
│  │    └── 输入骨架: outline_parser (7章结构)                            │   │
│  │    └── 输入素材: paper_reader (阅读笔记)                             │   │
│  │    └── 输入逻辑: methodology_analyst (演进分析)                      │   │
│  │    └── 按 prompts/ 目录提示词逐章生成:                                │   │
│  │        ├─ 1. introduction                                            │   │
│  │        ├─ 2. non_rl_method                                           │   │
│  │        ├─ 3. deep_rl_method                                          │   │
│  │        ├─ 4. mixed_and_SOTA_method                                   │   │
│  │        ├─ 5. experiment_and_performance                              │   │
│  │        ├─ 6. challenges_and_trends                                   │   │
│  │        └─ 7. conclusion                                              │   │
│  │    └── 输出至: outputs/drafts/*.md (初稿，引用处留占位符 [cite:X])     │   │
│  │    └── ▶ Checkpoint: outputs/checkpoints/writer.json                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Phase 7: 引用格式化与终稿整合 (citation_formatter)                  │   │
│  │  [DeepSeek V4 Pro | Think High]                                      │   │
│  │    └── 输入: writer 初稿 (含 [cite:X] 占位符)                        │   │
│  │    └── 输入: pdf_downloader 的 preliminary_bib                       │   │
│  │    └── 任务:                                                         │   │
│  │        ├─ 在占位符处插入正确 \cite{key}                              │   │
│  │        ├─ 去重、补全 DOI/URL/Venue                                  │   │
│  │        ├─ 生成统一 outputs/references.bib                            │   │
│  │        └─ 输出终稿 outputs/drafts/final_polished.md                  │   │
│  │    └── ▶ Checkpoint: outputs/checkpoints/citation_formatter.json     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  模型分工速查                                                                │
│  ├─ MiMo V2.5 Pro  (Agentic / 1M上下文 / 多步工具调用)                      │
│  │   └─ outline_parser → literature_searcher → pdf_downloader → paper_reader│
│  └─ DeepSeek V4 Pro (深度推理 / 结构化写作 / Think High~Max)                │
│      └─ methodology_analyst → writer → citation_formatter                  │
│                                                                             │
│  文献检索策略:                                                                │
│  ├─ 种子论文: seed_paper_parser → S2 enrichment → Citation expansion        │
│  └─ LLM 推荐: 根据章节提示词 → LLM 推荐论文标题 → 精确搜索                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  断点恢复: 任意 Phase 崩溃后，读取对应 checkpoint/*.json，跳过已完成阶段      │
└─────────────────────────────────────────────────────────────────────────────┘
```
# 📁 项目结构
```plain
ztx-research-claw/
├── .env                          # API Key 集中管理（已加入 .gitignore）
├── .env.example                  # 环境变量模板
├── .gitignore                    # 忽略 .env、outputs/、__pycache__ 等
├── config.yaml
├── main.py
├── models.py                     # 数据结构定义（Paper, Chapter, SearchResult 等）
├── requirements.txt
├── agents/
│   ├── __init__.py
│   ├── outline_parser.py         # 纯解析，无 LLM 调用
│   ├── seed_paper_parser.py      # 解析用户手写种子论文 (data/paper_example.md)
│   ├── literature_searcher.py    # LLM 推荐论文 + 三源检索 + 种子注入 + 引用扩展
│   ├── pdf_downloader.py         # 下载论文 + 记录引用格式（生成初步引用库）
│   ├── paper_reader.py           # 长文本阅读、提取关键信息
│   ├── methodology_analyst.py    # 分析相似论文方法间的演进逻辑
│   ├── citation_formatter.py     # 在合适位置插入引用、统一 .bib
│   └── writer.py                 # 按章节填写内容（7章）
├── prompts/
│   ├── system_prompt.txt
│   ├── introduction.txt
│   ├── non_rl_method.txt
│   ├── deep_rl_method.txt
│   ├── mixed_and_SOTA_method.txt
│   ├── experiment_and_performance.txt
│   ├── challenges_and_trends.txt
│   └── conclusion.txt
├── outputs/
│   ├── checkpoints/              # 7 个 agent 对应 7 个断点
│   ├── papers/                   # 下载的 PDF
│   ├── drafts/                   # 各章节草稿 + 终稿
│   ├── references.bib            # citation_formatter 统一维护
│   ├── failed_downloads.md       # 需要人工下载的论文清单
│   └── failed_references.bib     # 未下载论文的 BibTeX
└── data/
    ├── outline.md                # 综述框架
    └── paper_example.md          # 种子论文（用户手写）
```
# 🔧 核心代码实现

## 代码概览

全管线共 **4700+ 行** Python 代码，分布在 11 个文件中：

```plain
文件                            行数    职责
──────────────────────────────────────────────────────
models.py                       277    10 个 dataclass：Agent 间的数据契约
main.py                         400    7 阶段流水线编排 + checkpoint + --resume + 下载失败暂停
agents/__init__.py               23    统一导出 8 个模块
agents/outline_parser.py        249    纯解析，无 LLM 调用
agents/seed_paper_parser.py     140    解析用户手写种子论文 → Paper 列表
agents/literature_searcher.py   730    LLM 推荐 + 三源检索 + 种子注入 + citation expansion + 硬件过滤
agents/pdf_downloader.py        420    异步下载 + OA 链接级联 + arXiv 标题搜索兜底 + 失败报告
agents/paper_reader.py         1060    LLM 阅读（3 种模式 + 精读分级）
agents/methodology_analyst.py   402    LLM 方法分类 + 演进图谱生成
agents/writer.py                678    LLM 逐章写作 + [cite:X] 占位 + 终稿润色
agents/citation_formatter.py    524    全局引用编号 + BibTeX 生成 + LLM 校验
```

## 数据流与模型分工

```plain
                    MiMo V2.5 Pro                          DeepSeek V4 Pro
                    ─────────────                          ───────────────
Phase 1  outline_parser.run()
              → list[Chapter]
                                \
Phase 2  literature_searcher.run(chapters, seed_file)  [async]
              ├── 解析种子论文 (data/paper_example.md → Paper 列表)
              ├── S2 enrichment (补全种子元数据)
              ├── Citation expansion (种子的 references + citations)
              ├── LLM 推荐论文 (根据每章提示词推荐论文标题，排除硬件类)
              ├── 精确搜索 (论文标题 → Semantic Scholar / arXiv)
              ├── 去重 + arXiv PDF 链接修正
              ├── 硬件过滤 (排除传感器/执行器等硬件论文)
              └── 相关性过滤 + 评分 → list[Paper]  \
                                                    \
Phase 3  pdf_downloader.run(papers, pdf_dir)  [async]
              → list[Paper]  (含 local_path + preliminary_bib)
                    |
Phase 4  paper_reader.run(papers, mode)
              → list[ReadingNotes]
                    |
                    └──────────────────────→ Phase 5  methodology_analyst.run(notes)
                                                  → MethodAnalysis
                                                       |
                                                       ▼
                                              Phase 6  writer.run(chapters, notes, analysis)
                                                  → list[ChapterDraft]  (含 [cite:X] 占位符)
                                                       |
                                                       ▼
                                              Phase 7  citation_formatter.run(drafts, papers)
                                                  → (list[ChapterDraft], references.bib)
```

## models.py — 数据契约

所有 Agent 之间不传裸 dict，全部使用 dataclass：

| 数据类 | 用途 | 关键字段 |
|--------|------|----------|
| `Paper` | 一篇论文的完整元数据 | paper_id, title, abstract, year, citation_count, pdf_url, local_path |
| `Chapter` | 大纲中的一个章节 | chapter_id, chapter_num, sections, prompt_file, target_citations |
| `Section` | 章节下的子节 | section_id, title, level |
| `SearchResult` | 单源检索结果 | source, query, papers, total_found |
| `ReadingNotes` | 论文阅读笔记 | key_contributions, methodology_summary, experimental_results, chapter_tags |
| `MethodAnalysis` | 方法演进分析 | taxonomy (MethodEntry 列表), evolution_chains, chapter_mapping |
| `ChapterDraft` | 章节草稿 | chapter_id, content (含 [cite:X]), citations, word_count |
| `Citation` | BibTeX 条目 | key, title, authors, year, doi, to_bibtex() |
| `AgentCheckpoint` | 断点续跑 | agent_name, status, progress, to_json()/from_json(), save()/load() |

## 各 Agent 实现要点

### outline_parser（纯解析，无 LLM）
- 用正则匹配 Markdown heading：`# 第N章` → chapter，`## N.M` → section，`### N.M.K` → subsection
- 自动映射 chapter_num → chapter_id（1→introduction, ..., 7→conclusion）
- 为每个 chapter 关联 prompt_file 和 target_citations

### seed_paper_parser（纯解析，无 LLM）
- 解析用户手写的 `data/paper_example.md`，提取已填充的种子论文（跳过【待补充】条目）
- 从 URL 中提取 arXiv ID / DOI / IEEE document ID
- 从「详细」字段猜测年份和会议/期刊名
- 输出 Paper 对象列表，source 标记为 "seed"
- 种子论文是用户精心筛选的"奠基性"论文，质量高于自动检索结果

### literature_searcher（异步，LLM 推荐 + 三源检索 + 种子注入 + 引用扩展 + 硬件过滤）
- **LLM 推荐论文**：读取每章提示词文件，调用 MiMo V2.5 Pro 推荐该章应引用的论文标题
- **硬件过滤**：根据 config 中的 `hardware_filter` 配置，排除传感器设计、执行器制造等硬件类论文
- **综述过滤**：根据每章 prompt 中的引用要求，限制综述类论文数量
- **种子注入**：解析 paper_example.md → 种子 Paper → S2 API 补全元数据（abstract, citations, authors）
- **Citation expansion**：从种子出发，获取每篇种子的 references + citations，沿时间轴发现相关工作
- Semantic Scholar：GET /paper/search，fieldsOfStudy 过滤
- OpenAlex：GET /works，从 inverted index 重建 abstract
- arXiv：Atom XML 解析，xml.etree.ElementTree
- 去重：difflib.SequenceMatcher，阈值 0.85
- **PDF 链接修正**：去重后强制将有 arxiv_id 的论文 pdf_url 覆盖为 arXiv 直链
- 加权评分：0.3×引用数 + 0.2×时效性 + 0.5×摘要相关度
- 年份加权：>3 年引用阈值=5，≤3 年降为 2
- 全部用 httpx.AsyncClient(trust_env=True) + asyncio.Semaphore 控流
- S2 API 调用带 tenacity 重试（3 次）+ 429 限流处理

### pdf_downloader（异步，级联下载）
- 优先级：S2 OA 链接 → arXiv 直链 → Unpaywall API → arXiv 标题搜索兜底
- **arXiv 标题搜索**：当所有源都拿不到 PDF 时，用标题精确搜索 arXiv API
- 下载时验证 %PDF 文件头 + 最小体积
- 同步从 Paper 字段生成 preliminary_bib 供 citation_formatter 使用
- httpx.AsyncClient(trust_env=True) 支持系统代理
- **下载失败处理**：生成 `outputs/failed_downloads.md`（论文信息 + 下载链接）和 `outputs/failed_references.bib`（BibTeX），暂停管线等待人工下载

### paper_reader（LLM，MiMo V2.5 Pro）
- 三种阅读模式：abstract_only / fulltext_deep / cross_paper_compare
- **精读/粗读分级**（tiered 模式，可配置）：
  - 综述类：年份 > 2023 且被引 >= 200 → 精读，否则摘要
  - 非综述类：种子论文 → 精读 | 有开源代码 → 精读
  - 老论文(<=2023) 被引 >= 50 → 精读 | 近两年(2024-2025) 被引 >= 25 → 精读
  - 2025年及以后 → 精读 | 其余 → 摘要
- 全文模式：pdfplumber 提取文本 → 8000 字分块 → 逐块 LLM 提取 → 合并
- 输出结构化 JSON → 解析为 ReadingNotes
- OpenAI 客户端使用 httpx.Client(trust_env=True) 支持系统代理

### methodology_analyst（LLM，DeepSeek V4 Pro）
- 输入：全部 ReadingNotes
- 输出：方法分类图谱 (taxonomy) + 演进链 (evolution_chains) + 章节映射
- 大输入自动截断至 60K 字符
- OpenAI 客户端使用 httpx.Client(trust_env=True) 支持系统代理

### writer（LLM，DeepSeek V4 Pro）
- 按 pipeline 顺序逐章写作：introduction → non_rl_method → ... → conclusion
- 每章组装上下文：system_prompt + chapter_prompt + 相关 reading_notes + method_analysis
- 输出中用 [cite:paper_id] 标记引用位置
- 全部章节写完后执行 polish 终稿润色（reasoning_effort=max）
- OpenAI 客户端使用 httpx.Client(trust_env=True) 支持系统代理

### citation_formatter（LLM + 规则）
- 全局扫描所有 draft 中的 [cite:XXX]，按首次出现顺序分配 [1], [2], ...
- 引用编号跨章节一致（paper X 在第 1 章是 [1]，在第 3 章仍然是 [1]）
- 优先使用 pdf_downloader 的 preliminary_bib，缺失时从 Paper 字段构造
- 可选：用 DeepSeek 校验/补全 BibTeX 的 DOI、URL、Venue 字段
- OpenAI 客户端使用 httpx.Client(trust_env=True) 支持系统代理

# 🚀 启动方法

## 前置准备

```bash
# 1. 安装依赖
cd /path/to/ztx-research-claw
pip install -r requirements.txt
pip install httpx[socks]  # SOCKS5 代理支持（可选）

# 2. 创建 .env 文件（项目根目录）
cat > .env << 'EOF'
MIMO_API_KEY=你的MiMo_API_Key
DEEPSEEK_API_KEY=你的DeepSeek_API_Key
EOF

# 3. 确认 data/outline.md 已填写
# 4. 确认 data/paper_example.md 已填写（种子论文）
# 5. 确认 prompts/ 目录下的提示词文件完整
```

## 运行

```bash
# 从头运行完整管线
python main.py

# 断点续跑（上次中断后恢复）
python main.py --resume

# 指定配置文件
python main.py --config /path/to/config.yaml
```

## 网络代理

如果在国内网络环境，项目会自动读取系统代理环境变量（HTTP_PROXY / HTTPS_PROXY）。

```bash
# 设置代理（如果系统未自动配置）
export HTTP_PROXY=http://127.0.0.1:7897
export HTTPS_PROXY=http://127.0.0.1:7897

python main.py
```

## PDF 下载失败处理

某些学术网站（IEEE、Springer 等）会触发人机验证，脚本无法自动下载。Phase 3 完成后：
- 如果有未下载的论文，管线会自动暂停
- 生成 `outputs/failed_downloads.md`：包含每篇论文的标题、下载链接、BibTeX
- 生成 `outputs/failed_references.bib`：供 citation_formatter 使用
- 用户手动下载 PDF 后放入 `outputs/papers/` 目录
- 运行 `python main.py --resume` 继续后续流程

# ⚠️ 注意事项

## API 与网络

- **Semantic Scholar** 免费无需 Key，但有速率限制（~100 req/min）。大批量检索时会被限流，literature_searcher 内置了 tenacity 重试。
- **OpenAlex** 无需 Key，用 `mailto` 参数进入 polite pool（10 req/s）。config.yaml 里已配置。
- **Unpaywall** 无需 Key，只需在 URL 后加 `?email=xxx`。config 里的 `email` 字段会被拼入请求。
- **arXiv** 官方限流 3 req/s，代码中用 asyncio.Semaphore 控制。

## Checkpoint 机制

本项目有**两套独立的 checkpoint 系统**：

| 层级 | 格式 | 存储位置 | 用途 |
|------|------|----------|------|
| main.py 宏观 | pickle | outputs/checkpoint.pkl | 跳过已完成的 Phase |
| Agent 内部微观 | JSON | outputs/checkpoints/*.json | Agent 内部断点续跑（如 paper_reader 读到第 N 篇中断） |

`--resume` 控制的是 main.py 的宏观 checkpoint。Agent 内部的微观 checkpoint 由各 Agent 自行管理。

## LLM 调用

- **MiMo V2.5 Pro**（base_url: `https://token-plan-cn.xiaomimimo.com/v1`）：负责 outline_parser、literature_searcher、pdf_downloader、paper_reader
- **DeepSeek V4 Pro**（base_url: `https://api.deepseek.com`）：负责 methodology_analyst、writer、citation_formatter
- API Key 通过 `.env` 文件管理，config.yaml 中用 `${VAR}` 引用，不要硬编码
- writer 的终稿润色阶段使用 `reasoning_effort=max`，token 消耗较大，注意额度

## 输出文件

```plain
outputs/
├── papers/              # 下载的 PDF（按 sanitized_title 命名）
├── drafts/
│   ├── introduction.md          # 各章节独立草稿
│   ├── non_rl_method.md
│   ├── deep_rl_method.md
│   ├── mixed_and_SOTA_method.md
│   ├── experiment_and_performance.md
│   ├── challenges_and_trends.md
│   ├── conclusion.md
│   └── final_polished.md        # 全文合并终稿
├── references.bib               # 统一 BibTeX 引用库
├── failed_downloads.md          # 需要人工下载的论文清单（含下载链接）
└── failed_references.bib        # 未下载论文的 BibTeX（供引用）
```

# 💡 优化建议

## 提升检索质量

- 在 `data/paper_example.md` 中添加高质量种子论文，citation expansion 会自动发现相关工作
- `prompts/` 目录下的提示词越具体，LLM 推荐的论文越精准
- 如果检索结果噪声多，调高 `min_citation_count` 或降低 `max_total_papers`
- 硬件过滤可在 `config.yaml` 的 `agents.literature_searcher.hardware_filter` 中配置
- 综述类关键词可在 `config.yaml` 的 `agents.literature_searcher.survey_keywords` 中配置

## 提升写作质量

- `prompts/` 目录下的提示词是最核心的控制手段——越具体，LLM 输出越可控
- 每章提示词中已规定了核心论点和写法，如需调整直接编辑对应的 .txt 文件
- system_prompt.txt 中的全局写作规范（禁止套话、论点密度、引用格式）对所有章节生效

## Token 消耗控制

- paper_reader 的 fulltext_deep 模式消耗最大（每篇论文可能用 120K tokens），建议先用 abstract_only 模式初筛，再对高相关论文用 fulltext_deep
- methodology_analyst 的输入如果超过 60K 字符会自动截断，确保 ReadingNotes 足够精炼
- writer 的 polish 阶段用 max reasoning_effort，如果额度紧张可以改回 high
- literature_searcher 的 LLM 推荐功能会消耗 MiMo tokens，每章约 2K tokens
- 精读分级规则可在 `config.yaml` 的 `agents.paper_reader.tiered_reading` 中调整

## 自定义扩展

- 如需新增检索源，在 `agents/literature_searcher.py` 中添加 `_search_xxx()` 方法并在 config 中注册
- 如需新增 Agent，参照现有 Agent 的模式：`__init__(config)` + `run(...)` + `save_checkpoint()`/`load_checkpoint()`
- 数据结构扩展在 `models.py` 中添加即可，下游 Agent 自动可用
