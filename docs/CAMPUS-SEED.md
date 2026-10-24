# 真实校园数据测试集 / 初始知识库

本目录记录 v0.1.2 补充的**真实公开数据**，用于格式解析、Wiki 编译、引用回溯和 UI 验收。文件本体保存在
`backend/data/campus_seed/files/`（已 gitignore），仓库只提交可复核的清单
[manifest.json](campus-seed/manifest.json)，避免把学校附件二进制放进 Git。

## 一、已导入数据（10 篇）

| 类型 | 数量 | 文件 / 页面 | 来源 |
|---|---:|---|---|
| HTML 网页 | 3 | 教务部课程思政通知、民航学院学生日常管理通知、研究生院系统维护公示 | [教务部](https://jiaowu.nwpu.edu.cn/info/1164/30528.htm)、[民航学院](https://minhang.nwpu.edu.cn/info/1042/1531.htm)、[研究生院](https://gs.nwpu.edu.cn/info/2141/32257.htm) |
| PDF | 4 | 学位论文评阅意见处理办法、教授副教授授课管理办法、研究生培养国际化规定、研究生课程教学管理规定 | [材料学院资料下载](https://cailiao.nwpu.edu.cn/info/2643/31676.htm) |
| DOCX | 2 | 知识产权声明页、博士后进站审核表（非定向） | [材料学院](https://cailiao.nwpu.edu.cn/info/2643/31676.htm)、[留院](https://liuyuan.nwpu.edu.cn/info/1057/15064.htm) |
| DOC（Word 97-2003） | 1 | 在校学习基本情况表 | [材料学院](https://cailiao.nwpu.edu.cn/info/2643/31676.htm) |

另有 1 个扫描版 PDF（《关于进一步加强学生日常管理的通知》）作为**负样本**保留，不导入：pypdf 无文本，系统会给出“扫描件/无文本”提示，不做 OCR。

**PPT/PPTX 暂不实现**（按约定），上传时会提示另存为 PDF 或复制正文。

## 二、真实导入结果

- 数据库：20 篇源文件（10 篇原有示例 + 10 篇真实数据），全部 `ready`；56 个 Wiki 词条，58 个来源锚点。
- 真实 `.doc` 由纯 Python 的 `legacy-doc` 解析（不执行宏/嵌入对象），无需 Word/LibreOffice。
- 真实 `.docx` / `.pdf` 分别由 `python-docx` / `pypdf` 解析。
- 真实 HTML 通过 URL 导入，保留 `source_url`，并编译为 Wiki 词条。

抽查问答（均返回真实文件引用）：

| 问题 | 引用来源 |
|---|---|
| 研究生课程教学管理规定对调课有什么要求？ | 《西北工业大学研究生课程教学管理规定》PDF |
| 博士后进站需要准备哪些材料？ | 《西北工业大学博士后进站审核表（非定向）》DOCX |
| 知识产权声明页需要填什么？ | 《西北工业大学知识产权声明页》DOCX |

## 三、格式支持

| 扩展名 | 解析器 | 状态 |
|---|---|---|
| `.pdf` | pypdf | 支持；扫描版无文本会明确报错，不做 OCR |
| `.docx` | python-docx | 支持 |
| `.doc` | legacy-doc（纯 Python，MIT） | v0.1.2 新增 |
| `.html/.htm` | BeautifulSoup | 支持 |
| `.md/.txt/.xlsx/.csv/.json` | 内置/openpyxl | 支持 |
| `.ppt/.pptx` | — | 暂不实现（按约定） |

## 四、测试

- `backend/tests/test_parsing.py` 使用本目录三个真实样本（`.doc`/`.docx`/`.pdf`）做解析回归；样本位于 `backend/tests/fixtures/campus/`，来源与许可说明见同目录 README。
- 全量 pytest：**115 passed**。
- UI：内容管理 → 源文件可看到 20 条；知识库词条 56 条；真实问答引用正常。

## 五、采集说明与限制

- 附件下载走公开页面的正常会话（先访问详情页，再带 Cookie/Referer 下载）；不是绕过验证码。
- 部分栏目有 WAF（如 `xxgk` 直接 PDF 对脚本返回 403），本次只保留能稳定取得且可解析的公开材料。
- 未实现“定时栏目爬取/全站爬虫”；本批数据通过人工/脚本一次性采集并导入，后续可按 `docs/MOBILE-OPS.md` 的受控采集方案扩展。
- 真实文件仅用于校内演示/测试；如学校要求撤下，请删除 `backend/data/campus_seed/files/` 与对应 `documents` 记录。
