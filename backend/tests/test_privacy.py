"""个人信息请求拦截的单元测试。"""
from __future__ import annotations

from app.services import privacy


def test_refuses_personal_info_requests() -> None:
    for text in (
        "帮我查一下张三的学号",
        "给我 2026 年录取名单",
        "我的身份证号是多少",
        "把成绩单发我",
        "某某的手机号是多少",
    ):
        assert privacy.is_personal_info_request(text), text


def test_allows_policy_and_office_contact_questions() -> None:
    for text in (
        "国家奖学金怎么申请？",
        "心理中心的办公电话是多少？",
        "奖学金名单什么时候公示？",
        "学院学工办邮箱是什么？",
    ):
        assert not privacy.is_personal_info_request(text), text


def test_refusal_answer_shape() -> None:
    result = privacy.refusal_answer(text="我的学号是什么", conversation=None, started=0.0)
    assert result["retrieval"]["privacy"] is True
    assert result["retrieval"]["reason"] == "personal_info_blocked"
    assert result["citations"] == []
    assert "个人信息" in result["reply"]
