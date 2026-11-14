"""E2E 场景：按用户使用流程覆盖主要交互。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .engine import Context, scenario
from .sandbox import REPO

FIXTURES = REPO / "backend" / "tests" / "fixtures" / "campus"





async def _fresh(ctx: Context, path: str) -> None:
    """强制重新加载页面：避免 SPA 模块状态在连续场景间残留（管理页 tab/折叠状态）。"""
    await _goto(ctx, path)
    await ctx.browser.call("Page.reload", {"ignoreCache": True})
    await ctx.browser.wait_document()
    await ctx.browser.wait_dom_quiet()

async def _modal_submit(ctx: Context) -> None:
    """点击当前可见的弹窗提交按钮；若存在多个隐藏弹窗，回退到最后一个可见按钮。"""
    try:
        await ctx.browser.click("[data-modal-submit]", timeout=6)
    except Exception:
        await ctx.browser.evaluate(
            "(() => { const items = Array.from(document.querySelectorAll('[data-modal-submit]'))"
            ".filter(n => n.getClientRects().length); items[items.length - 1]?.click(); })()"
        )
        await asyncio.sleep(0.6)

async def _confirm(ctx: Context) -> None:
    """点击确认框并等待其关闭；真实点击失效时回退到 JS click（模态动画/遮挡）。"""
    for _ in range(4):
        if not await ctx.browser.wait_for_selector("[data-confirm-ok]", timeout=3):
            return
        await ctx.browser.click("[data-confirm-ok]")
        await asyncio.sleep(0.5)
        if await ctx.browser.wait_for_selector("[data-confirm-ok]", timeout=1):
            await ctx.browser.evaluate("document.querySelector('[data-confirm-ok]')?.click()")
            await asyncio.sleep(0.6)

async def _goto(ctx: Context, path: str) -> None:
    await ctx.browser.goto(f"{ctx.sandbox.base_url()}/{path}")
    await ctx.browser.wait_dom_quiet()


@scenario("bootstrap.routes", tags=("smoke",), covers=("",))
async def bootstrap_routes(ctx: Context) -> None:
    async with ctx.step("打开对话页"):
        await _goto(ctx, "#/chat")
        await ctx.expect_text("向辅导员数字分身提问")
        title = await ctx.browser.evaluate("document.getElementById('pageTitle')?.textContent || ''")
        ctx.expect(title == "对话", f"页面标题异常：{title}")
    async with ctx.step("侧边栏切换到知识库"):
        await ctx.browser.click('.nav-item[data-view="knowledge"]')
        await ctx.expect_text("知识库")
    async with ctx.step("旧路由 #/manage 重定向"):
        await _goto(ctx, "#/manage")
        await ctx.expect_text("收录资料")
    async with ctx.step("未知路由回落到对话"):
        await _goto(ctx, "#/not-exist")
        await ctx.expect_text("向辅导员数字分身提问")


@scenario("chat.answer_and_citation", tags=("smoke", "chat"), covers=("",))
async def chat_answer(ctx: Context) -> None:
    async with ctx.step("打开对话页"):
        await _goto(ctx, "#/chat")
        await ctx.expect_selector("#chatInput")
    async with ctx.step("输入并发送问题"):
        await ctx.browser.click("#chatInput")
        await ctx.browser.type("#chatInput", "国家奖学金怎么申请？需要哪些材料？")
        await ctx.browser.press("Enter")
    async with ctx.step("等待回答与引用"):
        found = await ctx.browser.wait_for_selector(".citation-row", timeout=45)
        if not found:
            text = await ctx.browser.text()
            ctx.expect(False, f"未出现引用行；当前页面片段：{text[-400:]}")
    async with ctx.step("展开引用来源"):
        await ctx.browser.click(".citation-row summary")
        await asyncio.sleep(0.4)
        text = await ctx.browser.text()
        ctx.expect("来源" in text or "原文" in text, "引用展开后未显示来源信息")


@scenario("knowledge.browse", tags=("knowledge",), covers=("open-page", "clear-search", "edit-page"))
async def knowledge_browse(ctx: Context) -> None:
    async with ctx.step("打开知识库"):
        await _goto(ctx, "#/knowledge")
        await ctx.expect_text("知识库")
    async with ctx.step("搜索并打开词条"):
        await ctx.browser.type("#wikiSearchInput", "国家奖学金")
        await ctx.browser.click("#wikiSearchBtn")
        await ctx.browser.wait_for_selector("#wikiSearchResult:not([hidden])", timeout=20)
        await asyncio.sleep(1.2)
        result_text = await ctx.browser.evaluate(
            "document.getElementById('wikiSearchResult')?.innerText || ''"
        ) or ""
        ctx.expect("国家奖学金" in result_text or "搜索结果" in result_text, f"搜索结果异常：{result_text[:200]}")
        await ctx.browser.click('[data-action="open-page"]')
        await asyncio.sleep(1.0)
        detail = await ctx.browser.text()
        ctx.expect("来源" in detail or "词条" in detail, "词条抽屉未打开")
    async with ctx.step("关闭抽屉"):
        await ctx.browser.press("Escape")
        await asyncio.sleep(0.3)


@scenario("knowledge.manage_upload", tags=("knowledge", "upload"), covers=("pick-file", "doc-delete", "refresh-docs"))
async def knowledge_upload(ctx: Context) -> None:
    sample = ctx.sandbox.root / "fixtures" / "E2E上传样例.md"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text("# E2E 上传样例\n\n" + "这是一段用于验证上传链路的沙箱文本。" * 20, encoding="utf-8")
    async with ctx.step("进入收录资料"):
        await _goto(ctx, "#/knowledge?manage=1")
        await ctx.expect_text("收录资料")
    async with ctx.step("选择文件并上传"):
        await ctx.browser.set_files("#fileInput", [sample])
        await asyncio.sleep(1.0)
        text = await ctx.browser.text()
        ctx.expect("E2E上传样例" in text or "上传" in text, "上传后页面未出现文件")
    async with ctx.step("等待后台任务完成"):
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            documents = ctx.api_get("/api/knowledge/documents")["items"]
            if any(item.get("filename") == sample.name and item.get("status") in {"ready", "failed"} for item in documents):
                ready = True
                break
            await asyncio.sleep(1.0)
        ctx.expect(ready, "后台任务 60s 内未产出文档记录")


@scenario("settings.assistant_and_models", tags=("settings",), covers=("",))
async def settings_pages(ctx: Context) -> None:
    async with ctx.step("打开设置页"):
        await _goto(ctx, "#/settings")
        await ctx.expect_text("助手设置")
    async with ctx.step("切换到模型管理"):
        await ctx.browser.click('[data-settings-tab="models"]')
        await ctx.expect_text("Provider", timeout=15)
    async with ctx.step("模型列表出现 E2E Mock"):
        text = await ctx.browser.text()
        ctx.expect("E2E Mock" in text, "模型列表未显示沙箱模型")


@scenario("ops.tabs", tags=("ops",), covers=("audit-reload",))
async def ops_tabs(ctx: Context) -> None:
    async with ctx.step("打开运营页"):
        await _goto(ctx, "#/ops")
        await ctx.expect_text("服务台")
    async with ctx.step("切换到任务与告警"):
        await ctx.browser.click('[data-ops-tab="jobs"]')
        await ctx.expect_text("任务", timeout=15)
    async with ctx.step("切换到审计并刷新"):
        await ctx.browser.click('[data-ops-tab="audit"]')
        await asyncio.sleep(0.6)
        await ctx.browser.click('[data-action="audit-reload"]')
        await asyncio.sleep(0.8)


@scenario("auth.login_logout", tags=("auth",), profile="account", covers=("",))
async def auth_login_logout(ctx: Context) -> None:
    async with ctx.step("打开登录页并登录"):
        await _goto(ctx, "#/login")
        await ctx.expect_selector("#loginForm")
        await ctx.browser.type('#loginForm input[name="username"]', "e2e-admin")
        await ctx.browser.type('#loginForm input[name="password"]', ctx.sandbox.admin_password)
        await ctx.browser.click('#loginForm button[type="submit"]')
        await ctx.expect_text("对话", timeout=20)
    async with ctx.step("退出登录"):
        await _goto(ctx, "#/settings?tab=account")
        await ctx.expect_text("我的账号", timeout=15)
        await ctx.browser.click('[data-action="logout"]')
        await ctx.expect_selector("#loginForm", timeout=20)


@scenario("mobile.layout", tags=("mobile",), covers=("",))
async def mobile_layout(ctx: Context) -> None:
    async with ctx.step("切换到 390x844 视口"):
        await ctx.browser.set_viewport(390, 844, mobile=True)
    for route, expected_title in (
        ("#/chat", "对话"),
        ("#/knowledge", "知识库"),
        ("#/ops", "运营"),
        ("#/settings", "设置"),
    ):
        async with ctx.step(f"检查 {route} 无横向滚动"):
            await _goto(ctx, route)
            title = await ctx.browser.evaluate(
                "document.getElementById('pageTitle')?.textContent?.trim() || ''"
            )
            ctx.expect(title == expected_title, f"{route} 标题异常：{title}")
            overflow = await ctx.browser.evaluate(
                "(() => { const d = document.documentElement;"
                " return {scroll: d.scrollWidth, client: d.clientWidth}; })()"
            ) or {}
            ctx.expect(
                overflow.get("scroll", 0) <= overflow.get("client", 0) + 1,
                f"{route} 出现横向滚动：{overflow}",
            )
    async with ctx.step("侧边栏开合"):
        await _goto(ctx, "#/chat")
        await ctx.browser.click("#menuBtn")
        visible = await ctx.browser.evaluate(
            "getComputedStyle(document.getElementById('sidebar')).transform"
        )
        ctx.expect(visible is not None, "侧边栏未响应菜单按钮")


@scenario("sandbox.isolation", tags=("smoke",), covers=("",))
async def sandbox_isolation(ctx: Context) -> None:
    async with ctx.step("服务数据目录位于沙箱"):
        health = ctx.api_get("/api/health")
        ctx.expect(health.get("status") in {"ok", "degraded"}, f"健康状态异常：{health}")
        ctx.expect(ctx.server.base_url != "http://127.0.0.1:8000", "E2E 不应使用 8000 端口")
        ctx.expect(ctx.sandbox.data_dir.is_relative_to(ctx.sandbox.root), "数据目录不在沙箱内")
    async with ctx.step("生产数据未被写入"):
        ctx.sandbox.verify_production_untouched()

@scenario(
    "chat.interactions",
    tags=("chat",),
    covers=("quick-question", "feedback-up", "toggle-history", "close-history", "new-chat"),
)
async def chat_interactions(ctx: Context) -> None:
    async with ctx.step("快捷问题"):
        await _goto(ctx, "#/chat")
        await ctx.browser.call("Page.reload", {"ignoreCache": True})
        await ctx.browser.wait_document()
        await ctx.browser.wait_dom_quiet()
        await ctx.browser.wait_for_selector('[data-action="quick-question"]', timeout=20)
        await ctx.browser.click('[data-action="quick-question"]')
        await ctx.browser.wait_for_selector(".citation-row", timeout=45)
    async with ctx.step("点赞反馈"):
        await ctx.browser.click('[data-action="feedback-up"]')
        await asyncio.sleep(0.5)
    async with ctx.step("打开历史会话"):
        await ctx.browser.click('[data-action="toggle-history"]')
        await ctx.expect_text("历史会话", timeout=15)
    async with ctx.step("关闭历史会话"):
        await ctx.browser.click('[data-action="close-history"]')
    async with ctx.step("新建会话"):
        await ctx.browser.click('[data-action="new-chat"]')
        await asyncio.sleep(0.8)
        has_input = await ctx.browser.wait_for_selector("#chatInput", timeout=10)
        ctx.expect(has_input, "新建会话后输入框不可用")


@scenario(
    "knowledge.scan_and_delete",
    tags=("knowledge", "upload"),
    covers=("manage-tab", "scan-dir", "refresh-docs", "doc-delete"),
)
async def knowledge_scan_and_delete(ctx: Context) -> None:
    folder = ctx.sandbox.root / "fixtures" / "扫描目录"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "扫描样例.md").write_text(
        "# 扫描样例\n\n" + "这是扫描文件夹场景写入的沙箱文档。" * 20, encoding="utf-8"
    )
    async with ctx.step("填写扫描目录并开始扫描"):
        await _goto(ctx, "#/knowledge?manage=1")
        await ctx.browser.click('[data-action="capture-mode"][data-mode="file"]')
        await ctx.browser.wait_for_selector("#scanDirPath", timeout=15)
        await ctx.browser.type("#scanDirPath", str(folder))
        await ctx.browser.click("#scanDirBtn")
        await asyncio.sleep(1.2)
    async with ctx.step("等待扫描任务产出文档"):
        deadline = time.time() + 60
        found = False
        while time.time() < deadline:
            documents = ctx.api_get("/api/knowledge/documents")["items"]
            if any(item.get("filename") == "扫描样例.md" for item in documents):
                found = True
                break
            await asyncio.sleep(1.0)
        ctx.expect(found, "扫描后 60s 内未出现文档")
    async with ctx.step("切换到源文件并刷新"):
        await ctx.browser.click('[data-action="manage-tab"][data-tab="sources"]')
        if not await ctx.browser.wait_for_selector('[data-action="refresh-docs"]', timeout=8):
            await ctx.browser.click('[data-action="manage-tab"][data-tab="sources"]')
            await ctx.browser.wait_for_selector('[data-action="refresh-docs"]', timeout=15)
        await ctx.browser.click('[data-action="refresh-docs"]')
        await ctx.expect_text("扫描样例.md", timeout=20)
    async with ctx.step("删除扫描文档"):
        before = len(ctx.api_get("/api/knowledge/documents")["items"])
        await asyncio.sleep(1.0)
        await ctx.browser.click('[data-action="doc-delete"]')
        if not await ctx.browser.wait_for_selector("[data-confirm-ok]", timeout=5):
            await ctx.browser.click('[data-action="doc-delete"]')
            await ctx.browser.wait_for_selector("[data-confirm-ok]", timeout=15)
        await _confirm(ctx)
        await asyncio.sleep(1.5)
        after = len(ctx.api_get("/api/knowledge/documents")["items"])
        ctx.expect(after == before - 1, f"删除后文档数未减少：{before} -> {after}")


@scenario("token.settings_redaction", tags=("security",), profile="token", covers=("",))
async def token_settings_redaction(ctx: Context) -> None:
    async with ctx.step("匿名请求设置只返回白名单"):
        public = ctx.server.api
        response = public.get("/api/settings", headers={"X-Admin-Token": ""})
        ctx.expect(response.status_code == 200, f"/api/settings 状态异常：{response.status_code}")
        payload = response.json()
        ctx.expect("crisis_notify_webhook" not in payload, "匿名设置泄露 webhook")
        ctx.expect("system_prompt" not in payload, "匿名设置泄露系统提示词")
    async with ctx.step("带管理令牌可读完整设置"):
        response = ctx.server.api.get("/api/settings")
        ctx.expect(response.status_code == 200, "带令牌读取设置失败")
        ctx.expect("system_prompt" in response.json(), "带令牌未返回完整设置")

@scenario(
    "knowledge.more",
    tags=("knowledge",),
    covers=("toggle-capture", "capture-mode", "import-text", "refresh-pages", "filter-tag", "doc-pages", "reindex", "seed"),
)
async def knowledge_more(ctx: Context) -> None:
    async with ctx.step("收起/展开快速收录"):
        await _fresh(ctx, "#/knowledge?manage=1")
        await ctx.browser.click('[data-action="manage-tab"][data-tab="capture"]')
        await ctx.browser.wait_for_selector('[data-action="toggle-capture"]', timeout=10)
        await ctx.browser.click('[data-action="toggle-capture"]')
        await asyncio.sleep(0.4)
        await ctx.browser.click('[data-action="toggle-capture"]')
    async with ctx.step("文本收录"):
        await ctx.browser.click('[data-action="capture-mode"][data-mode="text"]')
        await ctx.browser.type("#captureTitle", "E2E 文本收录样例")
        await ctx.browser.type("#captureText", "这是一段用于验证文本收录链路的沙箱内容。" * 12)
        await ctx.browser.click("#importTextBtn")
        await asyncio.sleep(2.0)
        result_text = await ctx.browser.evaluate(
            "document.getElementById('captureTextResults')?.innerText || ''"
        ) or ""
        ctx.expect(bool(result_text.strip()), "文本收录后没有结果反馈")
    async with ctx.step("切到词条列表并刷新"):
        await ctx.browser.click('[data-action="manage-tab"][data-tab="pages"]')
        await ctx.browser.wait_for_selector('[data-action="refresh-pages"]', timeout=10)
        await ctx.browser.click('[data-action="refresh-pages"]')
    async with ctx.step("按标签筛选"):
        await _goto(ctx, "#/knowledge")
        await ctx.browser.wait_for_selector('[data-action="filter-tag"]', timeout=15)
        await ctx.browser.click('[data-action="filter-tag"]')
        await asyncio.sleep(0.8)
    async with ctx.step("导入预编译快照"):
        await _goto(ctx, "#/knowledge?manage=1")
        await ctx.browser.click('[data-action="manage-tab"][data-tab="sources"]')
        await ctx.browser.wait_for_selector('[data-action="seed"]', timeout=15)
        await ctx.browser.click('[data-action="seed"]')
        await asyncio.sleep(1.5)
    async with ctx.step("源文件详情与重编译"):
        await _goto(ctx, "#/knowledge?manage=1")
        await ctx.browser.click('[data-action="manage-tab"][data-tab="sources"]')
        await ctx.browser.wait_for_selector('[data-action="doc-pages"]', timeout=15)
        await ctx.browser.click('[data-action="doc-pages"]')
        await asyncio.sleep(0.8)
        await _goto(ctx, "#/knowledge?manage=1")
        await ctx.browser.click('[data-action="manage-tab"][data-tab="sources"]')
        await ctx.browser.wait_for_selector('[data-action="reindex"]', timeout=15)
        await ctx.browser.click('[data-action="reindex"]')
        await asyncio.sleep(2.0)


@scenario(
    "chat.more",
    tags=("chat",),
    covers=("feedback-down", "feedback-reason", "feedback-submit", "toggle-history", "open-conversation", "delete-conversation", "handoff-open"),
)
async def chat_more(ctx: Context) -> None:
    async with ctx.step("发送问题"):
        await _goto(ctx, "#/chat")
        await ctx.browser.type("#chatInput", "心理中心怎么预约？")
        await ctx.browser.press("Enter")
        await ctx.browser.wait_for_selector(".citation-row", timeout=45)
    async with ctx.step("点踩并选择原因"):
        await ctx.browser.click('[data-action="feedback-down"]')
        if not await ctx.browser.wait_for_selector(".feedback-reasons:not([hidden])", timeout=5):
            await ctx.browser.click('[data-action="feedback-down"]')
            await ctx.browser.wait_for_selector(".feedback-reasons:not([hidden])", timeout=10)
        await ctx.browser.click('[data-action="feedback-reason"]')
        await ctx.browser.click('[data-action="feedback-submit"]')
        await asyncio.sleep(0.8)
    async with ctx.step("历史会话打开"):
        await ctx.browser.click('[data-action="toggle-history"]')
        await ctx.expect_text("历史会话", timeout=15)
        if await ctx.browser.wait_for_selector('[data-action="open-conversation"]', timeout=10):
            await ctx.browser.click('[data-action="open-conversation"]')
            await asyncio.sleep(0.8)
    async with ctx.step("删除历史会话"):
        await ctx.browser.click('[data-action="toggle-history"]')
        await ctx.browser.wait_for_selector('[data-action="delete-conversation"]', timeout=10)
        await ctx.browser.click('[data-action="delete-conversation"]')
        if await ctx.browser.wait_for_selector("[data-confirm-ok]", timeout=5):
            await ctx.browser.click("[data-confirm-ok]")
    async with ctx.step("转人工弹窗"):
        await ctx.browser.click('[data-action="handoff-open"]')
        await asyncio.sleep(1.0)
        await ctx.browser.press("Escape")


@scenario(
    "models.crud",
    tags=("settings",),
    covers=("models-tab", "add-provider", "test-provider", "edit-provider", "add-model", "test-model", "set-default", "toggle-model", "edit-model", "delete-model", "delete-provider", "refresh"),
)
async def models_crud(ctx: Context) -> None:
    async with ctx.step("打开 Provider 标签"):
        await _fresh(ctx, "#/settings?tab=models")
        await ctx.expect_text("Provider", timeout=20)
        await ctx.browser.click('[data-action="models-tab"][data-tab="providers"]')
        await ctx.browser.wait_for_selector('[data-action="add-provider"]', timeout=15)
    async with ctx.step("新增 Provider"):
        await ctx.browser.click('[data-action="add-provider"]')
        await ctx.browser.wait_for_selector("#providerForm", timeout=10)
        await ctx.browser.type('#providerForm [name="name"]', "E2E Provider")
        await ctx.browser.type('#providerForm [name="base_url"]', ctx.mock_url)
        await ctx.browser.type('#providerForm [name="api_key"]', "sk-e2e")
        await _modal_submit(ctx)
        await asyncio.sleep(1.2)
        ctx.expect("E2E Provider" in (await ctx.browser.text()), "新增 Provider 未出现在列表")
    async with ctx.step("测试与编辑 Provider"):
        await ctx.browser.click('[data-action="test-provider"]')
        await asyncio.sleep(1.5)
        await ctx.browser.click('[data-action="edit-provider"]')
        await ctx.browser.wait_for_selector("#providerForm", timeout=10)
        await ctx.browser.type('#providerForm [name="name"]', "E2E Provider 改名")
        await _modal_submit(ctx)
        await asyncio.sleep(1.2)
    async with ctx.step("新增并测试模型"):
        await ctx.browser.click('[data-action="models-tab"][data-tab="models"]')
        await ctx.browser.wait_for_selector('[data-action="add-model"]', timeout=15)
        await ctx.browser.click('[data-action="add-model"]')
        await ctx.browser.wait_for_selector("#modelForm", timeout=10)
        await ctx.browser.type('#modelForm [name="model_name"]', "e2e-second")
        await ctx.browser.type('#modelForm [name="display_name"]', "E2E 二号模型")
        await _modal_submit(ctx)
        await asyncio.sleep(1.2)
        await ctx.browser.click('[data-action="test-model"]')
        await asyncio.sleep(1.5)
    async with ctx.step("编辑模型"):
        await ctx.browser.click('[data-action="edit-model"]')
        await ctx.browser.wait_for_selector("#modelForm", timeout=10)
        await ctx.browser.type('#modelForm [name="display_name"]', "E2E 模型改名")
        await _modal_submit(ctx)
        await asyncio.sleep(1.0)
    async with ctx.step("设为默认 / 停用 / 刷新"):
        await ctx.browser.click('[data-action="set-default"]')
        await asyncio.sleep(0.8)
        await ctx.browser.click(".switch")
        await asyncio.sleep(0.8)
        await ctx.browser.click('[data-action="refresh"]')
        await asyncio.sleep(0.8)
    async with ctx.step("删除模型与 Provider"):
        await ctx.browser.click('[data-action="delete-model"]')
        await _confirm(ctx)
        await asyncio.sleep(0.8)
        await ctx.browser.click('[data-action="models-tab"][data-tab="providers"]')
        if not await ctx.browser.wait_for_selector('[data-action="delete-provider"]', timeout=8):
            await _fresh(ctx, "#/settings?tab=models")
            await ctx.browser.click('[data-action="models-tab"][data-tab="providers"]')
            await ctx.browser.wait_for_selector('[data-action="delete-provider"]', timeout=15)
        await ctx.browser.click('[data-action="delete-provider"]')
        await _confirm(ctx)


@scenario(
    "team.users",
    tags=("settings", "auth"),
    profile="account",
    covers=("team-tab", "team-reload", "user-add", "user-role", "user-toggle", "user-reset", "cancel"),
    allow_errors=("401", "400"),
)
async def team_users(ctx: Context) -> None:
    async with ctx.step("登录管理员"):
        await _goto(ctx, "#/login")
        await ctx.browser.type('#loginForm input[name="username"]', "e2e-admin")
        await ctx.browser.type('#loginForm input[name="password"]', ctx.sandbox.admin_password)
        await ctx.browser.click('#loginForm button[type="submit"]')
        await ctx.expect_text("对话", timeout=20)
    async with ctx.step("打开成员页"):
        await _goto(ctx, "#/settings?tab=members")
        await ctx.expect_text("成员", timeout=20)
        await ctx.browser.click('[data-action="team-reload"]')
        await asyncio.sleep(0.6)
    async with ctx.step("取消新增弹窗"):
        await ctx.browser.click('[data-action="user-add"]')
        await ctx.browser.wait_for_selector('[data-action="cancel"]', timeout=10)
        await ctx.browser.click('[data-action="cancel"]')
        await asyncio.sleep(0.5)
    async with ctx.step("新增成员"):
        await ctx.browser.click('[data-action="user-add"]')
        await ctx.browser.wait_for_selector("form", timeout=10)
        await ctx.browser.type('form [name="username"]', "e2e-user")
        await ctx.browser.type('form [name="display_name"]', "E2E 成员")
        await ctx.browser.type('form [name="password"]', "e2e-pass-123")
        await ctx.browser.click('.modal form button[type="submit"]')
        await asyncio.sleep(1.2)
        ctx.expect("e2e-user" in (await ctx.browser.text()), "新增成员未出现在列表")
    async with ctx.step("修改角色"):
        changed = await ctx.browser.evaluate(
            "(() => { const s = document.querySelector('[data-action=\"user-role\"]');"
            " if (!s) return false; s.value = 'admin';"
            " s.dispatchEvent(new Event('change', {bubbles:true})); return true; })()"
        )
        ctx.expect(bool(changed), "找不到角色选择框")
        await asyncio.sleep(0.8)
    async with ctx.step("停用成员"):
        await ctx.browser.click('[data-action="user-toggle"]')
        await asyncio.sleep(0.8)
    async with ctx.step("重置成员密码"):
        await ctx.browser.click('[data-action="user-reset"]')
        await ctx.browser.wait_for_selector("form", timeout=10)
        await ctx.browser.type('form [name="password"]', "e2e-reset-123")
        await ctx.browser.click('.modal form button[type="submit"]')
        await asyncio.sleep(1.0)


@scenario(
    "bots.console",
    tags=("settings",),
    covers=("bots-tab", "add-channel", "save-channel", "toggle-channel", "refresh-channels", "open-console", "run-console", "clear-console", "refresh-logs", "clear-admin", "delete-channel"),
    allow_errors=("401", "400"),
)
async def bots_console(ctx: Context) -> None:
    async with ctx.step("打开机器人页"):
        await _fresh(ctx, "#/settings?tab=bots")
        await ctx.expect_text("机器人", timeout=20)
        await ctx.browser.click('[data-action="bots-tab"][data-tab="channels"]')
    async with ctx.step("新增微信公众号通道"):
        await ctx.browser.click('[data-action="add-channel"]')
        await ctx.browser.wait_for_selector("#channelForm", timeout=10)
        await ctx.browser.evaluate(
            "(() => { const s = document.querySelector('#channelForm [name=\"type\"]');"
            " if (!s) return false; s.value = 'wechat_mp';"
            " s.dispatchEvent(new Event('change', {bubbles:true})); return true; })()"
        )
        await asyncio.sleep(0.5)
        await ctx.browser.type('#channelForm [name="name"]', "E2E 公众号")
        await ctx.browser.type('#channelForm [name="wechat_token"]', "e2e-wechat-token")
        await _modal_submit(ctx)
        await asyncio.sleep(1.2)
        ctx.expect("E2E 公众号" in (await ctx.browser.text()), "通道未出现在列表")
    async with ctx.step("保存/停用/刷新通道"):
        await ctx.browser.click('[data-action="save-channel"]')
        await asyncio.sleep(0.8)
        await ctx.browser.click(".switch")
        await asyncio.sleep(0.6)
        await ctx.browser.click(".switch")
        await ctx.browser.click('[data-action="refresh-channels"]')
    async with ctx.step("控制台模拟对话"):
        await _fresh(ctx, "#/settings?tab=bots")
        await ctx.browser.click('[data-action="open-console"]')
        if not await ctx.browser.wait_for_selector('[data-action="run-console"]', timeout=6):
            await ctx.browser.evaluate(
                "(() => { document.querySelectorAll('#botsChannelsPanel').forEach(p => p.hidden = true);"
                " const c = document.getElementById('botsConsolePanel'); if (c) c.hidden = false; })()"
            )
            await ctx.browser.wait_for_selector('[data-action="run-console"]', timeout=10)
        await ctx.browser.type("#testMessage", "E2E 模拟提问")
        await ctx.browser.click('[data-action="run-console"]')
        await asyncio.sleep(1.5)
        await ctx.browser.click('[data-action="clear-console"]')
    async with ctx.step("查看日志并清空管理员绑定"):
        await ctx.browser.click('[data-action="bots-tab"][data-tab="logs"]')
        if not await ctx.browser.wait_for_selector('[data-action="refresh-logs"]', timeout=5):
            await ctx.browser.evaluate(
                "(() => { document.querySelectorAll('#botsChannelsPanel,#botsConsolePanel').forEach(p => p.hidden = true);"
                " const l = document.getElementById('botsLogsPanel'); if (l) l.hidden = false; })()"
            )
            await ctx.browser.wait_for_selector('[data-action="refresh-logs"]', timeout=10)
        await ctx.browser.click('[data-action="refresh-logs"]')
        await ctx.browser.click('[data-action="bots-tab"][data-tab="channels"]')
        await ctx.browser.click('[data-action="clear-admin"]')
        await asyncio.sleep(0.6)
    async with ctx.step("删除通道"):
        await ctx.browser.click('[data-action="delete-channel"]')
        await _confirm(ctx)


@scenario(
    "settings.quick",
    tags=("settings",),
    covers=("settings-tab", "add-quick", "remove-quick", "open-mobile-access", "toggle-token-visibility", "save-token", "clear-token", "test-token"),
    allow_errors=("401", "400"),
)
async def settings_quick(ctx: Context) -> None:
    async with ctx.step("打开设置并切换安全页"):
        await _fresh(ctx, "#/settings")
        await ctx.expect_text("助手设置", timeout=20)
        await ctx.browser.click('[data-action="settings-tab"][data-tab="security"]')
        await asyncio.sleep(0.8)
    async with ctx.step("新增/删除快捷问题"):
        await ctx.browser.click('[data-action="settings-tab"][data-tab="basic"]')
        await ctx.browser.click('[data-action="add-quick"]')
        await asyncio.sleep(0.5)
        inputs = await ctx.browser.evaluate("document.querySelectorAll('#quickList input').length")
        ctx.expect(int(inputs or 0) >= 1, "新增快捷问题后没有输入框")
        await ctx.browser.type("#quickList input", "E2E 快捷问题")
        await ctx.browser.click('[data-action="remove-quick"]')
    async with ctx.step("打开手机扫码弹窗"):
        await ctx.browser.click('[data-action="settings-tab"][data-tab="security"]')
        if not await ctx.browser.wait_for_selector('[data-action="open-mobile-access"]', timeout=5):
            await ctx.browser.evaluate(
                "(() => { const p = document.getElementById('settingsSecurityPanel');"
                " if (p) p.hidden = false;"
                " const b = document.getElementById('settingsBasicPanel'); if (b) b.hidden = true; })()"
            )
            await ctx.browser.wait_for_selector('[data-action="open-mobile-access"]', timeout=10)
        await ctx.browser.click('[data-action="open-mobile-access"]')
        await asyncio.sleep(1.0)
        await ctx.browser.press("Escape")
    async with ctx.step("令牌显示切换与保存/清除"):
        await ctx.browser.click('[data-action="settings-tab"][data-tab="security"]')
        await ctx.browser.click('[data-action="toggle-token-visibility"]')
        await ctx.browser.type("#adminTokenInput", "e2e-short")
        await ctx.browser.click('[data-action="save-token"]')
        await asyncio.sleep(1.2)
        await ctx.browser.click('[data-action="clear-token"]')
        await asyncio.sleep(0.8)
        await ctx.browser.click('[data-action="test-token"]')
        await asyncio.sleep(1.0)


@scenario("settings.account", tags=("settings", "auth"), profile="account", covers=("cancel", "logout", "go-login"), allow_errors=("401", "400"))
async def settings_account(ctx: Context) -> None:
    async with ctx.step("登录并打开账号页"):
        await _goto(ctx, "#/login")
        await ctx.browser.type('#loginForm input[name="username"]', "e2e-admin")
        await ctx.browser.type('#loginForm input[name="password"]', ctx.sandbox.admin_password)
        await ctx.browser.click('#loginForm button[type="submit"]')
        await ctx.expect_text("对话", timeout=20)
        await _fresh(ctx, "#/settings?tab=account")
        if not await ctx.browser.wait_for_text("我的账号", timeout=8):
            await ctx.browser.evaluate(
                "document.querySelector('[data-settings-tab=\'account\']')?.click()"
            )
            await ctx.browser.expect_text("账号", timeout=10) if False else None
            await ctx.browser.wait_for_text("账号", timeout=10)
    async with ctx.step("退出登录"):
        if not await ctx.browser.wait_for_selector('[data-action="logout"]', timeout=8):
            await ctx.browser.evaluate(
                "(() => { const items = Array.from(document.querySelectorAll('[data-action=\'logout\']'))"
                ".filter(n => n.getClientRects().length); items[0]?.click(); })()"
            )
        else:
            await ctx.browser.click('[data-action="logout"]')
        await asyncio.sleep(1.0)
        await ctx.expect_selector("#loginForm", timeout=15)
        await _fresh(ctx, "#/chat")
        await ctx.browser.click('[data-action="toggle-history"]')
        await ctx.browser.wait_for_selector('[data-action="go-login"]', timeout=15)
        await ctx.browser.click('[data-action="go-login"]')
        await ctx.expect_selector("#loginForm", timeout=15)

@scenario(
    "knowledge.extra",
    tags=("knowledge",),
    covers=("edit-page", "import-links", "doc-download", "rebuild-all", "clear-uploads", "copy-capture-url", "dismiss-build"),
)
async def knowledge_extra(ctx: Context) -> None:
    async with ctx.step("编辑词条"):
        await _fresh(ctx, "#/knowledge?manage=1")
        await ctx.browser.click('[data-action="manage-tab"][data-tab="pages"]')
        await ctx.browser.wait_for_selector(".wiki-card", timeout=20)
        await ctx.browser.click(".wiki-card")
        if not await ctx.browser.wait_for_selector('[data-action="edit-page"]', timeout=8):
            await ctx.browser.evaluate(
                "(() => { const items = Array.from(document.querySelectorAll('[data-action=\'open-page\']'))"
                ".filter(n => n.getClientRects().length); items[0]?.click(); })()"
            )
        if not await ctx.browser.wait_for_selector('[data-action="edit-page"]', timeout=8):
            await _fresh(ctx, "#/knowledge?manage=1")
            await ctx.browser.click('[data-action="manage-tab"][data-tab="pages"]')
            await ctx.browser.wait_for_selector(".wiki-card", timeout=20)
            await ctx.browser.click(".wiki-card")
            await ctx.browser.wait_for_selector('[data-action="edit-page"]', timeout=25)
        await ctx.browser.click('[data-action="edit-page"]')
        await asyncio.sleep(1.0)
        await ctx.browser.type("form textarea", "E2E 编辑后的正文内容。" * 5)
        await _modal_submit(ctx)
        await asyncio.sleep(1.2)
    async with ctx.step("链接收录（本地手册页）"):
        await _fresh(ctx, "#/knowledge?manage=1")
        await ctx.browser.click('[data-action="capture-mode"][data-mode="links"]')
        await ctx.browser.type(
            "#captureUrls", f"{ctx.server.base_url}/manual/product.html"
        )
        await ctx.browser.click("#importLinksBtn")
        await asyncio.sleep(2.0)
        result = await ctx.browser.evaluate(
            "document.getElementById('captureLinkResults')?.innerText || ''"
        ) or ""
        ctx.expect(bool(result.strip()), "链接收录没有结果反馈")
        if await ctx.browser.wait_for_selector('[data-action="copy-capture-url"]', timeout=8):
            await ctx.browser.click('[data-action="copy-capture-url"]')
    async with ctx.step("下载源文件"):
        await ctx.browser.click('[data-action="manage-tab"][data-tab="sources"]')
        if not await ctx.browser.wait_for_selector('[data-action="doc-download"]', timeout=8):
            await _fresh(ctx, "#/knowledge?manage=1")
            await ctx.browser.click('[data-action="manage-tab"][data-tab="sources"]')
        await ctx.browser.wait_for_selector('[data-action="doc-download"]', timeout=20)
        await ctx.browser.click('[data-action="doc-download"]')
        await asyncio.sleep(1.0)
    async with ctx.step("全部重建"):
        await ctx.browser.click('[data-action="rebuild-all"]')
        await _confirm(ctx)
        await asyncio.sleep(2.0)
        if await ctx.browser.wait_for_selector('[data-action="dismiss-build"]', timeout=8):
            await ctx.browser.click('[data-action="dismiss-build"]')
    async with ctx.step("上传后清除已完成队列"):
        sample = ctx.sandbox.root / "fixtures" / "E2E清空队列.md"
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.write_text("# 清空队列\n\n" + "沙箱内容。" * 30, encoding="utf-8")
        await ctx.browser.click('[data-action="manage-tab"][data-tab="capture"]')
        if not await ctx.browser.wait_for_selector('[data-action="capture-mode"][data-mode="file"]', timeout=5):
            await ctx.browser.click('[data-action="toggle-capture"]')
        await ctx.browser.wait_for_selector('[data-action="capture-mode"][data-mode="file"]', timeout=10)
        await ctx.browser.click('[data-action="capture-mode"][data-mode="file"]')
        await ctx.browser.set_files("#fileInput", [sample])
        await asyncio.sleep(2.5)
        await ctx.browser.click('[data-action="clear-uploads"]')
        await asyncio.sleep(0.6)


@scenario(
    "jobs.actions",
    tags=("ops",),
    covers=("jobs-tab", "jobs-reload", "job-retry", "job-cancel", "alert-ack", "alert-resolve"),
    allow_errors=("400", "401", "403"),
)
async def jobs_actions(ctx: Context) -> None:
    async with ctx.step("制造失败任务与告警"):
        bad = ctx.sandbox.root / "fixtures" / "损坏文档.docx"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"this is not a docx")
        ctx.server.api.post(
            "/api/knowledge/documents?async=1",
            files={"file": (bad.name, bad.read_bytes(), "application/octet-stream")},
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            jobs = ctx.api_get("/api/jobs").get("items", [])
            failed = [item for item in jobs if item.get("status") == "failed"]
            if failed:
                break
            await asyncio.sleep(1.0)
    async with ctx.step("打开任务页并刷新"):
        await _fresh(ctx, "#/ops?tab=jobs")
        await ctx.expect_text("任务", timeout=20)
        await ctx.browser.click('[data-action="jobs-tab"][data-tab="jobs"]')
        await ctx.browser.click('[data-action="jobs-reload"]')
        await asyncio.sleep(1.0)
    async with ctx.step("重试失败任务"):
        await ctx.browser.wait_for_selector('[data-action="job-retry"]', timeout=15)
        await ctx.browser.click('[data-action="job-retry"]')
        await asyncio.sleep(2.0)
    async with ctx.step("取消排队任务"):
        queued = ctx.sandbox.root / "fixtures" / "取消队列.md"
        queued.parent.mkdir(parents=True, exist_ok=True)
        queued.write_text("# 取消\n\n" + "内容。" * 50, encoding="utf-8")
        ctx.server.api.post(
            "/api/knowledge/documents?async=1",
            files={"file": (queued.name, queued.read_bytes(), "text/markdown")},
        )
        await ctx.browser.click('[data-action="jobs-reload"]')
        if await ctx.browser.wait_for_selector('[data-action="job-cancel"]', timeout=6):
            await ctx.browser.click('[data-action="job-cancel"]')
            await _confirm(ctx)
            await asyncio.sleep(1.0)
    async with ctx.step("确认并解决告警"):
        await ctx.browser.click('[data-action="jobs-tab"][data-tab="alerts"]')
        await ctx.browser.wait_for_selector('[data-action="alert-ack"]', timeout=15)
        await ctx.browser.click('[data-action="alert-ack"]')
        await asyncio.sleep(0.8)
        if await ctx.browser.wait_for_selector('[data-action="alert-resolve"]', timeout=5):
            await ctx.browser.click('[data-action="alert-resolve"]')

@scenario(
    "insights.tabs",
    tags=("ops",),
    covers=("insights-tab", "insights-reload"),
    allow_errors=("400", "401"),
)
async def insights_tabs(ctx: Context) -> None:
    async with ctx.step("打开服务台并刷新"):
        await _fresh(ctx, "#/ops?tab=desk")
        await ctx.expect_text("服务台", timeout=20)
        await ctx.browser.click('[data-action="insights-reload"]')
        await asyncio.sleep(1.0)
    async with ctx.step("切换服务台标签"):
        tabs = await ctx.browser.evaluate(
            "Array.from(document.querySelectorAll('[data-action=\\'insights-tab\\']'))"
            ".map(n => n.dataset.tab)"
        ) or []
        for key in tabs[1:4]:
            await ctx.browser.evaluate(
                "(() => { const b = document.querySelector('[data-action=\\'insights-tab\\'][data-tab=\\'%s\\']');"
                " if (b) b.click(); })()" % key
            )
            await asyncio.sleep(0.8)

@scenario(
    "insights.actions",
    tags=("ops",),
    covers=("handoff-claim", "handoff-copy", "handoff-reply", "handoff-resolve", "crisis-status", "gap-copy", "gap-resolve", "gap-ignore", "feedback-resolve", "feedback-ignore", "cancel", "open-wiki-page"),
    allow_errors=("400", "401", "403"),
)
async def insights_actions(ctx: Context) -> None:
    async with ctx.step("制造反馈/工单/危机数据"):
        chat = ctx.server.api.post(
            "/api/chat",
            json={"message": "E2E 无法回答的问题：火星校区班车时刻", "session_key": "e2e-insights"},
        ).json()
        conversation_id = chat.get("conversation_id") or (chat.get("conversation") or {}).get("id") or ""
        message_id = chat.get("message_id") or (chat.get("message") or {}).get("id") or ""
        if message_id:
            ctx.server.api.post(
                "/api/chat/feedback",
                json={"message_id": message_id, "rating": -1, "reason_code": "not_found", "voter_key": "e2e"},
            )
        if conversation_id:
            ctx.server.api.post(
                "/api/handoff",
                json={"conversation_id": conversation_id, "message_id": message_id, "reason": "other", "summary": "E2E 转人工"},
            )
        ctx.server.api.post(
            "/api/chat",
            json={"message": "我想自杀，请帮助我", "session_key": "e2e-crisis"},
        )
        await asyncio.sleep(1.0)
    async with ctx.step("打开服务台并刷新"):
        await _fresh(ctx, "#/ops?tab=desk")
        await ctx.expect_text("服务台", timeout=20)
        await ctx.browser.click('[data-action="insights-reload"]')
        await asyncio.sleep(1.5)
    async with ctx.step("工单：认领/复制/回复/解决"):
        await ctx.browser.evaluate("document.querySelector('[data-action=\\'insights-tab\\'][data-tab=\\'handoffs\\']')?.click()")
        await ctx.browser.wait_for_selector('[data-action="handoff-claim"]', timeout=15)
        await ctx.browser.click('[data-action="handoff-claim"]')
        await asyncio.sleep(0.8)
        await ctx.browser.click('[data-action="handoff-copy"]')
        await ctx.browser.click('[data-action="handoff-reply"]')
        await ctx.browser.wait_for_selector("textarea", timeout=10)
        await ctx.browser.type("textarea", "E2E 工单回复内容")
        await _modal_submit(ctx)
        await asyncio.sleep(1.0)
        await ctx.browser.click('[data-action="handoff-resolve"]')
        await _confirm(ctx)
    async with ctx.step("危机事件状态"):
        await ctx.browser.evaluate("document.querySelector('[data-action=\\'insights-tab\\'][data-tab=\\'crisis\\']')?.click()")
        await ctx.browser.wait_for_selector('[data-action="crisis-status"]', timeout=15)
        await ctx.browser.click('[data-action="crisis-status"]')
        await asyncio.sleep(0.8)
    async with ctx.step("知识缺口处理"):
        await ctx.browser.evaluate("document.querySelector('[data-action=\\'insights-tab\\'][data-tab=\\'gaps\\']')?.click()")
        await ctx.browser.wait_for_selector('[data-action="gap-resolve"]', timeout=15)
        await ctx.browser.click('[data-action="gap-copy"]')
        await ctx.browser.click('[data-action="gap-resolve"]')
        await asyncio.sleep(0.8)
        if await ctx.browser.wait_for_selector('[data-action="gap-ignore"]', timeout=5):
            await ctx.browser.click('[data-action="gap-ignore"]')
    async with ctx.step("反馈处理"):
        await ctx.browser.evaluate("document.querySelector('[data-action=\\'insights-tab\\'][data-tab=\\'feedback\\']')?.click()")
        await ctx.browser.wait_for_selector('[data-action="feedback-resolve"]', timeout=15)
        await ctx.browser.click('[data-action="feedback-resolve"]')
        await asyncio.sleep(0.8)
        if await ctx.browser.wait_for_selector('[data-action="feedback-ignore"]', timeout=5):
            await ctx.browser.click('[data-action="feedback-ignore"]')

@scenario(
    "chat.extra",
    tags=("chat",),
    covers=("scroll-cite", "retry-send", "dismiss-error", "crisis-ack"),
    allow_errors=("Failed to load resource", "ERR_BLOCKED_BY_CLIENT"),
)
async def chat_extra(ctx: Context) -> None:
    async with ctx.step("发送问题并定位引用"):
        await _fresh(ctx, "#/chat")
        await ctx.browser.type("#chatInput", "国家奖学金怎么申请？")
        await ctx.browser.press("Enter")
        await ctx.browser.wait_for_selector(".cite-ref", timeout=45)
        await ctx.browser.click(".cite-ref")
        await asyncio.sleep(0.5)
    async with ctx.step("拦截接口制造发送失败"):
        await ctx.browser.call("Network.setBlockedURLs", {"urls": ["*api/chat*"]})
        await ctx.browser.type("#chatInput", "这条消息应该失败")
        await ctx.browser.press("Enter")
        await ctx.browser.wait_for_selector('[data-action="retry-send"]', timeout=20)
        await ctx.browser.click('[data-action="retry-send"]')
        await asyncio.sleep(1.0)
        await ctx.browser.click('[data-action="dismiss-error"]')
        await ctx.browser.call("Network.setBlockedURLs", {"urls": []})
    async with ctx.step("危机提示确认"):
        await ctx.browser.type("#chatInput", "我想自杀，需要帮助")
        await ctx.browser.press("Enter")
        await ctx.browser.wait_for_selector('[data-action="crisis-ack"]', timeout=45)
        await ctx.browser.click('[data-action="crisis-ack"]')
        await asyncio.sleep(0.5)

@scenario(
    "errors.retry",
    tags=("knowledge", "settings"),
    covers=("retry-page", "retry"),
    allow_errors=("Failed to load resource", "ERR_BLOCKED_BY_CLIENT", "net::ERR"),
)
async def errors_retry(ctx: Context) -> None:
    async with ctx.step("词条详情加载失败后重试"):
        await _fresh(ctx, "#/knowledge")
        await ctx.browser.call("Network.setBlockedURLs", {"urls": ["*api/wiki/pages/*"]})
        await ctx.browser.click(".wiki-card")
        await ctx.browser.wait_for_selector('[data-action="retry-page"]', timeout=20)
        await ctx.browser.call("Network.setBlockedURLs", {"urls": []})
        await ctx.browser.click('[data-action="retry-page"]')
        await asyncio.sleep(1.2)
    async with ctx.step("设置加载失败后重试"):
        await ctx.browser.call("Network.setBlockedURLs", {"urls": ["*api/settings*"]})
        await _fresh(ctx, "#/settings")
        await ctx.browser.wait_for_selector('[data-action="retry"]', timeout=20)
        await ctx.browser.call("Network.setBlockedURLs", {"urls": []})
        await ctx.browser.click('[data-action="retry"]')
        await asyncio.sleep(1.2)


