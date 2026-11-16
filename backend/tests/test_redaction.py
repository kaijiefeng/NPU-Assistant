"""个人信息脱敏单元测试。"""
from __future__ import annotations

from app.services import redaction


def test_redacts_personal_info_and_keeps_office_info() -> None:
    text = (
        "联系人王淼，手机 13812345678，邮箱 wangmiao@126.com；"
        "办公邮箱 miao@nwpu.edu.cn，办公电话 029-88430028，身份证 110101199001011234。"
    )
    clean = redaction.redact_text(text)
    assert "13812345678" not in clean
    assert "110101199001011234" not in clean
    assert "wangmiao@126.com" not in clean
    assert "1**********" in clean
    assert "***@126.com" in clean
    assert "miao@nwpu.edu.cn" in clean
    assert "029-88430028" in clean
    assert "王淼" in clean


def test_list_document_detection() -> None:
    assert redaction.is_list_document("2026年拟录取名单")
    assert redaction.is_list_document("学院通知", "姓名 学号 报名号\n张三 2023001 171069901000018")
    assert not redaction.is_list_document("奖学金申请条件", "学生可提交申请表，姓名与学号仅用于身份核验。")


def test_redacts_long_numbers() -> None:
    clean = redaction.redact_text("账号 611301130018000866283 请勿外传")
    assert "611301130018000866283" not in clean
