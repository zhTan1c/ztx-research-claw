# 📋 工作记录

## 2026-05-02 — 核心代码实现

完成 7 个 Agent + main.py + models.py，共 4400+ 行代码。

- `models.py`（277 行）：10 个 dataclass，Agent 间数据契约
- `agents/outline_parser.py`（249 行）：正则解析 outline.md → Chapter 列表
- `agents/literature_searcher.py`（612 行）：异步三源检索（S2/OpenAlex/arXiv）+ 去重 + 加权评分
- `agents/pdf_downloader.py`（303 行）：异步下载 + OA 链接级联 + BibTeX 提取
- `agents/paper_reader.py`（963 行）：MiMo LLM 阅读，3 种模式（摘要/全文/跨文献对比）
- `agents/methodology_analyst.py`（402 行）：DeepSeek LLM 方法分类 + 演进图谱
- `agents/writer.py`（678 行）：DeepSeek LLM 逐章写作 + [cite:X] 占位 + 终稿润色
- `agents/citation_formatter.py`（524 行）：全局引用编号 + BibTeX 生成
- `main.py`（392 行）：7 阶段流水线 + checkpoint + --resume

修复：Path/str 类型不匹配、outline.md 格式、requirements 重复依赖、Unpaywall 配置。

---

## 2026-05-05 — 代码审查与修复

- 6 个 Agent 添加 `trust_env=True` 代理支持
- models.py checkpoint 空文件解析修复
- arXiv API URL http→https 修复
- OpenAlex filter_topics 概念 ID 修复
- 搜索相关性：新增英文关键词过滤 + LLM 推荐论文标题搜索
- 文献检索逻辑重构：LLM 推荐论文标题 → 精确搜索
- .gitignore 更新

---

## 2026-05-06 — PDF 下载源修复 + 种子论文系统

### PDF 下载修复
- 问题：PDF 偏向 OpenAlex 出版商链接，arXiv 直链被丢弃
- 修复：`_enrich_pdf_links()` 去重后强制覆盖为 arXiv 直链；pdf_downloader 新增 `arxiv_search` 标题搜索兜底

### 种子论文系统
- 新增 `agents/seed_paper_parser.py`（140 行）：解析手写引用论文.md → Paper 列表
- literature_searcher 新增种子注入 + S2 enrichment + Citation expansion
- config 新增 `seed_papers_file` 配置

### 运行问题修复
- `max_results_per_query` 从 10 改回 100
- S2 enrichment 限流崩溃：try-except 包裹 + 1 秒间隔
- 种子论文解析器格式兼容（有无 [N] 前缀）
- Readme 架构图、代码概览、Agent 实现要点全面更新

---

## 2026-05-07 — 性能优化（搜索 + 去重 + 下载）

### 问题：Phase 2 搜索卡死

**根因分析**：不是卡住，是三重慢叠加——
1. S2 持续 429，每个查询重试浪费 3 秒
2. 429 计数器被偶发成功重置，永远触发不了跳过逻辑
3. 41,850 篇论文 O(n²) 去重 = 17.5 亿次比较

**修复**：
- S2 任何异常 → 立即跳过该源，后续只用 OpenAlex + arXiv
- S2 搜索重试从 3 次降到 1 次（不重试）
- 去重前预过滤：按引用数排序取 top 3000 再去重
- 每 5 个查询打印进度 + 保存 checkpoint

### 问题：Phase 3 PDF 下载 PoolTimeout

**根因**：5 个并发下载占满 httpx 连接池，后续请求等不到连接超时。

**修复**：
- httpx 连接池限制：`Limits(max_connections=10, max_keepalive_connections=5)`
- 并发数 5→2
- 下载超时 60s→30s

### 目录调整
- `paper_example.md` 移至 `data/` 目录

### S2 enrichment 重写
- 只试 arXiv ID（快速路径），不再逐策略重试
- 连续 5 次 429 自动放弃剩余种子
- 成功 enrichment 的种子标记 `source="seed_enriched"`
- Citation expansion 只对 enrichment 成功的种子展开（避免 arXiv ID 查 S2 返回 404）
- 120 秒全局超时兜底

---

## 2026-05-08 — literature_searcher 重构 + 精读分级 + 综述过滤

### literature_searcher.py 重构（1031 → 649 行）

- 搜索方法重命名为 `_search_s2` / `_search_oa` / `_search_ax`，解析逻辑提取为独立方法
- enrichment + citation expansion 合并精简
- 去掉冗余方法（`_dispatch_search`、`_is_english_query` 等）
- 后处理流水线简化为 5 步

### 综述过滤

- `_is_survey()` 通过标题关键词判断综述类论文
- `_parse_citation_requirement()` 从 prompt 文件自动解析引用要求（目标数 + 综述上限）
- LLM 查询时根据引用要求约束综述数量，非综述章节明确禁止推荐综述

### 代码检测（has_code）

- models.py 新增 `has_code: bool` + `code_url: str`
- 搜索解析时自动检测 abstract/URL 中的 GitHub/GitLab/HuggingFace 链接

### paper_reader 精读/粗读分级

```
综述类：year > 2023 且 citations > 200 → 精读，否则摘要
非综述类：种子论文 → 精读 | has_code → 精读
  year <= 2023 且 citations > 50 → 精读
  2024-2025 且 citations > 25 → 精读
  year >= 2025 → 精读 | 其余 → 摘要
```

### Bug 修复

- `_merge_chunk_notes` 中 LLM 返回 list 导致崩溃：新增 `_to_str()` 强制类型转换

---

## 2026-05-09 — 硬件过滤 + 精读配置化 + 下载失败处理

### config.yaml 新增约束

**文献检索约束（literature_searcher）：**
- `hardware_filter`：硬件过滤配置
  - `exclude_keywords`：22 个硬件类关键词（传感器设计、执行器制造、电子皮肤等）
  - `override_keywords`：7 个算法类关键词（grasp、manipulation、RL 等），避免误杀
- `survey_keywords`：综述类论文识别关键词（15 个），可配置覆盖

**精读分级配置（paper_reader）：**
- `tiered_reading`：精读/粗读分级规则
  - `survey`：综述类精读条件（citations >= 200 且 year > 2023）
  - `non_survey`：非综述类精读条件（种子论文、有代码、高被引、新论文等）

### literature_searcher.py 改动

- `_is_survey()` 支持自定义关键词列表参数
- `_generate_queries()` LLM prompt 新增硬件排除提示
- `_filter_surveys_and_score()` 使用 config 中的 survey_keywords
- 新增 `_filter_hardware()` 方法：按 exclude/override 关键词过滤硬件论文

### pdf_downloader.py 改动

**新增 `_generate_failed_report()` 方法：**
- 生成 `outputs/failed_downloads.md`：每篇失败论文的标题、ID、年份、引用数、下载链接、建议文件名
- 生成 `outputs/failed_references.bib`：失败论文的 BibTeX（供 citation_formatter 引用）
- 控制台显式提示用户手动下载

### main.py 改动

- Phase 3 完成后检查是否有未下载的论文
- 如果有，打印提示信息并 `sys.exit(0)`，暂停管线
- 用户手动下载后运行 `python main.py --resume` 继续

### 工作流程

```
Phase 2 完成 → Phase 3 下载 PDF
  ↓
检查：是否有未下载的论文？
  ├── 全部下载成功 → 继续 Phase 4
  └── 有失败论文 → 生成 failed_downloads.md + failed_references.bib
                    → 打印提示 → sys.exit(0)
                    → 用户手动下载 PDF 放入 outputs/papers/
                    → python main.py --resume → 继续 Phase 4
```

---

## 2026-05-09（续）— 检索质量优化

### 问题：检索到的论文与主题不相关

首轮运行后发现检索到的论文大多是大模型、NLP、综述类，与"柔性物质灵巧抓取"无关。

### 根因分析

LLM 推荐的论文经过 5 层过滤后大幅缩减：
```
22728 → Pre-filter(3000) → Dedup(1137) → Relevance(116) → Year(70) → Citation(70) → Hardware(68) → Final(68)
```
Relevance 过滤（正向关键词匹配）砍掉 90%，且正向词太宽泛（"reinforcement learning"匹配了游戏AI、NLP等不相关论文）。

### 修复

#### 1. Relevance 过滤：正向 + 反向联合（literature_searcher.py）

**正向词（~60 个，精准聚焦机器人操控）：**
- 核心抓取：grasp, gripper, in-hand, bimanual, pick-and-place...
- 柔性物体：deformable object, cloth, fabric, rope, folding, knot...
- 灵巧手：dexterous hand, allegro, shadow hand...
- 触觉：tactile grasping, gelsight, visuotactile...
- 仿真平台：isaac gym, softgym, robosuite...

**反向词（~60 个，排除非机器人领域）：**
- NLP/大模型：language model, llm, gpt, bert, nlp, machine translation...
- CV（非机器人）：image classification, autonomous driving, face recognition...
- 推荐/数据：recommendation system, stock prediction, anomaly detection...
- 医学：cancer, clinical, patient, drug, diagnosis...

**过滤逻辑：** 有反向词 → 排除；无正向词 → 排除；两者都通过 → 保留。

#### 2. LLM 查询生成优化（literature_searcher.py）

- 从 prompt 文件解析引用要求（`_parse_citation_requirement`）
- 支持格式：`引用要求：35～45篇，最多引用3篇综述类论文`
- 综述章节（introduction, challenges_and_trends, conclusion）允许推荐综述
- 非综述章节明确要求 LLM 不推荐综述

#### 3. 代码检测（models.py + literature_searcher.py）

- Paper 数据类新增 `has_code` 和 `code_url` 字段
- 搜索结果解析时检测摘要中的 GitHub/GitLab/HF 链接
- 用于 paper_reader 的精读/粗读决策

#### 4. 全局上限约束（literature_searcher.py）

- 按章节 prompt 中的 `target_citations` 计算总需求
- cap = min(总需求, max_total_papers)
- 评分排序后按 cap 截断
- 日志打印每章需求和总量

#### 5. polish reasoning_effort 降级（config.yaml）

- `polish: "max"` → `polish: "high"`
- 与写作阶段一致，减少 token 消耗

#### 6. Paper Reader 精读/粗读规则更新（paper_reader.py）

综述类论文：
- 年份 > 2023 且 被引 > 200 → 精读全文
- 其余 → 只读摘要

非综述类论文：
- 种子论文 → 精读全文
- 有开源项目 → 精读全文
- 年份 ≤ 2023 且 被引 > 50 → 精读全文
- 年份 2024-2025 且 被引 > 25 → 精读全文
- 年份 ≥ 2025 → 精读全文
- 其余 → 只读摘要

### 依赖安装

无新增依赖。

---

