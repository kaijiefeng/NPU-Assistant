# E2E 覆盖率与截图证据

- 生成时间：2026-11-14 16:43:20
- 沙箱：`npu-e2e-ofd_5680`（临时目录，运行后删除）
- 静态动作覆盖：**45/114 = 39.5%**
- 场景：PASS 16 / FAIL 0 / KNOWN 0

## 场景与截图

| 场景 | 状态 | 覆盖动作 | 截图 |
|---|---|---|---|
| `bootstrap.routes` | PASS |  | [00-打开对话页](screenshots/e2e/bootstrap.routes/00-打开对话页.png)<br>[01-侧边栏切换到知识库](screenshots/e2e/bootstrap.routes/01-侧边栏切换到知识库.png)<br>[02-旧路由_manage_重定向](screenshots/e2e/bootstrap.routes/02-旧路由_manage_重定向.png)<br>[03-未知路由回落到对话](screenshots/e2e/bootstrap.routes/03-未知路由回落到对话.png) |
| `chat.answer_and_citation` | PASS |  | [00-打开对话页](screenshots/e2e/chat.answer_and_citation/00-打开对话页.png)<br>[01-输入并发送问题](screenshots/e2e/chat.answer_and_citation/01-输入并发送问题.png)<br>[02-等待回答与引用](screenshots/e2e/chat.answer_and_citation/02-等待回答与引用.png)<br>[03-展开引用来源](screenshots/e2e/chat.answer_and_citation/03-展开引用来源.png) |
| `knowledge.browse` | PASS | open-page, clear-search, edit-page | [00-打开知识库](screenshots/e2e/knowledge.browse/00-打开知识库.png)<br>[01-搜索并打开词条](screenshots/e2e/knowledge.browse/01-搜索并打开词条.png)<br>[02-关闭抽屉](screenshots/e2e/knowledge.browse/02-关闭抽屉.png) |
| `knowledge.manage_upload` | PASS | pick-file, doc-delete, refresh-docs | [00-进入收录资料](screenshots/e2e/knowledge.manage_upload/00-进入收录资料.png)<br>[01-选择文件并上传](screenshots/e2e/knowledge.manage_upload/01-选择文件并上传.png)<br>[02-等待后台任务完成](screenshots/e2e/knowledge.manage_upload/02-等待后台任务完成.png) |
| `settings.assistant_and_models` | PASS |  | [00-打开设置页](screenshots/e2e/settings.assistant_and_models/00-打开设置页.png)<br>[01-切换到模型管理](screenshots/e2e/settings.assistant_and_models/01-切换到模型管理.png)<br>[02-模型列表出现_E2E_Mock](screenshots/e2e/settings.assistant_and_models/02-模型列表出现_E2E_Mock.png) |
| `ops.tabs` | PASS | audit-reload | [00-打开运营页](screenshots/e2e/ops.tabs/00-打开运营页.png)<br>[01-切换到任务与告警](screenshots/e2e/ops.tabs/01-切换到任务与告警.png)<br>[02-切换到审计并刷新](screenshots/e2e/ops.tabs/02-切换到审计并刷新.png) |
| `mobile.layout` | PASS |  | [00-切换到_390x844_视口](screenshots/e2e/mobile.layout/00-切换到_390x844_视口.png)<br>[01-检查_chat_无横向滚动](screenshots/e2e/mobile.layout/01-检查_chat_无横向滚动.png)<br>[02-检查_knowledge_无横向滚动](screenshots/e2e/mobile.layout/02-检查_knowledge_无横向滚动.png)<br>[03-检查_ops_无横向滚动](screenshots/e2e/mobile.layout/03-检查_ops_无横向滚动.png)<br>[04-检查_settings_无横向滚动](screenshots/e2e/mobile.layout/04-检查_settings_无横向滚动.png)<br>[05-侧边栏开合](screenshots/e2e/mobile.layout/05-侧边栏开合.png) |
| `sandbox.isolation` | PASS |  | [00-服务数据目录位于沙箱](screenshots/e2e/sandbox.isolation/00-服务数据目录位于沙箱.png)<br>[01-生产数据未被写入](screenshots/e2e/sandbox.isolation/01-生产数据未被写入.png) |
| `chat.interactions` | PASS | quick-question, feedback-up, toggle-history, close-history, new-chat | [00-快捷问题](screenshots/e2e/chat.interactions/00-快捷问题.png)<br>[01-点赞反馈](screenshots/e2e/chat.interactions/01-点赞反馈.png)<br>[02-打开历史会话](screenshots/e2e/chat.interactions/02-打开历史会话.png)<br>[03-关闭历史会话](screenshots/e2e/chat.interactions/03-关闭历史会话.png)<br>[04-新建会话](screenshots/e2e/chat.interactions/04-新建会话.png) |
| `knowledge.scan_and_delete` | PASS | manage-tab, scan-dir, refresh-docs, doc-delete | [00-填写扫描目录并开始扫描](screenshots/e2e/knowledge.scan_and_delete/00-填写扫描目录并开始扫描.png)<br>[01-等待扫描任务产出文档](screenshots/e2e/knowledge.scan_and_delete/01-等待扫描任务产出文档.png)<br>[02-切换到源文件并刷新](screenshots/e2e/knowledge.scan_and_delete/02-切换到源文件并刷新.png)<br>[03-删除扫描文档](screenshots/e2e/knowledge.scan_and_delete/03-删除扫描文档.png) |
| `knowledge.more` | PASS | toggle-capture, capture-mode, import-text, refresh-pages, filter-tag, doc-pages, reindex, seed | [00-收起_展开快速收录](screenshots/e2e/knowledge.more/00-收起_展开快速收录.png)<br>[01-文本收录](screenshots/e2e/knowledge.more/01-文本收录.png)<br>[02-切到词条列表并刷新](screenshots/e2e/knowledge.more/02-切到词条列表并刷新.png)<br>[03-按标签筛选](screenshots/e2e/knowledge.more/03-按标签筛选.png)<br>[04-导入预编译快照](screenshots/e2e/knowledge.more/04-导入预编译快照.png)<br>[05-源文件详情与重编译](screenshots/e2e/knowledge.more/05-源文件详情与重编译.png) |
| `chat.more` | PASS | feedback-down, feedback-reason, feedback-submit, toggle-history, open-conversation, delete-conversation, handoff-open | [00-发送问题](screenshots/e2e/chat.more/00-发送问题.png)<br>[01-点踩并选择原因](screenshots/e2e/chat.more/01-点踩并选择原因.png)<br>[02-历史会话打开](screenshots/e2e/chat.more/02-历史会话打开.png)<br>[03-删除历史会话](screenshots/e2e/chat.more/03-删除历史会话.png)<br>[04-转人工弹窗](screenshots/e2e/chat.more/04-转人工弹窗.png) |
| `models.crud` | PASS | models-tab, add-provider, test-provider, edit-provider, add-model, test-model, set-default, toggle-model, delete-model, delete-provider, refresh | [00-打开_Provider_标签](screenshots/e2e/models.crud/00-打开_Provider_标签.png)<br>[01-新增_Provider](screenshots/e2e/models.crud/01-新增_Provider.png)<br>[02-测试与编辑_Provider](screenshots/e2e/models.crud/02-测试与编辑_Provider.png)<br>[03-新增并测试模型](screenshots/e2e/models.crud/03-新增并测试模型.png)<br>[04-设为默认_停用_刷新](screenshots/e2e/models.crud/04-设为默认_停用_刷新.png)<br>[05-删除模型与_Provider](screenshots/e2e/models.crud/05-删除模型与_Provider.png) |
| `auth.login_logout` | PASS |  | [00-打开登录页并登录](screenshots/e2e/auth.login_logout/00-打开登录页并登录.png)<br>[01-退出登录](screenshots/e2e/auth.login_logout/01-退出登录.png) |
| `team.users` | PASS | team-tab, team-reload, user-add, user-role, user-toggle, user-reset | [00-登录管理员](screenshots/e2e/team.users/00-登录管理员.png)<br>[01-打开成员页](screenshots/e2e/team.users/01-打开成员页.png)<br>[02-新增成员](screenshots/e2e/team.users/02-新增成员.png)<br>[03-修改角色](screenshots/e2e/team.users/03-修改角色.png)<br>[04-停用成员](screenshots/e2e/team.users/04-停用成员.png)<br>[05-重置成员密码](screenshots/e2e/team.users/05-重置成员密码.png) |
| `token.settings_redaction` | PASS |  | [00-匿名请求设置只返回白名单](screenshots/e2e/token.settings_redaction/00-匿名请求设置只返回白名单.png)<br>[01-带管理令牌可读完整设置](screenshots/e2e/token.settings_redaction/01-带管理令牌可读完整设置.png) |

## 分视图覆盖

- **bots**：0/11
- **chat**：11/20
- **insights**：0/15
- **jobs**：0/6
- **knowledge**：16/26
- **models**：11/12
- **settings**：0/10
- **settings-hub**：0/3
- **team**：7/11
