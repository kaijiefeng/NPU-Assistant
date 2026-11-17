#!/usr/bin/env python3
"""生成多类型文件测试包到 ``scan/测试文件包/``，用于手动测试所有文件相关功能。

覆盖：全部支持格式（md/txt/pdf/docx/doc/xlsx/csv/json/html）、编码与大小边界
（UTF-16 / 大文件 / 空文件 / 不支持类型 / 临时文件）、隐私场景（脱敏 / 名单拦截）、
递归深度。配合「知识库 → 内容管理 → 收录资料 → 扫描并收录 / 更新」使用，
包内附 ``README-测试清单.md``（人工测试逐项核对表）。

用法::

    .\\.venv\\Scripts\\python.exe scripts\\make_test_pack.py            # 生成（默认清空重建）
    .\\.venv\\Scripts\\python.exe scripts\\make_test_pack.py --no-clean  # 追加而不清空
    .\\.venv\\Scripts\\python.exe scripts\\make_test_pack.py --out D:\\tmp\\pack
"""
from __future__ import annotations

import argparse
import base64
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "scan" / "测试文件包"
FIXTURE_DOC = ROOT / "backend" / "tests" / "fixtures" / "campus" / "西工大-在校学习基本情况表.doc"

# 1x1 透明 PNG（合法图片字节，验证「不支持类型被扫描跳过」）
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

LOREM = "选课调整说明，请同学们按通知时间完成确认。"


def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)


def make_pdf(path: Path) -> None:
    """两页中文 PDF（验证多页解析与 page_count）。"""
    import fitz

    doc = fitz.open()
    for page_no in (1, 2):
        page = doc.new_page()
        page.insert_text(
            (50, 80),
            f"学生手册节选（第 {page_no} 页）",
            fontname="china-s",
            fontsize=16,
        )
        page.insert_text(
            (50, 120),
            "奖学金评审每学年组织一次，按综合测评成绩排序。" * 3,
            fontname="china-s",
            fontsize=11,
        )
        page.insert_text(
            (50, 150),
            f"本页用于测试 PDF 解析与页码统计（{page_no}/2）。" * 2,
            fontname="china-s",
            fontsize=11,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()


def make_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("奖学金评审办法", 1)
    doc.add_paragraph("评审办法说明，按综合测评结果排序。" * 30)
    doc.add_heading("材料清单", 2)
    doc.add_paragraph("申请表、成绩单、综合测评证明。")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "成绩表"
    ws.append(["学号", "姓名", "综合测评", "奖学金等级"])
    ws.append(["20230001", "张三", 92.5, "一等"])
    ws.append(["20230002", "李四", 88.0, "二等"])
    ws2 = wb.create_sheet("说明")
    ws2["A1"] = "本表用于测试 Excel 多 sheet 解析（每个 sheet 名作为章节标题）。"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def build(out: Path) -> None:
    pack01 = out / "01-常规格式"
    pack02 = out / "02-编码与边界"
    pack03 = out / "03-隐私场景"
    pack04 = out / "04-深层目录" / "二级" / "三级"

    # --- 01 常规格式：全部应解析成功 ---
    write_text(pack01 / "奖学金申请指南.md", "# 奖学金申请指南\n\n申请条件与材料清单。\n\n" + LOREM * 20)
    write_text(pack01 / "校园网使用说明.txt", "校园网使用说明\n\n认证地址与常见故障处理。\n\n" + LOREM * 20)
    make_pdf(pack01 / "学生手册节选.pdf")
    make_docx(pack01 / "奖学金评审办法.docx")
    if FIXTURE_DOC.is_file():
        shutil.copyfile(FIXTURE_DOC, pack01 / "在校学习基本情况表.doc")
    make_xlsx(pack01 / "学生成绩统计.xlsx")
    write_text(
        pack01 / "联系方式示例.csv",
        "部门,电话,办公地点\n学生工作办公室,0000-0000000,行政楼201\n教务办公室,0000-0000001,行政楼305\n",
    )
    write_text(
        pack01 / "服务配置示例.json",
        '{\n  "服务名称": "校园服务台",\n  "工作日": ["周一", "周二", "周三"],\n  "响应时限小时": 24\n}\n',
        )
    write_text(
        pack01 / "常见问题.html",
        "<html><head><meta charset='utf-8'><title>常见问题</title></head><body>"
        "<h1>校园卡常见问题</h1><p>挂失与补办流程说明。</p>" + LOREM * 15 + "</body></html>",
    )

    # --- 02 编码与边界 ---
    write_text(pack02 / "UTF16编码说明.txt", "UTF-16 编码测试：本文件应被正确解码为中文而非乱码。\n\n" + LOREM * 10, encoding="utf-16")
    write_text(pack02 / "UTF16表格.csv", "项目,说明\n借阅期限,30 天\n续借次数,2 次\n", encoding="utf-16")
    big = ("大数据文件内容段落，用于测试大文件上传与后台任务队列。" * 40 + "\n") * 700  # ≈3MB
    write_text(pack02 / "大文件测试.txt", big)
    (pack02 / "空文件.txt").parent.mkdir(parents=True, exist_ok=True)
    (pack02 / "空文件.txt").write_bytes(b"")  # 0 字节：扫描应跳过
    (pack02 / "图片示例.png").write_bytes(TINY_PNG)  # 不支持类型：扫描应跳过
    write_text(pack02 / "~$临时文档.md", "临时文件内容，扫描应跳过（~$ 前缀）。")

    # --- 03 隐私场景 ---
    write_text(
        pack03 / "资助政策正文含联系方式.md",
        "# 临时困难补助政策\n\n如需咨询请联系辅导员，电话 138-0000-0000，"
        "邮箱 counselor@example.com。\n\n" + LOREM * 20,
    )
    write_text(
        pack03 / "学生联系人名单.md",
        "# 学生联系人名单\n\n姓名 学号 电话\n张三 2023000100001 13800000001\n"
        "李四 2023000100002 13800000002\n王五 2023000100003 13800000003\n",
    )

    # --- 04 递归深度 ---
    write_text(pack04 / "院系通知.md", "# 院系通知（三层目录）\n\n" + LOREM * 20)
    write_text(
        out / "04-深层目录" / "二级" / "三级" / "四级" / "五级" / "超深层通知.md",
        "# 超出深度上限（第 5 层）\n\n默认 NPU_SCAN_MAX_DEPTH=4，第 5 层文件应被扫描跳过。\n",
    )

    # --- 测试清单 ---
    write_text(
        out / "README-测试清单.md",
        TEST_CHECKLIST,
    )


TEST_CHECKLIST = """# 文件功能人工测试清单

> 配套目录：`01-常规格式` / `02-编码与边界` / `03-隐私场景` / `04-深层目录`。
> 前提：服务已启动，且「设置 → 模型」默认模型可用（mock 也能编译，走抽取式降级）。

## 一、扫描目录（一键编译 / 更新）

- [ ] 收录资料 → 「扫描并收录 / 更新」，路径留空 → 默认扫描本包目录；提示发现 N 个可收录文件
- [ ] **跳过项**：空文件、图片示例.png、~$临时文档.md、五级/超深层通知.md 不被收录
- [ ] **收录项**：共 16 个可见（01 包 9 个 + UTF16×2 + 大文件 + 03 隐私×2 + 院系通知 + 本清单）
- [ ] 任务完成后 toast 区分：新收录 / 更新 / 跳过 / 失败
- [ ] **原样重扫（skip 模式）**：全部 skipped(unchanged)，无重复词条
- [ ] **修改 01/奖学金申请指南.md 后重扫**：skip 模式 → skipped(name-exists)；update 模式 → updated 1 且词条内容变为"2026 修订"（可先手动改标题加字样）
- [ ] 名单文档（03/学生联系人名单.md）：源文件状态 failed，提示名单类不收录，**包内该文件被删除**（隐私规则）
- [ ] 脱敏验证：03/资助政策正文含联系方式.md 的词条正文不含明文手机号/邮箱

## 二、界面上传

- [ ] 多选上传 01 包全部文件；拖拽 02/UTF16编码说明.txt 到拖拽区
- [ ] 上传进度条与后台队列展示；大文件测试.txt 走异步任务不阻塞页面
- [ ] 上传空文件.txt → 拒绝；上传 png → 拒绝（格式校验）

## 三、解析结果抽查（词条内容）

- [ ] md/txt：标题与正文完整，无乱码
- [ ] PDF：词条含两页内容；源文件页码标注正常
- [ ] docx：两个章节（评审办法/材料清单）
- [ ] doc（真实 Word 97-2003 样例）：解析出"在校学习基本情况"表格内容
- [ ] xlsx：两个 sheet（成绩表/说明）各自成章节
- [ ] csv（UTF-8 与 UTF-16）：表格行可见，无乱码
- [ ] json：字段结构可读；html：正文提取（无标签残留）

## 四、源文件管理

- [ ] 源文件列表可见上述全部文档，状态「已就绪」
- [ ] 「下载」原文件（如 学生手册节选.pdf）能打开且为生成内容
- [ ] 选一篇点「重新编译」→ 词条 version 变化、无重复词条
- [ ] 删除一篇独占文档 → 词条同步清理；源文件列表计数恢复

## 五、其他入口（对照）

- [ ] 机器人页 → 控制台通道模拟对话，提问与包内文件相关的问题，回答带引用
- [ ] （可选）把包路径发给配置了 admin_user_ids 的 QQ/微信机器人 → 自动入库
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成文件功能测试包")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 scan/测试文件包）")
    parser.add_argument("--no-clean", action="store_true", help="不清空输出目录，直接追加")
    args = parser.parse_args()

    out = Path(args.out)
    if out.exists() and not args.no_clean:
        shutil.rmtree(out)
    build(out)

    # 统计
    files = [p for p in out.rglob("*") if p.is_file()]
    print(f"测试包已生成：{out}")
    print(f"文件总数：{len(files)}（含测试清单）")
    for p in sorted(files):
        print(f"  {p.relative_to(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
