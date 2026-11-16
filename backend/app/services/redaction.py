"""个人信息脱敏：信息源解析后、入库/编译前调用。

保留公开且必要的办公信息（办公邮箱 `@nwpu.edu.cn`、办公座机 `029-...`、办公人员姓名），
其余可识别到个人的信息一律打码：手机号、身份证号、个人邮箱、16–19 位长数字（银行账号/报名号等）。

名单/成绩类文档不进入知识库：由 :func:`is_list_document` 在入库/编译前拦截。
"""
from __future__ import annotations

import re
from collections import Counter

MOBILE = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")
MOBILE_SPACED = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d(?:[\s-]?\d{4}){2}(?![0-9A-Za-z])")
ID_CARD = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
ID_SPACED = re.compile(r"(?<![0-9A-Za-z])\d{6}(?:[\s-]?\d{4}){2}[\s-]?\d{3}[\dXx](?![0-9A-Za-z])")
EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
LONG_NUMBER = re.compile(r"(?<![0-9A-Za-z])\d{16,24}(?![0-9A-Za-z])")

LIST_TITLE = re.compile(r"(名单|录取|拟录取|合格考生|考生名单|成绩单|花名册|复试|推免)")


def redact_with_stats(text: str) -> tuple[str, Counter]:
    """返回 (脱敏文本, 各类型打码次数)。"""
    counts: Counter = Counter()

    def mobile(_match: re.Match) -> str:
        counts["mobile"] += 1
        return "1**********"

    def idcard(_match: re.Match) -> str:
        counts["id_card"] += 1
        return "******************"

    def email(match: re.Match) -> str:
        domain = match.group(2)
        if domain.lower().endswith("nwpu.edu.cn"):
            return match.group(0)
        counts["personal_email"] += 1
        return f"***@{domain}"

    def long_number(_match: re.Match) -> str:
        counts["long_number"] += 1
        return "***"

    value = str(text or "")
    value = MOBILE_SPACED.sub(mobile, value)
    value = MOBILE.sub(mobile, value)
    value = ID_SPACED.sub(idcard, value)
    value = ID_CARD.sub(idcard, value)
    value = EMAIL.sub(email, value)
    value = LONG_NUMBER.sub(long_number, value)
    return value, counts


def redact_text(text: str) -> str:
    """脱敏后的文本；无个人信息时原样返回。"""
    return redact_with_stats(text)[0]


def is_list_document(title: str, text: str = "") -> bool:
    """名单/成绩类文档：标题命中，或正文含“姓名 + 报名号/学号/准考证号/身份证”表格。"""
    if LIST_TITLE.search(str(title or "")):
        return True
    body = str(text or "")
    if "姓名" not in body or not re.search(r"\d{12,}", body):
        return False
    return any(key in body for key in ("报名号", "准考证号", "考生号", "学号"))
