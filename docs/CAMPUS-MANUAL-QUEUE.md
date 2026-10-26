# 人工下载 / 人工导入清单

这是与自动同步源分开的**人工队列**。自动同步只处理允许的公开列表页（当前仅研究生院）；下列条目因 WAF、页面结构、扫描件或附件限制，需人工在浏览器中打开/下载后导入。

机器可读清单：[`docs/campus-seed/manual-queue.json`](campus-seed/manual-queue.json)（23 条，P0/P1/P2）；本轮分流结果：[`manual-queue-results.json`](campus-seed/manual-queue-results.json)。

## 〇、本轮分流结果（2026-10-26）

按「互联网抓取 → 自动下载 → 人工接管」处理后的状态：

| 状态 | 数量 | 说明 |
|---|---:|---|
| `imported` | 14 | 直接抓取、CUA 复制、PDF/XLSX 附件导入 |
| `imported_ocr` | 5 | 本地 RapidOCR 识别后导入（奖助 4 份 + 绿色通道） |
| `already_present` | 2 | 学生违纪处分办法、学生公寓管理办法（此前已导入） |
| `needs_browser` | 2 | 就业网 412、心理中心列表页；留待人工/CUA |

- CUA 试验：用独立 Edge 配置打开“绿色通道”页面，`Ctrl+S` 成功下载 PDF（4 页，扫描件）→ 保存为 `backend/data/campus_manual/files/xgb-zdgk-13428.pdf`。
- OCR：5 份扫描 PDF 用本机 RapidOCR 识别，低置信度 0 行，全部导入成功。
- 本轮没有遇到验证码；如遇到，按约定暂停，由人工完成后再继续。

## 一、处理原则

- 用**普通浏览器**打开公开页面；不登录、不绕过验证码/WAF。
- 正文：复制到「内容管理 → 收录资料 → 文本」，标题用清单标题，标签加 `真实数据,人工导入,<领域>`。
- 附件：人工下载到 `backend/data/campus_manual/files/`（gitignored），再用「文件」上传；不要用脚本破解验证码。
- 导入后把 JSON 中对应项 `status` 改为 `done`，并在 `documents.source_url`（如可保留）记录来源。
- PPT/PPTX 按约定暂不处理；扫描件如无法复制，先 OCR 再文本收录。

## 二、P0（优先补齐，建议本周完成）

| 领域 | 标题 | 页面 |
|---|---|---|
| 奖助学金 | 国家奖助学金评审办法 | <https://xgb.nwpu.edu.cn/info/1227/13408.htm> |
| 奖助学金 | 家庭经济困难学生认定办法 | <https://xgb.nwpu.edu.cn/info/1227/13418.htm> |
| 奖助学金 | 本科生助学金实施细则 | <https://xgb.nwpu.edu.cn/info/1227/13448.htm> |
| 奖助学金 | 学生临时困难补助发放管理办法 | <https://xgb.nwpu.edu.cn/info/1227/13438.htm> |
| 日常管理 | 学生违纪处分管理办法 | <https://xgb.nwpu.edu.cn/info/1227/10612.htm> |
| 宿舍管理 | 学生公寓管理办法（试行） | <https://xgb.nwpu.edu.cn/info/1227/10600.htm> |

## 三、P1（下一批）

| 领域 | 标题 | 页面 |
|---|---|---|
| 奖助学金 | 新生入学“绿色通道”管理办法 | <https://xgb.nwpu.edu.cn/info/1227/13428.htm> |
| 奖助学金 | 本科生综合测评办法 | <https://xgb.nwpu.edu.cn/info/1227/11300.htm> |
| 奖助学金 | 本科生奖励办法 | <https://xgb.nwpu.edu.cn/info/1227/11299.htm> |
| 奖助学金 | 研究生奖助体系实施办法 | <https://xgb.nwpu.edu.cn/info/1227/11087.htm> |
| 安全应急 | 学生安全管理办法 | <https://xgb.nwpu.edu.cn/info/1227/10611.htm> |
| 学籍与日常管理 | 学生管理规定 | <https://xgb.nwpu.edu.cn/info/1227/10610.htm> |
| 心理健康 | 学生心理委员管理规定 | <https://xgb.nwpu.edu.cn/info/1227/10609.htm> |
| 办事流程 | 学生工作平台帮助手册 | <https://xgb.nwpu.edu.cn/info/1261/11046.htm> |
| 心理健康 | 2026级新生校园生活适应访谈工作全面启动 | <https://xl.nwpu.edu.cn/info/1009/4102.htm> |
| 校历校车 | 西北工业大学校历、校车时刻表（图片） | <https://xygg.nwpu.edu.cn/info/1003/22073.htm> |
| 学籍与注册 | 本科生学籍管理办法（扫描图片） | <https://xxgk.nwpu.edu.cn/info/1541/5921.htm> |

## 四、P2（有余力再补）

| 领域 | 标题 | 页面 |
|---|---|---|
| 心理健康 | 心理咨询师管理办法 | <https://xgb.nwpu.edu.cn/info/1227/10608.htm> |
| 宿舍管理 | 研究生新生提前入住公寓流程图 | <https://xgb.nwpu.edu.cn/info/1261/11047.htm> |
| 心理健康 | 2026级新生心理适应讲座 | <https://xl.nwpu.edu.cn/info/1009/4072.htm> |
| 信息公开 | 2024-2025学年信息公开年度报告 PDF | <https://xxgk.nwpu.edu.cn:8080/__local/8/3B/86/99636A5BCE1773BD7430977A235_A60C1D8B_48658.pdf> |
| 心理健康 | 心理中心通知/工作动态（自动抽取失败的 20 条） | <https://xl.nwpu.edu.cn/gzdt/tzgg.htm> |
| 就业 | 关于启用新版就业网的公告 | <https://job.nwpu.edu.cn/frontpage/nwpu/html/newsDetail.html?id=b1fad40a3f9c4b51acffdb0b7d0d51d3> |

## 五、为什么这些不能自动抓

| 原因 | 影响条目 | 处理 |
|---|---|---|
| xgb 列表对脚本 403（WAF） | 大部分 P0/P1 xgb 制度 | 浏览器人工打开；不伪装/换 IP |
| 心理中心详情页正文抽取失败 | xl 文章 | 浏览器复制正文 |
| 页面正文是图片/扫描件 | 校历、学籍办法 | 人工 OCR/复制 |
| 直链 PDF 对脚本 403 | 信息公开年报 | 浏览器下载 |
| 就业网 412/WAF | 就业公告 | 浏览器人工处理 |

## 六、与自动源的关系

- 自动源：见 [`backend/app/campus_sources.json`](../backend/app/campus_sources.json)，当前启用 `gs-tzgg`。
- 人工队列：本清单 + `manual-queue.json`，不计入自动同步，避免违规绕过。
- 导入后仍复用同一 `documents`/Wiki 编译/引用链路，来源 URL 能保留的尽量保留。
