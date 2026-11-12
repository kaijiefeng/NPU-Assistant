#!/usr/bin/env python3
"""公开文档采集器：NWPU 各站点栏目 → 详情页 → 统一 Markdown 语料。

设计原则（沿用 ``services/campus_sync.py`` 的受控采集约定）：

* 只读取公开栏目列表页与详情页，不登录、不绕过验证码、不递归全站、不抓附件；
* 按站点限速（默认同域 0.6s 一次）、遵守 robots.txt；
* 以 URL 去重，可断点续采（manifest.jsonl 同时充当进度文件）；
* 详情页正文复用 ``services.importer.extract_article``（与线上「快速收录」同一套提取逻辑）；
* 采集结果独立存放于 ``docs/npu-handbook/collect/``，不污染既有 114 篇精选语料。

用法::

    # 1) 生成/更新栏目种子（探测机构站点，见 --discover，输出 collect/discovered.json）
    python scripts/handbook_collect.py --discover

    # 2) 按 seeds.json 采集（默认目标 1200 篇，可反复运行续采）
    python scripts/handbook_collect.py --fetch --target 1200

    # 3) 生成采集报告与覆盖矩阵
    python scripts/handbook_collect.py --report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import queue
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "npu-handbook" / "collect"
CORPUS_DIR = OUT_DIR / "corpus"
MANIFEST = OUT_DIR / "manifest.jsonl"
SHARD_DIR = OUT_DIR / "manual-shards"
RAW_SHARD_DIR = SHARD_DIR / "raw"
SEEDS = OUT_DIR / "seeds.json"
DISCOVERED = OUT_DIR / "discovered.json"
REPORT = OUT_DIR / "COLLECTION-REPORT.md"
COVERAGE_CSV = OUT_DIR / "coverage.csv"
EXISTING_CORPUS = ROOT / "docs" / "npu-handbook" / "corpus.json"

sys.path.insert(0, str(ROOT / "backend"))
from app.services import campus_sync as cs  # noqa: E402
from app.services import importer  # noqa: E402

logger = logging.getLogger("npu-assistant.collect")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DETAIL_RE = re.compile(r"/info/\d+/\d+\.(?:htm|html)$", re.I)
DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
MIN_CHARS = 180
MAX_CHARS = 6000

# 分类法：L1 主题（对齐既有 coverage.csv 并扩展），按标题/路径关键词打分。
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("招生与入学", ("招生", "录取", "入学", "报到", "迎新", "新生", "分班", "生源地")),
    ("学籍与注册", ("学籍", "注册", "转专业", "休学", "复学", "退学", "保留学籍", "降级", "转段")),
    ("培养与课程", ("培养方案", "课程", "选课", "教学", "实验", "实践", "小学期", "导师", "开题", "中期")),
    ("考试与成绩", ("考试", "成绩", "补考", "缓考", "重修", "四六级", "GPA", "学分", "考场", "监考")),
    ("学位与毕业", ("学位", "毕业", "论文", "答辩", "评阅", "离校", "学位授予")),
    ("推免与升学", ("推免", "保研", "考研", "直博", "硕博连读", "夏令营")),
    ("奖助与资助", ("奖学金", "助学金", "资助", "困难", "绿色通道", "勤工", "学费减免", "补助")),
    ("纪律与申诉", ("违纪", "处分", "申诉", "诚信", "学术不端", "作弊")),
    ("宿舍与公寓", ("宿舍", "公寓", "住宿", "调宿", "退宿", "门禁", "晚归", "洗浴", "水电")),
    ("生活服务", ("食堂", "餐厅", "超市", "快递", "外卖", "热水", "洗衣", "打印", "报修", "校车", "浴室")),
    ("医疗与医保", ("医保", "医院", "就医", "体检", "报销", "门诊", "卫生")),
    ("心理健康", ("心理", "咨询", "危机", "朋辈", "情绪")),
    ("就业与档案", ("就业", "三方", "档案", "报到证", "签约", "派遣", "改派", "招聘", "双选", "实习")),
    ("网络与信息化", ("校园网", "网络", "信息化", "账号", "密码", "VPN", "一网通办", "系统")),
    ("校园卡与财务", ("校园卡", "一卡通", "缴费", "收费", "财务", "发票", "报销", "奖贷")),
    ("图书馆与文体", ("图书馆", "借阅", "体育", "场馆", "自习", "文体", "运动", "跑道", "游泳", "社团")),
    ("安全与户籍", ("安全", "保卫", "户口", "户籍", "消防", "诈骗", "应急", "防诈", "实验室安全")),
    ("国际交流", ("国际", "出国", "留学", "交换", "访学", "境外", "外事")),
    ("党团与思政", ("党建", "党支部", "党员", "团学", "团委", "思政", "主题教育", "团日")),
    ("科研与竞赛", ("科研", "竞赛", "创新创业", "挑战杯", "项目", "专利", "论文投稿", "重点实验室")),
    ("军训与国防", ("军训", "国防", "征兵", "役前", "军事理论")),
    ("校历与出行", ("校历", "假期", "放假", "开学", "校门", "地铁", "公交", "火车站")),
    ("校园概况与机构", ("学校简介", "学院简介", "学院概况", "现任领导", "组织机构", "校区", "历史", " culture", "师资")),
]

DOC_TYPE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("制度规章", ("办法", "规定", "细则", "条例", "制度", "章程", "规范", "标准", "准则", "实施方案", "意见")),
    ("办事指南", ("指南", "流程", "办理", "须知", "问答", "常见问题", "FAQ", "手册", "说明", "操作", "入口", "登录")),
    ("通知公告", ("通知", "公告", "公示", "通报", "提醒", "安排", "征稿", "报名", "征集")),
    ("数据名录", ("名单", "目录", "汇总", "统计", "一览", "表格")),
    ("新闻动态", ("新闻", "动态", "举行", "开展", "举办", "荣获", "顺利", "成功", "会议", "论坛", "座谈", "调研", "走访", "讲座", "报告会", "获奖", "蝉联", "启动")),
]


AUDIENCE_VALUES = (
    "本科新生",
    "本科生",
    "研究生",
    "毕业生",
    "国际学生",
    "教职工",
    "全体",
)
AUDIENCE_ALIASES = {
    "学生": "全体",
    "本硕学生": "本科生",
    "本硕博学生": "全体",
    "本科": "本科生",
    "硕士": "研究生",
    "博士": "研究生",
    "教师": "教职工",
}


def normalize_audience(value: str) -> str:
    text = (value or "").strip()
    text = AUDIENCE_ALIASES.get(text, text)
    return text if text in AUDIENCE_VALUES else "全体"


def classify(title: str, path: str, default: str = "其他") -> tuple[str, str]:
    text = f"{title} {path}".lower()
    category = default
    for name, words in CATEGORY_RULES:
        if any(word.lower() in text for word in words):
            category = name
            break
    doc_type = "其他"
    for name, words in DOC_TYPE_RULES:
        if any(word.lower() in text for word in words):
            doc_type = name
            break
    if doc_type == "其他":
        # 标题没线索时按栏目路径兜底（NWPU CMS 常见栏目缩写）。
        doc_type = classify_by_path(path)
    return category, doc_type


def classify_by_path(path: str) -> str:
    lowered = (path or "").lower()
    hints = [
        ("制度规章", ("zdgk", "gzzd", "zcfg", "zdgl", "guizhang", "zhidu")),
        ("办事指南", ("bszn", "fwzn", "bslc", "cyxz", "xzzq", "xzq", "zcxz", "fwzx")),
        ("通知公告", ("tzgg", "ggtz", "gg", "tz", "gsgg", "xngg", "gonggao")),
        ("新闻动态", ("xwdt", "xwzx", "zhxw", "xwjj", "gzdt", "xydt", "kydt", "jldt", "djdt", "zxdt")),
        ("数据名录", ("zsml", "md", "ml", "tj", "ndbg")),
    ]
    for name, keys in hints:
        if any(key in lowered for key in keys):
            return name
    return "其他"


def site_of(url: str) -> str:
    return urlsplit(url).netloc


def doc_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:32]


def clean_title(value: str) -> str:
    title = re.sub(r"\s+", " ", value or "").strip(" \t\r\n·|-_—")
    # 去掉站点名后缀（如「中华人民共和国治安管理处罚法-国际教育学院」）。
    title = re.sub(
        r"\s*[-_|—]\s*[^-_|—]{2,16}(?:大学|学院|中心|部|处|馆|网|办公室|研究院)\s*$",
        "",
        title,
    )
    return title.strip()


def extract_date(*texts: str) -> str:
    for text in texts:
        match = DATE_RE.search(text or "")
        if match:
            year, month, day = (int(part) for part in match.groups())
            if 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def manifest_files() -> list[Path]:
    """采集清单：脚本自身 manifest + 子智能体分片（manual-shards/*.jsonl）。"""
    files = [MANIFEST] if MANIFEST.is_file() else []
    if SHARD_DIR.is_dir():
        files.extend(sorted(SHARD_DIR.glob("*.jsonl")))
    return files


def load_existing_urls() -> set[str]:
    urls: set[str] = set()
    if EXISTING_CORPUS.is_file():
        for row in json.loads(EXISTING_CORPUS.read_text(encoding="utf-8")):
            if row.get("source_url"):
                urls.add(str(row["source_url"]))
    for row in load_manifest():
        urls.add(str(row["url"]))
    return urls


def load_manifest() -> list[dict]:
    rows: list[dict] = []
    for path in manifest_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ---------------------------------------------------------------------------
# 发现：机构站点 → 首页栏目 → 列表页（含详情链接数）
# ---------------------------------------------------------------------------
ORG_PAGES = [
    "https://www.nwpu.edu.cn/jgsz_cd_/dzjg/dqbm.htm",
    "https://www.nwpu.edu.cn/jgsz_cd_/jxkyjg/xy.htm",
    "https://www.nwpu.edu.cn/jgsz_cd_/zhichenghe/zcdw.htm",
]
COLUMN_HINT = re.compile(
    r"(通知|公告|制度|规章|政策|文件|信息公开|公开|新闻|动态|服务|办事|下载|工作|管理|"
    r"招生|就业|党建|科研|教学|培养|学位|资助|奖助|心理|后勤|安全|图书|"
    r"tzgg|zdgk|xwzx|xxgk|gzdt|jxxx|xngg|list|xwdt|zhxw|tz|gg)",
    re.I,
)


def discover(workers: int = 6, rate_limit: float = 0.4) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        sites: dict[str, str] = {}
        for page in ORG_PAGES:
            try:
                resp = client.get(page)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                logger.warning("机构页抓取失败 %s：%s", page, exc)
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = urljoin(page, anchor["href"].strip())
                parts = urlsplit(href)
                if parts.scheme not in {"http", "https"} or not parts.netloc.endswith("nwpu.edu.cn"):
                    continue
                if parts.netloc in {"www.nwpu.edu.cn", "news.nwpu.edu.cn", "xygg.nwpu.edu.cn"}:
                    continue
                name = re.sub(r"\s+", "", anchor.get_text(" ", strip=True))
                if not name or len(name) > 20:
                    continue
                sites.setdefault(f"{parts.scheme}://{parts.netloc}", name)
        logger.info("机构站点 %s 个", len(sites))

        lock = threading.Lock()
        results: list[dict] = []

        def probe(origin: str, name: str) -> None:
            entry = {"origin": origin, "name": name, "home_status": None, "list_pages": []}
            try:
                with httpx.Client(
                    timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}
                ) as site_client:
                    home = site_client.get(origin + "/")
                    entry["home_status"] = home.status_code
                    if home.status_code != 200:
                        with lock:
                            results.append(entry)
                        return
                    soup = BeautifulSoup(home.text, "html.parser")
                    candidates: dict[str, str] = {}
                    for anchor in soup.find_all("a", href=True):
                        href = urljoin(origin + "/", anchor["href"].strip()).split("#", 1)[0]
                        parts = urlsplit(href)
                        if parts.netloc != urlsplit(origin).netloc:
                            continue
                        if not re.search(r"\.(?:htm|html)$", parts.path, re.I):
                            continue
                        if DETAIL_RE.search(parts.path) or "/info/" in parts.path:
                            continue
                        text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))
                        if not COLUMN_HINT.search(text) and not COLUMN_HINT.search(parts.path):
                            continue
                        candidates.setdefault(href, text[:30])
                    for list_url, label in list(candidates.items())[:16]:
                        time.sleep(rate_limit)
                        try:
                            listing = site_client.get(list_url)
                            if listing.status_code != 200:
                                continue
                            items = cs.parse_list_page(listing.text, list_url)
                        except Exception:  # noqa: BLE001
                            continue
                        if len(items) < 5:
                            continue
                        category, doc_type = classify(label or "", list_url, default="")
                        entry["list_pages"].append(
                            {
                                "url": list_url,
                                "label": label,
                                "count": len(items),
                                "category": category,
                                "doc_type": doc_type,
                                "samples": [item.title for item in items[:3]],
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["list_pages"].sort(key=lambda row: -row["count"])
            with lock:
                results.append(entry)
                logger.info(
                    "%s %s 列表页 %s 个，可采 %s 篇",
                    name,
                    origin,
                    len(entry["list_pages"]),
                    sum(row["count"] for row in entry["list_pages"]),
                )

        threads = []
        batch = list(sites.items())
        for index in range(0, len(batch), workers):
            for origin, name in batch[index : index + workers]:
                thread = threading.Thread(target=probe, args=(origin, name), daemon=True)
                thread.start()
                threads.append(thread)
            for thread in threads:
                thread.join()
            threads = []
        results.sort(key=lambda row: -(sum(p["count"] for p in row["list_pages"])))
        payload = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sites": results,
        }
        DISCOVERED.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        total_pages = sum(len(row["list_pages"]) for row in results)
        total_est = sum(sum(p["count"] for p in row["list_pages"]) for row in results)
        logger.info("已写入 %s：%s 个站点，%s 个列表页，估算 %s 篇", DISCOVERED.name, len(results), total_pages, total_est)
        return 0
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def article_markdown(record: dict, text: str) -> str:
    front = [
        "---",
        f"title: {json.dumps(record['title'], ensure_ascii=False)}",
        f"source_url: {record['url']}",
        f"source_site: {record['site']}",
        f"source_name: {json.dumps(record.get('source_name', ''), ensure_ascii=False)}",
        f"category: {record['category']}",
        f"doc_type: {record['doc_type']}",
        f"audience: {record.get('audience', '')}",
        f"publish_date: {record.get('publish_date', '') or ''}",
        f"collected_at: {record['collected_at']}",
        "---",
        "",
        f"# {record['title']}",
        "",
        text.strip(),
        "",
        f"> 来源：{record['url']}",
        "",
    ]
    return "\n".join(front)


MAX_BYTES = 5 * 1024 * 1024


def fetch_html(client: httpx.Client, url: str) -> tuple[str, str] | None:
    """直接抓取详情页 HTML。

    不复用 ``importer.fetch_url``：它带 SSRF 防护，会拒绝解析到私网/保留地址的域名，
    而部分高校站点在本机 DNS 下会被映射到保留网段；这里的种子是人工挑选的官方公开站点。
    """
    response = client.get(url)
    if response.status_code != 200:
        return None
    content_type = (response.headers.get("content-type") or "").lower()
    if content_type and "html" not in content_type and "text" not in content_type:
        return None
    if len(response.content or b"") > MAX_BYTES:
        return None
    return response.text, response.headers.get("content-type") or "text/html"


def fetch_document(
    client: httpx.Client,
    item: cs.ListItem,
    meta: dict,
) -> dict | None:
    page = fetch_html(client, item.url)
    if page is None:
        return None
    html, content_type = page
    article = importer.extract_article(html, content_type=content_type)
    text = (article.get("text") or "").strip()
    if len(text) < MIN_CHARS:
        return None
    title = clean_title(article.get("title") or item.title)
    hint_path = f"{item.url} {meta.get('list_page', '')} {meta.get('label', '')}"
    category, doc_type = classify(title, hint_path, default=meta.get("category") or "其他")
    if meta.get("doc_type") and meta["doc_type"] != "其他":
        doc_type = meta["doc_type"]
    content_id = doc_id(item.url)
    record = {
        "id": content_id,
        "url": item.url,
        "title": title,
        "site": site_of(item.url),
        "source_name": meta.get("source_name", ""),
        "category": category,
        "doc_type": doc_type,
        "audience": normalize_audience(meta.get("audience", "")),
        "publish_date": item.date or extract_date(title),
        "collected_at": datetime.now().astimezone().date().isoformat(),
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "file": f"corpus/{content_id}.md",
        "list_page": meta.get("list_page", ""),
    }
    record["text"] = text[:MAX_CHARS]
    return record


def fetch(target: int, per_list: int, delay: float, workers: int) -> int:
    if not SEEDS.is_file():
        logger.error("缺少 %s，请先运行 --discover 并据此生成 seeds.json", SEEDS)
        return 2
    seeds = json.loads(SEEDS.read_text(encoding="utf-8"))
    pages: list[dict] = []
    sites = seeds.get("sites", [])
    site_pages = [site.get("list_pages", []) for site in sites]
    depth = max((len(pages) for pages in site_pages), default=0)
    for index in range(depth):
        for site, page_list in zip(sites, site_pages):
            if index < len(page_list):
                page = page_list[index]
                pages.append({**page, "site": site, "list_page": page["url"]})
    logger.info("种子列表页 %s 个", len(pages))

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_existing_urls()
    manifest_rows = load_manifest()
    collected = len(manifest_rows)
    if collected >= target:
        logger.info("已完成目标 %s（当前 %s），跳过采集", target, collected)
        return 0

    robots = cs.RobotsCache(USER_AGENT)
    task_queue: queue.Queue[dict] = queue.Queue()
    for page in pages:
        task_queue.put(page)
    state = {"collected": collected, "attempted": 0, "failed": 0, "skipped": 0}
    state_lock = threading.Lock()
    write_lock = threading.Lock()
    manifest_handle = MANIFEST.open("a", encoding="utf-8")

    def worker() -> None:
        with httpx.Client(
            timeout=25,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            while True:
                if state["collected"] >= target:
                    return
                try:
                    page = task_queue.get_nowait()
                except queue.Empty:
                    return
                list_url = page["list_page"]
                site = page["site"]
                try:
                    if not robots.allowed(list_url):
                        with state_lock:
                            state["skipped"] += 1
                        continue
                    time.sleep(delay)
                    listing = client.get(list_url)
                    listing.raise_for_status()
                    items = cs.parse_list_page(listing.text, list_url)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("列表页失败 %s：%s", list_url, exc)
                    with state_lock:
                        state["failed"] += 1
                    continue
                taken = 0
                attempts = 0
                max_attempts = per_list + 8
                for item in items:
                    if state["collected"] >= target or taken >= per_list or attempts >= max_attempts:
                        break
                    attempts += 1
                    if item.url in existing:
                        continue
                    with state_lock:
                        state["attempted"] += 1
                    try:
                        if not robots.allowed(item.url):
                            continue
                        time.sleep(delay)
                        record = fetch_document(
                            client,
                            item,
                            {
                                **page,
                                "source_name": site.get("name", ""),
                                "audience": site.get("audience", ""),
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("详情失败 %s：%s", item.url, exc)
                        with state_lock:
                            state["failed"] += 1
                        continue
                    if record is None:
                        continue
                    text = record.pop("text")
                    path = OUT_DIR / record["file"]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with write_lock:
                        path.write_text(article_markdown(record, text), encoding="utf-8")
                        manifest_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        manifest_handle.flush()
                        existing.add(item.url)
                        state["collected"] += 1
                        taken += 1
                        if state["collected"] % 25 == 0:
                            logger.info(
                                "进度 %s/%s（列表页 %s，失败 %s）",
                                state["collected"],
                                target,
                                page.get("label", ""),
                                state["failed"],
                            )
                task_queue.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, workers))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    manifest_handle.close()
    logger.info(
        "采集结束：新增 %s，尝试 %s，失败 %s，跳过 %s",
        state["collected"] - collected,
        state["attempted"],
        state["failed"],
        state["skipped"],
    )
    return 0


# ---------------------------------------------------------------------------
# 子智能体分片导入（manual-shards/raw/*.json → corpus + imported.jsonl）
# ---------------------------------------------------------------------------
def import_shards() -> int:
    files = sorted(RAW_SHARD_DIR.glob("*.json")) if RAW_SHARD_DIR.is_dir() else []
    if not files:
        logger.error("没有找到 %s/*.json（子智能体采集分片）", RAW_SHARD_DIR)
        return 1
    existing = load_existing_urls()
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    target_manifest = SHARD_DIR / "imported.jsonl"
    known_categories = {name for name, _ in CATEGORY_RULES} | {"其他"}
    known_types = {name for name, _ in DOC_TYPE_RULES} | {"其他"}
    added = skipped = 0
    with target_manifest.open("a", encoding="utf-8") as handle:
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.warning("分片解析失败 %s：%s", path.name, exc)
                continue
            documents = payload.get("documents") if isinstance(payload, dict) else payload
            for doc in documents or []:
                url = str(doc.get("url") or "").strip()
                title = clean_title(str(doc.get("title") or ""))
                body = str(doc.get("body_md") or doc.get("text") or "").strip()
                if not url.startswith("http") or not title or len(body) < MIN_CHARS:
                    skipped += 1
                    continue
                if url in existing:
                    skipped += 1
                    continue
                category = str(doc.get("category") or "").strip()
                doc_type = str(doc.get("doc_type") or "").strip()
                if category not in known_categories or not category:
                    category, auto_type = classify(title, f"{url} {doc.get('list_page', '')}")
                    if not doc_type:
                        doc_type = auto_type
                if doc_type not in known_types or not doc_type:
                    doc_type = "其他"
                publish_date = str(doc.get("publish_date") or "").strip()
                if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", publish_date):
                    publish_date = extract_date(publish_date, title)
                content_id = doc_id(url)
                record = {
                    "id": content_id,
                    "url": url,
                    "title": title,
                    "site": site_of(url),
                    "source_name": str(doc.get("source_name") or "子智能体采集"),
                    "category": category,
                    "doc_type": doc_type,
                    "audience": normalize_audience(str(doc.get("audience") or "")),
                    "publish_date": publish_date,
                    "collected_at": datetime.now().astimezone().date().isoformat(),
                    "chars": len(body),
                    "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "file": f"corpus/{content_id}.md",
                    "list_page": str(doc.get("list_page") or ""),
                }
                record["text"] = body[:MAX_CHARS]
                text = record.pop("text")
                (OUT_DIR / record["file"]).write_text(article_markdown(record, text), encoding="utf-8")
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                existing.add(url)
                added += 1
    logger.info("分片导入完成：新增 %s，跳过 %s（分片 %s 个）", added, skipped, len(files))
    return 0


# ---------------------------------------------------------------------------
# 重新分类（不重新抓取；只补 category/doc_type 的「其他」）
# ---------------------------------------------------------------------------
def reclassify() -> int:
    files = [path for path in manifest_files() if path.name.endswith(".jsonl")]
    if not files:
        logger.error("没有可重分类的清单文件")
        return 1
    known_categories = {name for name, _ in CATEGORY_RULES} | {"其他"}
    changed = 0
    for path in files:
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        for row in rows:
            new_category, new_type = classify(
                str(row.get("title") or ""),
                f"{row.get('url', '')} {row.get('list_page', '')}",
                default=row.get("category") or "其他",
            )
            updated = False
            if (row.get("category") in (None, "", "其他")) and new_category in known_categories:
                row["category"] = new_category
                updated = True
            if row.get("doc_type") in (None, "", "其他") and new_type != "其他":
                row["doc_type"] = new_type
                updated = True
            row["audience"] = normalize_audience(str(row.get("audience") or ""))
            if updated or True:
                markdown_path = OUT_DIR / str(row.get("file") or "")
                if markdown_path.is_file():
                    text = markdown_path.read_text(encoding="utf-8")
                    text = re.sub(r"(?m)^category: .*$", f"category: {row['category']}", text)
                    text = re.sub(r"(?m)^doc_type: .*$", f"doc_type: {row['doc_type']}", text)
                    text = re.sub(r"(?m)^audience: .*$", f"audience: {row['audience']}", text)
                    markdown_path.write_text(text, encoding="utf-8")
            changed += 1 if updated else 0
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
    logger.info("重分类完成：修正 %s 行（分类只填「其他」，不改已有结论）", changed)
    return 0


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def report() -> int:
    rows = load_manifest()
    if not rows:
        logger.error("manifest.jsonl 为空，先运行 --fetch")
        return 1
    by_site = Counter(row["site"] for row in rows)
    by_category = Counter(row["category"] for row in rows)
    by_type = Counter(row["doc_type"] for row in rows)
    by_audience = Counter(normalize_audience(row.get("audience") or "") for row in rows)
    matrix: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        matrix[row["category"]][row["doc_type"]] += 1
    total_chars = sum(int(row.get("chars") or 0) for row in rows)
    dates = sorted(row.get("publish_date") or "" for row in rows if row.get("publish_date"))
    categories = [name for name, _ in CATEGORY_RULES] + ["其他"]
    types = [name for name, _ in DOC_TYPE_RULES] + ["其他"]
    lines = [
        "# 公开文档采集报告",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 文档总数：**{len(rows)}**（目标 ≥1000）",
        f"- 覆盖站点：**{len(by_site)}** 个；主题类目：**{len([c for c in by_category if by_category[c]])}** 个；文档类型：**{len([t for t in by_type if by_type[t]])}** 种",
        f"- 正文合计：{total_chars:,} 字符（平均 {total_chars // max(1, len(rows)):,} 字/篇）",
        f"- 发布时间范围：{dates[0] if dates else '未知'} ~ {dates[-1] if dates else '未知'}",
        "",
        "## 站点覆盖（Top 30）",
        "",
        "| 站点 | 文档数 |",
        "|---|---:|",
    ]
    for site, count in by_site.most_common(30):
        lines.append(f"| {site} | {count} |")
    lines += ["", "## 主题 × 文档类型覆盖矩阵", "", "| 主题 | " + " | ".join(types) + " | 合计 |", "|---|" + "---:|" * (len(types) + 1)]
    for category in categories:
        counts = matrix.get(category, Counter())
        if not counts:
            continue
        cells = " | ".join(str(counts.get(name, 0)) for name in types)
        lines.append(f"| {category} | {cells} | {sum(counts.values())} |")
    lines += ["", "## 文档类型分布", "", "| 类型 | 文档数 |", "|---|---:|"]
    for doc_type, count in by_type.most_common():
        lines.append(f"| {doc_type} | {count} |")
    lines += ["", "## 受众标注", "", "| 受众 | 文档数 |", "|---|---:|"]
    for audience, count in by_audience.most_common(12):
        lines.append(f"| {audience} | {count} |")
    lines += [
        "",
        "## 示例文档",
        "",
    ]
    for row in rows[:15]:
        lines.append(f"- [{row['title']}]({row['url']})（{row['site']} · {row['category']} · {row['doc_type']}）")
    lines += [
        "",
        "## 方法与边界",
        "",
        "- 只采集公开栏目列表页与详情页正文，不含附件 PDF / 图片，不登录、不绕过验证码。",
        "- 站点限速（默认同域 0.6s/次）并遵守 robots.txt；以 URL 去重，可断点续采。",
        "- 正文由 `services.importer.extract_article` 提取，与线上「快速收录」同一套逻辑；不足 180 字丢弃。",
        "- 主题/类型由标题与路径关键词规则分类，后续可人工抽检修正。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")

    with COVERAGE_CSV.open("w", encoding="utf-8", newline="") as handle:
        import csv

        writer = csv.writer(handle)
        writer.writerow(["category", "documents", "share"])
        for category in categories:
            if by_category.get(category):
                writer.writerow(
                    [category, by_category[category], f"{by_category[category] / len(rows):.1%}"]
                )
    logger.info("报告已写入 %s（%s 篇）", REPORT, len(rows))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="NWPU 公开文档采集器")
    parser.add_argument("--discover", action="store_true", help="探测机构站点与栏目")
    parser.add_argument("--fetch", action="store_true", help="按 seeds.json 采集")
    parser.add_argument("--report", action="store_true", help="生成报告与覆盖矩阵")
    parser.add_argument("--import-shards", action="store_true", help="导入子智能体采集分片")
    parser.add_argument("--reclassify", action="store_true", help="只补充分类为「其他」的记录，不重新抓取")
    parser.add_argument("--target", type=int, default=1200, help="目标文档数")
    parser.add_argument("--per-list", type=int, default=25, help="每个列表页最多采集篇数")
    parser.add_argument("--delay", type=float, default=0.6, help="同域请求最小间隔秒")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    args = parser.parse_args()
    exit_code = 0
    if args.discover:
        exit_code = discover(workers=args.workers)
    if args.fetch:
        exit_code = fetch(args.target, args.per_list, args.delay, args.workers) or exit_code
    if args.report:
        exit_code = report() or exit_code
    if args.import_shards:
        exit_code = import_shards() or exit_code
    if args.reclassify:
        exit_code = reclassify() or exit_code
    if not any((args.discover, args.fetch, args.report, args.import_shards, args.reclassify)):
        parser.print_help()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
