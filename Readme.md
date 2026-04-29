# 🎯 ztx-research-claw 架构设计
```plain
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ztx-research-claw 工作流                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  Phase 1: 解析论文框架 (parse_outline)                                       │
│     └── 读取 outline.md → 提取章节结构、每章主题、所需引用类型                   │
│                                                                             │
│  Phase 2: 智能文献检索 (search_literature)                                   │
│     └── 根据章节主题 → Semantic Scholar / arXiv / CrossRef 多源检索          │
│     └── 相关性排序 → 筛选 top-N 候选文献                                     │
│                                                                             │
│  Phase 3: PDF 下载与解析 (download_and_parse)                                │
│     └── Unpaywall / arXiv / Sci-Hub 获取 PDF                                │
│     └── PyPDF2 / pdfplumber 提取文本内容                                     │
│                                                                             │
│  Phase 4: 深度阅读与写作 (read_and_write)                                    │
│     └── DeepSeek V4 Pro 1M 上下文阅读文献                                    │
│     └── 根据 prompts/ 目录下的章节提示词生成内容                               │
│                                                                             │
│  Phase 5: 引用格式化 (format_citations)                                      │
│     └── 生成 BibTeX / GB/T 7714 引用格式                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```
# 📁 项目结构
```plain
ztx-research-claw/
├── config.yaml              # API 配置、模型参数
├── main.py                  # 主入口
├── agents/
│   ├── __init__.py
│   ├── outline_parser.py    # 论文框架解析器
│   ├── literature_searcher.py  # 文献检索Agent
│   ├── pdf_downloader.py    # PDF下载器
│   ├── paper_reader.py      # 文献阅读Agent
│   └── writer.py            # 写作Agent
├── prompts/
│   ├── system_prompt.txt    # 系统级提示词
│   ├── introduction.txt     # 引言章节提示词
│   ├── related_work.txt     # 相关工作提示词
│   ├── methodology.txt      # 方法提示词
│   ├── experiments.txt      # 实验提示词
│   └── conclusion.txt       # 结论提示词
├── outputs/                 # 输出目录
│   ├── papers/              # 下载的PDF
│   ├── drafts/              # 生成的草稿
│   └── references.bib       # 引用库
├── data/
│   └── outline.md           # 你的论文框架
└── requirements.txt
```
# 🔧 核心代码实现
