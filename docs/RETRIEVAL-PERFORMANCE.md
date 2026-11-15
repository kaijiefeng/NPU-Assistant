# 检索性能（大语料导入后）

2026-11-15 把采集语料全量导入后，Wiki 词条从 **287 → 3,686**（其中 ready 2,803）。本文记录检索链路的瓶颈、修复与实测数据。

## 结论

| 场景 | 287 页（旧库） | 3,686 页（现库） |
|---|---:|---:|
| 旧代码：单次关键词检索 | ~840 ms | **~11.4 s**（估算实测） |
| 新代码：暖缓存检索 | **5.4 ms** | **50.5 ms** |
| 新代码：进程冷启动后首次检索 | 448 ms | 1.33 s |

也就是说：**不优化会慢 13 倍到 10 秒级；优化后虽然词条多了 12 倍，检索仍只要约 50 ms**，相对 LLM 回答（通常 1–3 s）可以忽略。

## 瓶颈

1. `_page_dict()` 对每个词条调用 `freshness.freshness_for()`，而后者每次都读两条 settings（`freshness_*`、`academic_year`）→ 2,803 页 × 2 次 SQLite 查询，约 **6.6 s**；
2. 每次查询都对全部词条的标题/别名/标签/摘要重新 `jieba` 分词 → 约 **1 s/次**；
3. 每次检索都重新加载并构造全部页面字典 → 冷加载约 **7 s**。

## 修复

- `freshness` 的设置读取加 30 s TTL 缓存（`_cached_setting`），避免每页两次 DB 查询；
- `wiki_nav._load_ready_pages()` 按 `(ready 数量, MAX(updated_at), 日期)` 缓存页面列表，词条变更自动失效；
- 页面加载时预计算标题/别名/标签/摘要 token（`_TOKEN_CACHE`），检索时直接复用；
- 静态展示的 `search_pages()` 保留原有评分逻辑，不改变召回结果。

## 复测

```powershell
# 旧库（导入前备份）
.\.venv\Scripts\python.exe scripts\bench_retrieval.py --db backend\data\backups\app-20261115-162421.db --label before
# 当前库
.\.venv\Scripts\python.exe scripts\bench_retrieval.py --db backend\data\app.db --label after
```

脚本在临时目录复制数据库后测 `_load_ready_pages`、`search_pages`、`select_pages_with_method`（关闭 LLM 导航），不会修改生产数据。

## 其他影响

- 首请求冷启动会多 ~1 s（分词预热），之后进程内复用；
- LLM 选页只在这类弱命中时触发；强命中走 keyword/exact，不额外调用模型；
- 若继续增长到 1 万词条，建议升级为 SQLite FTS5 或倒排索引；当前 50 ms 仍远低于模型耗时。
