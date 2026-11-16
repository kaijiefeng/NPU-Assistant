"""个人信息保护：命中个人信息请求时直接拒绝，不检索、不调用模型。

与危机升级一样，这是确定性规则，优先于模型与检索；办公邮箱/办公电话属于公开
办公联系方式，不在拦截范围内。
"""
from __future__ import annotations

import time

from .. import db

# 明确指向“个人信息/个人数据”的请求词；不含“电话/邮箱/名单”等单独词，
# 避免把“心理中心电话”“奖学金名单什么时候公示”这类政策问题误伤。
PERSONAL_INFO_TERMS = (
    "身份证",
    "学号",
    "成绩单",
    "成绩排名",
    "录取名单",
    "考生名单",
    "合格名单",
    "拟录取名单",
    "学生名单",
    "花名册",
    "家庭住址",
    "家庭地址",
    "个人邮箱",
    "私人邮箱",
    "报名号",
    "准考证号",
    "手机号",
    "个人信息",
)

REFUSAL_REPLY = (
    "抱歉，我不能提供或查询任何个人信息，包括姓名、学号、身份证号、手机号、"
    "个人邮箱、家庭住址、成绩/录取名单等。\n\n"
    "如需查询本人信息，请通过学校官方系统（教务/学工/研究生系统）或联系辅导员、"
    "学院学工办等官方渠道办理。公开的办公邮箱和办公电话可以在知识库范围内提供。"
)


def is_personal_info_request(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return any(term in value for term in PERSONAL_INFO_TERMS)


def refusal_answer(
    *,
    text: str,
    conversation: dict | None,
    started: float,
    channel: str = "web",
) -> dict:
    """返回与 ``counselor.answer`` 一致的结构，并落库两条消息。"""
    conversation_id = str((conversation or {}).get("id") or "")
    message_id = ""
    model = "隐私保护规则"
    if conversation_id:
        try:
            db.add_message(conversation_id, "user", text)
            saved = db.add_message(
                conversation_id,
                "assistant",
                REFUSAL_REPLY,
                citations=[],
                model=model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                retrieval={
                    "wiki_used": False,
                    "remote_used": False,
                    "complete": False,
                    "reason": "personal_info_blocked",
                    "privacy": True,
                },
                fallback=True,
                navigation_method="privacy",
            )
            message_id = str((saved or {}).get("id") or "")
        except Exception:  # pragma: no cover - 落库失败不影响拒绝回答
            pass
    return {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "reply": REFUSAL_REPLY,
        "citations": [],
        "model": model,
        "provider_type": "none",
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "fallback": True,
        "sources_used": 0,
        "navigation": {"method": "privacy", "pages": []},
        "retrieval": {
            "wiki_used": False,
            "remote_used": False,
            "complete": False,
            "reason": "personal_info_blocked",
            "privacy": True,
        },
    }

