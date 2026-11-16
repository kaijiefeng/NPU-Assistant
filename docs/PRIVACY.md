# 个人信息保护

## 1. 回答侧：确定性拒绝

`backend/app/services/privacy.py` 在检索与模型调用之前拦截个人信息请求，命中后返回固定话术，
不检索 Wiki、不调用 LLM、不生成引用，并在 `retrieval` 中标记 `privacy=true`、
`reason=personal_info_blocked`。

拦截词包括：身份证、学号、成绩单、成绩排名、录取/考生/合格/拟录取名单、学生名单、花名册、
家庭住址、个人邮箱、报名号、准考证号、手机号、个人信息。

不拦截“电话/邮箱/名单”单独出现的政策类问题，例如“心理中心电话”“奖学金名单什么时候公示”；
公开办公邮箱/办公电话可以正常提供。

默认系统提示词第 6 条同步约束模型；已安装实例的 `system_prompt` 也已追加同一规则。

## 2. 数据侧：解析时脱敏

`backend/app/services/redaction.py` 在解析后、入库/编译前执行：

- 手机号、身份证号、个人邮箱、16–24 位长数字打码；
- 办公邮箱 `@nwpu.edu.cn`、`029-` 办公座机、办公人员姓名保留；
- 名单/成绩类文档直接拒绝（HTTP 400），不落库。

`scripts/build_sanitized_release.py` 再生成可公开的 `wiki_seed.json` 与 `seed_docs/`：

- 名单/成绩类文档整篇剔除；
- 手机号、身份证号、个人邮箱、超长数字打码；
- 办公邮箱（`@nwpu.edu.cn`）、办公座机与公开办公人员/联系人姓名保留（用于回答“找谁办/联系方式”）；
- PDF/DOC/DOCX 等二进制原件不进入发布包。

详见 [SOURCE-PROVENANCE-AUDIT.md](SOURCE-PROVENANCE-AUDIT.md)。

## 3. 测试

`backend/tests/test_privacy.py` 覆盖拦截、放行与拒绝响应结构。
