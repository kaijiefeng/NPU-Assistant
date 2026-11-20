# E2E 覆盖率与验收摘要

- 原始报告：2026-11-15 10:27:35（沙箱 `npu-e2e-gv4i0o5i`）
- 静态动作覆盖：**114/114 = 100.0%**
- 场景：PASS 32 / FAIL 0 / KNOWN 0
- 截图只在本地生成（`docs/screenshots/e2e/` 已 gitignore），不随仓库提交；需要取证时运行 `scripts/e2e.ps1 -Publish`。

## 场景与覆盖

| 场景 | 状态 | 覆盖动作 |
|---|---|---|
| `bootstrap.routes` | PASS | — |
| `chat.answer_and_citation` | PASS | — |
| `knowledge.browse` | PASS | open-page, clear-search, edit-page |
| `knowledge.manage_upload` | PASS | pick-file, doc-delete, refresh-docs |
| `settings.assistant_and_models` | PASS | — |
| `ops.tabs` | PASS | audit-reload |
| `mobile.layout` | PASS | — |
| `sandbox.isolation` | PASS | — |
| `chat.interactions` | PASS | quick-question, feedback-up, toggle-history, close-history, new-chat |
| `knowledge.scan_and_delete` | PASS | manage-tab, scan-dir, refresh-docs, doc-delete |
| `knowledge.more` | PASS | toggle-capture, capture-mode, import-text, refresh-pages, filter-tag, doc-pages, reindex, seed |
| `chat.more` | PASS | feedback-down, feedback-reason, feedback-submit, feedback-reset, feedback-cancel, toggle-history, open-conversation, delete-conversation, handoff-open |
| `models.crud` | PASS | models-tab, add-provider, test-provider, edit-provider, add-model, test-model, set-default, toggle-model, edit-model, delete-model, delete-provider, refresh |
| `bots.console` | PASS | bots-tab, add-channel, save-channel, toggle-channel, refresh-channels, open-console, run-console, clear-console, refresh-logs, clear-admin, delete-channel |
| `settings.quick` | PASS | settings-tab, add-quick, remove-quick, open-mobile-access, toggle-token-visibility, save-token, clear-token, test-token, enable-server-token |
| `knowledge.extra` | PASS | edit-page, import-links, doc-download, rebuild-all, clear-uploads, copy-capture-url, dismiss-build, delete-page |
| `jobs.actions` | PASS | jobs-tab, jobs-reload, job-retry, job-cancel, alert-ack, alert-resolve |
| `insights.tabs` | PASS | insights-tab, insights-reload |
| `insights.actions` | PASS | handoff-claim, handoff-copy, handoff-reply, handoff-resolve, crisis-status, gap-copy, gap-resolve, gap-ignore, feedback-resolve, feedback-ignore, cancel, open-wiki-page |
| `chat.extra` | PASS | scroll-cite, retry-send, dismiss-error, crisis-ack |
| `errors.retry` | PASS | retry-page, retry, refresh-history |
| `mobile.chat` | PASS | new-chat, feedback-up, toggle-history |
| `mobile.knowledge` | PASS | open-page, filter-tag |
| `mobile.manage` | PASS | pick-file, manage-tab |
| `mobile.settings` | PASS | models-tab |
| `knowledge.empty` | PASS | go-manage |
| `links.oversize` | PASS | use-text-import |
| `auth.login_logout` | PASS | — |
| `team.users` | PASS | team-tab, team-reload, user-add, user-role, user-toggle, user-reset, cancel |
| `team.account` | PASS | password-self, user-delete |
| `settings.account` | PASS | cancel, logout, go-login |
| `token.settings_redaction` | PASS | — |

## 分视图覆盖

- **bots**：11/11
- **chat**：20/20
- **insights**：15/15
- **jobs**：6/6
- **knowledge**：26/26
- **models**：12/12
- **settings**：10/10
- **settings-hub**：3/3
- **team**：11/11
