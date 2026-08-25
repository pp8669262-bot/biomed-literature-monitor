#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生物医药顶刊文献监控机器人
功能：定时抓取 PubMed 顶级期刊最新文献 -> AI智能汇总 -> 飞书推送
"""

import json
import os
import sys
import time
import logging
import argparse
import base64
import hmac
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

# ============================================================
# 配置与日志
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PUBMED_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def load_config(config_path=None):
    """加载配置文件，环境变量优先"""
    if config_path is None:
        config_path = os.path.join(SCRIPT_DIR, "config.json")
    config = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

    env_map = {
        "FEISHU_WEBHOOK": "feishu_webhook",
        "FEISHU_SECRET": "feishu_secret",
        "AI_API_KEY": "ai_api_key",
        "AI_API_BASE": "ai_api_base",
        "AI_MODEL": "ai_model",
    }
    for env_key, config_key in env_map.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            config[config_key] = val
    return config


def setup_logging(log_file):
    """配置日志"""
    log_path = os.path.join(SCRIPT_DIR, log_file)
    logger = logging.getLogger("biomed_monitor")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# ============================================================
# 已推送记录
# ============================================================

def load_seen(seen_file):
    path = os.path.join(SCRIPT_DIR, seen_file)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen(seen_file, seen_data):
    path = os.path.join(SCRIPT_DIR, seen_file)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen_data, f, ensure_ascii=False, indent=2)


# ============================================================
# PubMed API
# ============================================================

def search_journal(journal_name, days, max_results, logger):
    """搜索特定期刊最近N天的文献，返回PMID列表"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    date_range = f"{start_date.strftime('%Y/%m/%d')}:{end_date.strftime('%Y/%m/%d')}[pdat]"

    term = f'"{journal_name}"[journal] AND {date_range}'
    url = f"{PUBMED_EUTILS}/esearch.fcgi"
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": max_results,
        "retmode": "json",
        "sort": "date",
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        pmids = data.get("esearchresult", {}).get("idlist", [])
        count = data.get("esearchresult", {}).get("count", "0")
        logger.info(f"  {journal_name}: 找到 {count} 篇，取前 {len(pmids)} 篇")
        return pmids
    except Exception as e:
        logger.error(f"  {journal_name}: 搜索失败 - {e}")
        return []


def fetch_articles(pmids, logger):
    """批量获取文献详细信息（标题、作者、摘要、DOI等）"""
    if not pmids:
        return []

    url = f"{PUBMED_EUTILS}/efetch.fcgi"
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "medline",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return parse_pubmed_xml(resp.text, logger)
    except Exception as e:
        logger.error(f"  获取文献详情失败: {e}")
        return []


def parse_pubmed_xml(xml_text, logger):
    """解析 PubMed XML，提取文献信息"""
    articles = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"  XML解析失败: {e}")
        return articles

    for article_elem in root.findall(".//PubmedArticle"):
        try:
            # PMID
            pmid_elem = article_elem.find(".//PMID")
            pmid = pmid_elem.text if pmid_elem is not None else ""

            # 标题
            title_elem = article_elem.find(".//ArticleTitle")
            title = "".join(title_elem.itertext()).strip() if title_elem is not None else ""

            # 期刊
            journal_elem = article_elem.find(".//Journal/Title")
            journal = journal_elem.text.strip() if journal_elem is not None else ""
            journal_abbr_elem = article_elem.find(".//Journal/ISOAbbreviation")
            journal_abbr = journal_abbr_elem.text.strip() if journal_abbr_elem is not None else journal

            # 出版日期
            pub_date = ""
            year_elem = article_elem.find(".//PubDate/Year")
            month_elem = article_elem.find(".//PubDate/Month")
            day_elem = article_elem.find(".//PubDate/MedlineDate")
            if year_elem is not None:
                pub_date = year_elem.text
                if month_elem is not None:
                    pub_date += f" {month_elem.text}"
            elif day_elem is not None:
                pub_date = day_elem.text

            # 作者
            authors = []
            for author_elem in article_elem.findall(".//AuthorList/Author"):
                last = author_elem.find("LastName")
                fore = author_elem.find("ForeName")
                if last is not None and fore is not None:
                    authors.append(f"{fore.text} {last.text}")
                elif last is not None:
                    authors.append(last.text)
                if len(authors) >= 3:
                    break

            # 摘要
            abstract_parts = []
            for abs_elem in article_elem.findall(".//Abstract/AbstractText"):
                label = abs_elem.get("Label", "")
                text = "".join(abs_elem.itertext()).strip()
                if label:
                    abstract_parts.append(f"{label}: {text}")
                else:
                    abstract_parts.append(text)
            abstract = " ".join(abstract_parts)

            # DOI
            doi = ""
            for eloc in article_elem.findall(".//ELocationID"):
                if eloc.get("EIdType") == "doi":
                    doi = eloc.text
                    break

            # 文章类型（过滤新闻/社论等）
            pub_types = []
            for pt in article_elem.findall(".//PublicationTypeList/PublicationType"):
                pub_types.append(pt.text if pt.text else "")

            if pmid and title:
                articles.append({
                    "pmid": pmid,
                    "title": title,
                    "journal": journal,
                    "journal_abbr": journal_abbr,
                    "pub_date": pub_date,
                    "authors": authors,
                    "abstract": abstract,
                    "doi": doi,
                    "pub_types": pub_types,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                })
        except Exception as e:
            logger.warning(f"  解析单篇文献失败: {e}")
            continue

    return articles


def is_research_article(article):
    """判断是否为研究论文（过滤新闻、社论、信件等）"""
    non_research = {"News", "Editorial", "Letter", "Comment", "Biography",
                     "Autobiography", "Portraits", "Newspaper Article",
                     "Historical Article", "Practice Guideline", "Lecture",
                     "Personal Narrative", "Video-Audio Media"}
    pub_types = set(article.get("pub_types", []))
    # 如果所有类型都是非研究类，过滤掉
    if pub_types and pub_types.issubset(non_research):
        return False
    return True


# ============================================================
# AI 智能汇总
# ============================================================

def ai_summarize(articles_by_group, config, logger):
    """用AI对文献进行智能汇总"""
    api_key = config.get("ai_api_key", "").strip()
    if not api_key:
        return rule_based_summary(articles_by_group)

    try:
        # 构建输入文本
        input_parts = []
        for group, articles in articles_by_group.items():
            if not articles:
                continue
            input_parts.append(f"【{group}】")
            for i, art in enumerate(articles, 1):
                authors = ", ".join(art["authors"][:2])
                if len(art["authors"]) > 2:
                    authors += " et al."
                abs_text = art["abstract"][:500] if art["abstract"] else "无摘要"
                input_parts.append(f"{i}. 《{art['title']}》\n   期刊: {art['journal_abbr']} | 作者: {authors}\n   摘要: {abs_text}")
            input_parts.append("")

        input_text = "\n".join(input_parts)
        if len(input_text) > 12000:
            input_text = input_text[:12000] + "...(内容过长已截断)"

        prompt = (
            "你是一个生物医药领域的科研情报助手。请阅读以下最新顶级期刊文献，"
            "生成一份简洁的每日文献速递。要求：\n"
            "1. 先写一段总体概述（3-5句话），概括今天最重要的研究方向和突破\n"
            "2. 然后按期刊分组，每篇文献用1-2句话提炼核心发现和意义\n"
            "3. 重点突出有重大突破、临床转化价值高的研究\n"
            "4. 语言专业但简洁，不要废话，控制在1500字以内\n"
            "5. 用中文输出\n\n"
            f"以下是文献列表：\n\n{input_text}"
        )

        api_base = config.get("ai_api_base", "https://ark.cn-beijing.volces.com/api/v3")
        model = config.get("ai_model", "doubao-1-5-pro-32k-250115")

        resp = requests.post(
            f"{api_base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "max_tokens": 2000,
            },
            timeout=60,
        )
        resp.raise_for_status()
        summary = resp.json()["choices"][0]["message"]["content"].strip()
        logger.info("AI汇总成功")
        return summary
    except Exception as e:
        logger.warning(f"AI汇总失败，降级为规则摘要: {e}")
        return rule_based_summary(articles_by_group)


def rule_based_summary(articles_by_group):
    """无AI时的降级方案：按分组列出标题和摘要前80字"""
    parts = ["以下是最新生物医药顶刊文献速递（规则摘要，配置AI Key后可获得智能汇总）：\n"]
    for group, articles in articles_by_group.items():
        if not articles:
            continue
        parts.append(f"### {group}")
        for art in articles:
            abs_short = art["abstract"][:80] + "..." if len(art["abstract"]) > 80 else art["abstract"]
            parts.append(f"- **{art['title']}**\n  {abs_short}")
        parts.append("")
    return "\n".join(parts)


# ============================================================
# 飞书推送
# ============================================================

def gen_feishu_sign(secret, timestamp):
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def build_feishu_card(articles_by_group, summary, total_count, config):
    """构建飞书消息卡片"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 构建文献列表（按分组）
    article_sections = []
    for group, articles in articles_by_group.items():
        if not articles:
            continue
        lines = [f"**📖 {group}**"]
        for art in articles[:8]:  # 每组最多显示8篇
            title_link = f"[{art['title'][:60]}]({art['url']})"
            journal_info = f"_{art['journal_abbr']}_"
            lines.append(f"- {title_link}  {journal_info}")
        if len(articles) > 8:
            lines.append(f"- ...还有 {len(articles)-8} 篇")
        article_sections.append("\n".join(lines))

    articles_text = "\n\n".join(article_sections)

    # 摘要部分（截断，避免卡片过长）
    summary_text = summary[:2000] if len(summary) > 2000 else summary

    # 统计信息
    journal_count = sum(1 for arts in articles_by_group.values() if arts)

    card = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"【通知】🧬 生物医药顶刊文献速递 | {today}"},
                "template": "purple",
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**今日概览**：共监测 {journal_count} 本期刊，发现 {total_count} 篇新文献"},
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**📊 AI 智能汇总**\n{summary_text}"},
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**📚 文献列表**\n{articles_text}"},
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": "数据来源：PubMed | 点击标题可跳转原文 | 每日自动推送"}
                    ],
                },
            ],
        },
    }
    return card


def send_feishu(articles_by_group, summary, total_count, config, logger):
    """发送飞书消息"""
    webhook = config.get("feishu_webhook", "")
    if not webhook or "你的webhook" in webhook:
        logger.warning("飞书webhook未配置，跳过推送")
        return False

    try:
        payload = build_feishu_card(articles_by_group, summary, total_count, config)
        secret = config.get("feishu_secret", "").strip()
        if secret:
            timestamp = str(int(time.time()))
            sign = gen_feishu_sign(secret, timestamp)
            payload["timestamp"] = timestamp
            payload["sign"] = sign

        resp = requests.post(webhook, json=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            logger.info(f"飞书推送成功，共 {total_count} 篇文献")
            return True
        else:
            logger.error(f"飞书推送失败: {result}")
            return False
    except Exception as e:
        logger.error(f"飞书推送异常: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def run_once(config, logger, init_mode=False):
    """执行一次检查"""
    logger.info("=" * 60)
    logger.info("开始检查生物医药顶刊最新文献...")

    journals_config = config.get("journals", {})
    check_days = config.get("check_days", 3)
    max_per_journal = config.get("max_articles_per_journal", 5)
    max_total = config.get("max_total_articles", 50)

    seen = load_seen(config.get("seen_file", "seen_pmids.json"))

    # 1. 搜索所有期刊的新文献
    all_new_pmids = []
    articles_by_group = {}
    total_found = 0

    for group, journal_list in journals_config.items():
        group_articles = []
        for journal in journal_list:
            logger.info(f"搜索期刊: {journal}")
            pmids = search_journal(journal, check_days, max_per_journal, logger)
            time.sleep(0.4)  # 遵守PubMed速率限制（每秒不超过3次）

            # 过滤已推送的
            new_pmids = [p for p in pmids if p not in seen]
            if new_pmids:
                logger.info(f"  新文献: {len(new_pmids)} 篇")
                articles = fetch_articles(new_pmids, logger)
                # 过滤非研究论文
                research_articles = [a for a in articles if is_research_article(a)]
                group_articles.extend(research_articles)
                all_new_pmids.extend([a["pmid"] for a in research_articles])
                total_found += len(research_articles)
                time.sleep(0.4)

        articles_by_group[group] = group_articles

    logger.info(f"共发现 {total_found} 篇新研究文献")

    if init_mode:
        # 初始化模式：全部标记为已读，不推送
        for pmid in all_new_pmids:
            seen[pmid] = {"date": datetime.now().strftime("%Y-%m-%d")}
        save_seen(config.get("seen_file", "seen_pmids.json"), seen)
        logger.info(f"初始化完成，已记录 {len(all_new_pmids)} 篇现有文献（不推送）")
        return

    if total_found == 0:
        logger.info("没有新文献，结束")
        return

    # 2. 限制总数
    if total_found > max_total:
        logger.info(f"文献过多，截断到 {max_total} 篇")
        # 按组截断
        current = 0
        for group in articles_by_group:
            arts = articles_by_group[group]
            if current + len(arts) > max_total:
                articles_by_group[group] = arts[:max_total - current]
                current = max_total
            else:
                current += len(arts)

    # 3. AI汇总
    logger.info("开始AI智能汇总...")
    summary = ai_summarize(articles_by_group, config, logger)

    # 4. 飞书推送
    actual_total = sum(len(arts) for arts in articles_by_group.values())
    success = send_feishu(articles_by_group, summary, actual_total, config, logger)

    # 5. 更新已推送记录
    for group_arts in articles_by_group.values():
        for art in group_arts:
            seen[art["pmid"]] = {
                "title": art["title"][:80],
                "journal": art["journal_abbr"],
                "date": datetime.now().strftime("%Y-%m-%d"),
                "pushed": success,
            }
    save_seen(config.get("seen_file", "seen_pmids.json"), seen)

    logger.info(f"本轮处理完成，共推送 {actual_total} 篇文献")


def main():
    parser = argparse.ArgumentParser(description="生物医药顶刊文献监控机器人")
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--init", action="store_true", help="初始化模式：只记录不推送")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    args = parser.parse_args()

    config = load_config(args.config)
    logger = setup_logging(config.get("log_file", "monitor.log"))

    logger.info("=" * 60)
    logger.info("生物医药顶刊文献监控机器人启动")
    logger.info(f"监测天数: 最近 {config.get('check_days', 3)} 天")
    journal_count = sum(len(v) for v in config.get("journals", {}).values())
    logger.info(f"监测期刊数: {journal_count} 本")

    if args.init:
        run_once(config, logger, init_mode=True)
        return

    if args.once:
        run_once(config, logger, init_mode=False)
        return

    # 持续运行模式（一般用GitHub Actions，不需要这个）
    while True:
        try:
            run_once(config, logger, init_mode=False)
        except Exception as e:
            logger.error(f"主循环异常: {e}", exc_info=True)
        logger.info("等待24小时后下次检查...")
        time.sleep(86400)


if __name__ == "__main__":
    main()
