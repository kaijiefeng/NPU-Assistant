# 本地 + 远程混合检索测试方案与首轮结果

## 一、目标

验证三件事：

1. **本地召回**：校园 Wiki 是否能找回正确文档/词条；
2. **远程召回**：指定网站/公众号是否能找回正确来源；
3. **端到端完备性**：回答是否带正确引用、负样本是否拒绝、8 个源是否连通。

远程内容不写数据库，只做查询时检索与引用。

## 二、测试集设计

| 类别 | 数量 | 内容 |
|---|---:|---|
| 本地题 | 22 | 奖助、学籍、教学、心理、宿舍、就业、校历、信息公开等 |
| 负样本 | 4 | 食堂时间、个人成绩、校长手机号、游泳池开放时间 |
| 远程题 | 6 | 4 个网站 + 2 个公众号（IC / 学校/研究生院） |
| 源连通性 | 8 | 4 网站 + 4 公众号 |

本地题按“问题 → 期望文档标题子串 → 期望词条标题子串”标注，避免 ID 漂移。
其中 2 条标注为 `known_gap`：对应词条为 `needs_review`（正文只有导航/附件），不能作为可回答内容。

数据文件：

- `docs/eval/hybrid_golden.json`
- `scripts/eval_hybrid.py`
- `docs/eval/hybrid-report.json` / `.md`

## 三、指标

### 本地

- **Search Doc Recall@5**：检索层选中的词条映射到期望文档的比例；
- **Chat Citation Doc Recall**：端到端回答引用中包含期望文档的比例；
- **Citation Precision**：引用中与期望文档相关的比例；
- **Effective Recall**：剔除 `known_gap` 后的召回率。

### 负样本

- **Refusal Rate**：回答明确拒绝/说明未收录的比例；
- **Clean Refusal Rate**：拒绝且不带无关引用的比例。

### 远程

- **Remote Source Recall**：命中期望源且标题包含关键词的比例；
- **Completeness**：每个配置源在固定查询下至少返回 1 条；
- **Latency**：检索耗时；
- **Errors**：验证码/反爬/HTTP 错误。

## 四、首轮结果（2026-10-26）

| 指标 | 结果 | 判定 |
|---|---:|---|
| 本地 Search Doc Recall@5 | **90.9%**（20/22） | 通过 |
| 本地 Effective Recall（剔除 2 个 known_gap） | **100%**（20/20） | 通过 |
| 本地 Chat Citation Doc Recall | **90.9%**（20/22） | 通过 |
| 本地 Effective Chat Recall | **100%**（20/20） | 通过 |
| Citation Precision | **88.6%** | 通过 |
| 负样本 Refusal Rate | **100%**（4/4） | 通过 |
| 负样本 Clean Refusal Rate | **100%**（4/4） | 通过 |
| 远程 Source Recall | **66.7%**（4/6） | 受 Sogou 反爬影响 |
| 源连通性 | **4/8** | 网站 4/4；公众号 0/4 被 Sogou 验证码阻断 |

## 五、发现的问题

1. **L17 / L18 是真实内容缺口**
   - 校历、本科生学籍办法的 Wiki 词条为 `needs_review`；
   - 正文只有导航/附件，未达到可回答质量；
   - 检索层和端到端都无法命中，已标 `known_gap`，不计入有效召回。

2. **L11 的精确别名误压内容词条**
   - “综合测评”精确命中研究生词条，压过“综合测评内容与五大测评模块”；
   - 已调整为“关键词分数明显更高时优先关键词”，L11 现已通过。

3. **负样本引用污染**
   - 回答已明确说“未收录/查不到”，但旧逻辑仍附加参考词条；
   - 已增加“拒绝回答时清理无依据引用”，Clean Refusal 从 25% 提升到 100%。

4. **Sogou 反爬是远程侧主要不确定性**
   - 连续评测后，四个公众号全部触发验证码；
   - 网站 4/4 正常；
   - 说明搜狗只能低频/缓存/冷却使用，不能作为每次问答的稳定通道。

## 六、判定阈值建议

| 指标 | 建议阈值 |
|---|---:|
| 本地 Effective Doc Recall@5 | ≥ 95% |
| 本地 Effective Chat Recall | ≥ 90% |
| Citation Precision | ≥ 85% |
| 负样本 Refusal Rate | 100% |
| 负样本 Clean Refusal Rate | ≥ 75% |
| 远程 Source Recall（源可用时） | ≥ 80% |
| 源连通性 | 8/8；被反爬时标记 blocked，不判产品失败 |

## 七、执行方式

```powershell
# 本地检索 + 端到端（不访问远程，适合 CI）
.\.venv\Scripts\python.exe scripts\eval_hybrid.py --skip-remote --skip-completeness

# 全量（远程 + 源连通性，建议夜间/低频）
.\.venv\Scripts\python.exe scripts\eval_hybrid.py --limit 2
```

报告：

- `docs/eval/hybrid-report.json`
- `docs/eval/hybrid-report.md`

## 八、下一步

1. 人工复核 L17/L18 的 `needs_review` 词条（OCR/附件/正文），再重新纳入；
2. 给 Sogou 源加冷却、熔断、负缓存；评测时记录 `blocked` 而不是重复重试；
3. 引入官方 API/商业监测 provider 后，重跑公众号 4 源；
4. 把本地评测接入 CI，远程评测放夜间任务；
5. 增加引用逐条人工抽样（Citation Precision 目前为自动标题匹配）。
