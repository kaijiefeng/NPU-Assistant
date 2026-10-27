# 校园内容领域清单与合法更新方案

本方案的目标：让初始知识库来自**真实、公开、可追溯**的校园内容；同时提供一条**合法、可控、可定时**的更新通道。官方 API 作为优先备选，而不是前提。

## 一、内容领域清单

| # | 领域 | 学生常见问题 | 首选公开来源 | 当前状态 |
|---:|---|---|---|---|
| 1 | 学籍与注册 | 学籍异动、转专业、休复学、学生证 | 学生工作部制度公开、教务部、研究生院 | 示例 + 真实表单；xgb 需人工审核导入 |
| 2 | 教学与课程考试 | 选课、调课、考试、成绩、培养方案 | 教务部通知、研究生院通知 | 已导入真实 HTML/PDF |
| 3 | 奖助学金与资助 | 国家奖学金、助学金、助学贷款、困难认定 | 学生工作部制度公开/奖励资助 | 示例；xgb 需人工审核导入 |
| 4 | 请假考勤与日常管理 | 请假、销假、违纪、日常管理 | 学生工作部、校园公告 | 已导入真实通知/PDF |
| 5 | 心理健康 | 心理咨询预约、危机求助、适应问题 | 学生心理健康教育中心 | 示例；真实文章抽取待完善 |
| 6 | 就业、毕业与档案 | 三方协议、档案、报到证、就业指导 | 学生职业发展中心、研究生院 | 示例；job 站点 WAF 412 |
| 7 | 宿舍与一站式社区 | 宿舍管理、调宿、社区服务 | 学生工作部宿舍管理 | 示例；xgb 需人工审核导入 |
| 8 | 校园安全与应急 | 消防、实验室安全、突发事件 | 学生工作部安全教育、保卫处 | 示例；栏目列表为空/待补 |
| 9 | 校园生活与一卡通 | 一卡通、网络、校医院、场馆 | 校园公告、信息化管理处 | 示例 |
| 10 | 校历校车与图书馆 | 校历、班车、图书馆开放 | 校园公告、图书馆 | 示例；xygg WAF 403 |
| 11 | 信息公开与制度 | 学校规章、年报、办事依据 | 信息公开网 | 示例；部分 PDF 为扫描件 |
| 12 | 研究生教育 | 培养、学位、答辩、导师 | 研究生院 | 已导入真实通知，**自动源已启用** |

> 原则：优先学生事务与教学制度，不采招标采购、新闻宣传、个人名单/成绩等低价值或含个人信息的内容。

## 二、真实来源与合法性

### 2.1 已确认公开源

| 源 | 列表页 | 自动同步 | 说明 |
|---|---|---|---|
| 研究生院通知 | `https://gs.nwpu.edu.cn/cn/index/tzgg.htm` | ✅ 已启用 | robots 404、列表 200；当前唯一稳定自动源 |
| 校园公告综合通知 | `https://xygg.nwpu.edu.cn/xygg/xngg.htm` | ❌ 停用 | 列表对脚本 403（WAF），人工审核导入 |
| 教务部通知 | `https://jiaowu.nwpu.edu.cn/jxxx1/tzgg.htm` | ❌ 停用 | 同上 |
| 学生工作部制度/通知 | `https://xgb.nwpu.edu.cn/xxgk/zdgk.htm` 等 | ❌ 停用 | 列表对脚本 403，人工审核/官方接口 |
| 心理中心通知 | `https://xl.nwpu.edu.cn/gzdt/tzgg.htm` | ❌ 停用 | 列表 200，但详情页多为图片/短讯，自动抽取失败 |
| 信息公开网 | `https://xxgk.nwpu.edu.cn/` | ❌ 停用 | 部分 PDF 扫描件/直链 WAF，人工处理 |
| 就业信息网 | `https://job.nwpu.edu.cn/` | ❌ 停用 | 412/WAF，需官方接口或人工 |

### 2.2 合规红线

- 只抓**无需登录的公开页面**；不登录翱翔门户、不访问成绩/个人信息。
- **不绕过验证码、WAF、登录或付费墙**；脚本被 403/412 拦截即停止，不伪装浏览器/换 IP 规避。
- 遵守 robots.txt：200 解析；4xx 视为未提供；5xx/网络错误视为不可用并暂停。
- 限速：同域请求间隔 3 秒；每源每次最多 5 条；建议每 6 小时一次。
- 只保存页面正文与来源 URL；附件默认不抓（部分附件有验证码）；不把整站/二进制文件提交 Git。
- 设置可联系 UA：`NPU-Assistant campus-sync/0.1`；部署方可在 `campus_sources.json` 中补充真实联系人。
- 收到学校撤下要求时，删除 `documents` 记录与本地缓存。

## 三、自动更新途径（已实现 MVP）

### 3.1 机制

- 源注册表：[`backend/app/campus_sources.json`](../backend/app/campus_sources.json)
- 同步服务：[`backend/app/services/campus_sync.py`](../backend/app/services/campus_sync.py)
- 命令行：[`scripts/campus_sync.py`](../scripts/campus_sync.py) / [`scripts/campus_sync.ps1`](../scripts/campus_sync.ps1)
- 流程：列表页 → 提取 `/info/...` 详情链接 → 过滤采购/外站/重复 → 限速抓取详情 → 复用现有 `importer.import_url` 编译 → 以 `documents.source_url` 去重。
- 只导入正文；附件不自动下载；失败只记录，不阻塞其它源。
- 可 `--dry-run` 预览，不写库；`--limit` 控制本次条数；`--source` 只跑指定源。

### 3.2 运行

```powershell
# 预览候选（不写库）
.\scripts\campus_sync.ps1 -DryRun

# 只同步研究生院，最多 2 条
.\scripts\campus_sync.ps1 -Source gs-tzgg -Limit 2

# 全量启用源
.\scripts\campus_sync.ps1
```

Windows 任务计划示例（每 6 小时）：

```powershell
schtasks /Create /TN "NPU-Assistant-CampusSync" /SC HOURLY /MO 6 `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\Development\NPU-Assistant\scripts\campus_sync.ps1" `
  /F
```

### 3.3 当前限制

- 自动源 `gs-tzgg` 第一页 10 条已全部拉取（去重后新增 7 条）；其它源受 WAF/页面结构限制，自动开关默认关闭，避免违规绕过。
- 需要人工浏览器下载/复制的条目单独维护在 **[CAMPUS-MANUAL-QUEUE.md](CAMPUS-MANUAL-QUEUE.md)**（23 条已全部分流完成），分流结果见 `docs/campus-seed/manual-queue-results.json`，不混入自动源。
- 自动更新是“新 URL 增量”，不做全站递归；页面内容变更暂不自动覆盖（可在后续加入 hash 变更检测）。
- 不包含附件解析/下载；PPT/PPTX 暂不实现。

## 四、官方接口备选方案

另见限定范围联网检索试验：[CAMPUS-REMOTE-SEARCH.md](CAMPUS-REMOTE-SEARCH.md)。该方案不把互联网内容入库，只在查询时检索指定网站/公众号并引用。

已确认学校有 [API 数据使用申请入口](https://dev-portal.nwpu.edu.cn/dev-portal/#/home)，但未发现公开的校园公告 API/MCP。建议并行申请：

- 申请只读的“通知公告列表/详情”接口，字段至少：标题、发布日期、正文、来源 URL、栏目、附件链接；
- 授权后新增 `type: "api"` 源适配器，复用 `CampusSource`/去重/编译链路；
- 官方接口优先于爬取；未获批前使用公开页面 + 人工审核导入；
- 不直连校园业务数据库，不申请学生个人信息字段。

## 五、初始库与后续目标

- 当前：**63 篇真实源文件 / 186 个词条 / 217 个来源锚点**（示例已清理）。
- 已到 **63 篇真实数据**；下一阶段补到 **80 篇**，重点补就业、心理中心列表、校历/一卡通、办事流程；每篇保留来源 URL、抓取时间、SHA256 清单。
- 清单：[`docs/CAMPUS-SEED.md`](CAMPUS-SEED.md)、[`docs/campus-seed/manifest.json`](campus-seed/manifest.json)。

## 六、验收

- [x] 真实 HTML/PDF/DOCX/DOC 入库并可问答引用；扫描件/PPT 有明确降级提示。
- [x] `campus_sync.py --dry-run` 可列出候选；真实 `gs-tzgg` 增量导入成功。
- [x] 重复 URL 不重复编译；限速/限条数/robots 处理有测试。
- [x] 全量 pytest **120 passed**（含 campus sync 5 条）。
- [ ] 官方 API 申请/授权（待学校审批）。
- [ ] WAF 源的人工审核导入与更多领域补全（下一步）。
