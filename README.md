# 生物医药顶刊文献监控机器人

自动监控 PubMed 上国内外顶级生物医药期刊的最新文献，AI 智能汇总后通过飞书机器人推送。

## 功能

- 监测 **35+ 本**国内外顶级生物医药期刊（CNS、医学顶刊、Nature/Cell 子刊、国内顶刊）
- 基于 **PubMed E-utilities API**（完全免费，无需 API Key）
- 自动过滤新闻、社论等非研究论文
- AI 智能汇总（豆包大模型，未配置 Key 时降级为规则摘要）
- 飞书消息卡片推送（含 AI 汇总、文献列表、原文链接）
- 去重机制（基于 PMID）
- GitHub Actions 每日自动运行（北京时间早 8 点）

## 监测期刊列表

| 分组 | 期刊 |
|------|------|
| 综合顶刊 (CNS) | Nature, Science, Cell |
| 医学顶刊 | NEJM, Lancet, JAMA, BMJ |
| Nature 子刊 | Nature Medicine, Nature Biotechnology, Nature Genetics, Nature Biomedical Engineering, Nature Cell Biology, Nature Cancer, Nature Microbiology, Nature Immunology, Nature Neuroscience |
| Cell 子刊 | Cancer Cell, Immunity, Neuron, Cell Stem Cell, Molecular Cell, Cell Metabolism, Cell Host & Microbe |
| 其他重要期刊 | PNAS, Cell Research, Blood, Circulation, Genome Biology, Genome Research |
| 国内顶级期刊 | Science China Life Sciences, Journal of Molecular Cell Biology, Acta Pharmacologica Sinica, Chinese Medical Journal |

## 快速开始

### 本地运行

```bash
pip install requests
# 初始化（标记现有文献为已读，不推送）
python pubmed_monitor.py --init
# 运行一次
python pubmed_monitor.py --once
```

### 配置

编辑 `config.json`：
- `feishu_webhook`：飞书自定义机器人 Webhook 地址
- `ai_api_key`：火山引擎方舟 API Key（可选，不配置用规则摘要）
- `check_days`：监测最近几天的文献（默认 3 天）
- `journals`：监测的期刊列表（可自行增删）

### GitHub Actions 部署（推荐）

1. 创建 GitHub 仓库，上传所有文件
2. 在仓库 Settings → Secrets and variables → Actions 中添加：
   - `FEISHU_WEBHOOK`：飞书 Webhook 地址
   - `AI_API_KEY`：火山引擎 API Key（可选）
3. 首次运行先手动触发 `--init` 模式（或本地初始化后上传 seen_pmids.json）
4. 之后每天北京时间早 8 点自动运行

## 文件结构

```
biomed_literature_monitor/
├── .github/workflows/monitor.yml  # GitHub Actions 定时任务
├── pubmed_monitor.py               # 主程序
├── config.json                     # 配置文件（期刊列表、飞书、AI等）
├── requirements.txt                # Python 依赖
├── seen_pmids.json                 # 已推送文献记录（自动生成）
└── README.md                       # 本说明
```

## 推送效果

飞书收到紫色标题的消息卡片，包含：
- 🧬 标题：生物医药顶刊文献速递 | 日期
- 今日概览：监测期刊数、新文献数
- 📊 AI 智能汇总：总体概述 + 核心发现提炼
- 📚 文献列表：按期刊分组，标题可点击跳转 PubMed 原文
- 底部：数据来源说明
