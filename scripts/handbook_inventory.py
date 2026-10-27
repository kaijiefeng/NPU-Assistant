"""M2：生成 NPU 学生手册数据集来源盘点。

读取当前 documents，按类别归类，输出：
- docs/npu-handbook/sources.json
- docs/npu-handbook/coverage.csv
- docs/npu-handbook/INVENTORY.md
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend/data/app.db"
OUT = ROOT / "docs/npu-handbook"

CATEGORIES = [
    ("学籍管理", ("学籍", "注册", "转专业", "休学", "复学", "退学", "保留学籍", "降级")),
    ("教学与考试", ("课程", "教学", "选课", "调课", "考试", "成绩", "补考", "缓考", "重修", "学业")),
    ("学位与论文", ("学位", "论文", "评阅", "答辩", "学术规范", "学术不端")),
    ("奖助与资助", ("奖助", "奖学金", "助学金", "困难", "贷款", "绿色通道", "资助", "勤工")),
    ("纪律与申诉", ("违纪", "处分", "申诉", "考试违纪")),
    ("宿舍与生活", ("公寓", "宿舍", "洗浴", "洗澡", "洗衣", "空调", "水电", "门禁", "晚归", "报修", "食堂", "餐厅", "超市", "快递", "电动车", "打印")),
    ("心理健康", ("心理", "咨询")),
    ("医疗与医保", ("医保", "医院", "就医", "体检")),
    ("就业与档案", ("就业", "三方", "档案", "报到证", "签约", "派遣", "改派")),
    ("校历与出行", ("校历", "校车", "假期", "暑假", "寒假")),
    ("网络与校园卡", ("校园网", "网络", "校园卡", "一卡通", "信息化", "网费")),
    ("图书馆与文体", ("图书", "图书馆", "体育", "场馆", "自习")),
    ("安全与户籍", ("安全", "保卫", "报警", "户口", "户籍", "消防", "诈骗")),
    ("迎新与新生", ("迎新", "新生", "报到", "入学")),
    ("国际交流", ("国际", "出国", "交流", "留学", "交换")),
]


def classify(title: str, tags: str) -> str:
    text = f"{title} {tags}"
    for category, words in CATEGORIES:
        if any(word in text for word in words):
            return category
    return "其他"


TARGETS = [
    ("学籍管理", "本科生学生手册（最新版）", "P0", "完整手册是数据集主骨架"),
    ("学籍管理", "研究生手册（最新版）", "P0", "研究生学籍/培养主骨架"),
    ("学籍管理", "本科生转专业（大类）实施办法", "P1", "转专业细则"),
    ("学籍管理", "本科生考试管理办法", "P1", "补考/缓考/重修细则"),
    ("学籍管理", "学生申诉处理办法", "P1", "处分申诉"),
    ("学位与论文", "学位授予工作细则", "P0", "学位条件/流程"),
    ("学位与论文", "学术不端行为处理办法", "P1", "学术规范"),
    ("奖助与资助", "勤工助学管理办法", "P1", "资助体系缺口"),
    ("奖助与资助", "学生资助政策汇编", "P1", "奖助贷整体口径"),
    ("纪律与申诉", "学生违纪处分管理办法（最新修订）", "P0", "已有旧版，需核对最新版"),
    ("宿舍与生活", "学生公寓管理办法（最新修订）", "P1", "住宿/调宿/退宿"),
    ("宿舍与生活", "学生宿舍消防安全管理规定", "P1", "消防/用电安全"),
    ("宿舍与生活", "校园商业/快递/外卖服务指南", "P2", "生活服务"),
    ("医疗与医保", "大学生医保政策与报销细则", "P1", "医保报销"),
    ("就业与档案", "毕业生就业管理办法", "P1", "就业手续"),
    ("就业与档案", "毕业生档案管理办法", "P1", "档案转递"),
    ("网络与校园卡", "校园卡管理办法（最新版）", "P0", "已有 2026 PDF，核对生效"),
    ("网络与校园卡", "校园网服务与收费办法", "P1", "网络服务"),
    ("图书馆与文体", "图书馆借阅与违规处理办法", "P1", "借阅规则"),
    ("图书馆与文体", "体育场馆管理办法", "P2", "场馆预约"),
    ("安全与户籍", "学生安全教育与管理办法", "P1", "安全/应急"),
    ("安全与户籍", "新生户口迁入与户籍办理细则", "P1", "户籍"),
    ("迎新与新生", "新生入学须知（最新版）", "P1", "报到/绿色通道"),
    ("国际交流", "学生出国（境）交流管理办法", "P2", "国际交流"),
    ("其他", "学生社团管理办法", "P2", "社团/活动"),
    ("其他", "创新创业与竞赛管理办法", "P2", "双创"),
    ("其他", "军训与国防教育管理办法", "P2", "军训"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT id,title,filename,file_ext,source_type,source_url,tags,status,page_count,char_count "
        "FROM documents ORDER BY title"
    ).fetchall()
    con.close()

    documents = []
    counts = Counter()
    for row in rows:
        doc_id, title, filename, ext, source_type, source_url, tags, status, pages, chars = row
        category = classify(title or "", tags or "")
        counts[category] += 1
        documents.append(
            {
                "id": doc_id,
                "title": title,
                "filename": filename,
                "file_ext": ext,
                "source_type": source_type,
                "source_url": source_url or "",
                "tags": tags,
                "status": status,
                "page_count": pages,
                "char_count": chars,
                "category": category,
                "in_dataset": True,
            }
        )

    targets = [
        {"category": c, "title": t, "priority": p, "reason": r, "status": "todo", "in_dataset": False}
        for c, t, p, r in TARGETS
    ]
    payload = {
        "version": "2026.1-draft",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": "M2 来源盘点：documents 为当前已入库；targets 为 v0.2.0 待补官方来源。",
        "documents": documents,
        "targets": targets,
    }
    (OUT / "sources.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    categories = [name for name, _ in CATEGORIES] + ["其他"]
    with (OUT / "coverage.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["category", "current_documents", "target_todo", "status"])
        for category in categories:
            todo = sum(1 for item in targets if item["category"] == category)
            status = "covered" if counts[category] and todo == 0 else ("partial" if counts[category] else "missing")
            writer.writerow([category, counts[category], todo, status])

    lines = [
        "# NPU 学生手册数据集来源盘点（M2）",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 已入库文档：{len(documents)}",
        f"- 待补目标：{len(targets)}",
        "",
        "## 当前文档分类",
        "",
        "| 类别 | 已入库 | 待补 |",
        "|---|---:|---:|",
    ]
    for category in categories:
        todo = sum(1 for item in targets if item["category"] == category)
        lines.append(f"| {category} | {counts[category]} | {todo} |")
    lines += ["", "## 待补来源（P0/P1）", "", "| 优先级 | 类别 | 来源 |", "|---|---|---|"]
    for item in targets:
        if item["priority"] in {"P0", "P1"}:
            lines.append(f"| {item['priority']} | {item['category']} | {item['title']} |")
    (OUT / "INVENTORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"documents": len(documents), "targets": len(targets), "by_category": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
