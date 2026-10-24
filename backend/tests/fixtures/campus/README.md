# 真实校园文件解析样本

本目录的 `.doc` / `.docx` / `.pdf` 文件来自西北工业大学公开网页，仅用于
`backend/tests/test_parsing.py` 的解析器回归测试，不参与知识库编译或对外分发。

| 文件 | 来源页 | 说明 |
|---|---|---|
| `西工大-在校学习基本情况表.doc` | <https://cailiao.nwpu.edu.cn/info/2643/31676.htm> | 材料学院公开资料下载中的 Word 97-2003 表单 |
| `西工大-知识产权声明页.docx` | <https://cailiao.nwpu.edu.cn/info/2643/31676.htm> | 同一页面的 docx 附件 |
| `西工大-学位论文评阅意见处理办法.pdf` | <https://cailiao.nwpu.edu.cn/info/2643/31676.htm> | 同一页面的 PDF 附件 |

这些页面均为公开可访问页面；如学校要求撤下，请删除对应 fixture 并同步调整测试。
