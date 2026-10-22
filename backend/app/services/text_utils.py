"""文本工具：中文分词、标题归一化与 slug 生成。

LLM Wiki 改造后，经典 RAG 的 ``retrieval_core``（BM25）会被删除，但导航仍然需要
轻量的「关键词」通道，因此把分词、归一化等纯函数收敛到本模块：

* :func:`tokenize` —— jieba 分词 + 停用词 + 小写 + 中文 bigram，供关键词打分使用；
* :func:`normalize_title` —— 去空白/标点/全半角差异，用于标题与别名的精确匹配；
* :func:`slugify` —— 由归一化标题生成稳定、唯一的页面 slug（中文保留）。

本模块不依赖数据库，也不依赖任何旧 RAG 模块，可被单测直接调用。
"""
from __future__ import annotations

import hashlib
import re
import string
import unicodedata

import jieba

# 中文 / 英文数字片段
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ALNUM_RE = re.compile(r"[a-zA-Z0-9]+")

# 归一化时删除的字符：空白、常见中英文标点、Markdown 装饰符等。
# 注意：中文字符与字母数字保留，英文统一小写。
_NORMALIZE_RE = re.compile(
    r"[\s\u3000\u00a0"
    r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"
    r"，。、；：？！“”‘’（）【】《》〈〉「」『』—…·•～￥"
    r"]+"
)

# 停用词：中文校园问答中的功能词；内容词（申请/材料/流程等）保留。
STOPWORDS = frozenset(
    {
        "的", "了", "是", "在", "和", "与", "及", "或", "也", "就", "都", "而",
        "被", "把", "我", "你", "他", "她", "它", "我们", "你们", "他们",
        "自己", "这个", "那个", "这些", "那些", "吗", "呢", "啊", "呀", "吧",
        "哦", "嗯", "么", "什么", "怎么", "怎样", "如何", "哪些", "哪个",
        "请问", "一下", "可以", "能否", "是否", "需要", "应该", "一个", "现在",
        "然后", "以及", "对于", "关于", "如果", "因为", "所以", "但是", "而且",
        "还有", "已经", "进行", "相关", "有没有", "怎么样", "怎么办", "多少",
        "一般", "具体", "主要", "包括", "以及", "等等",
    }
)

_PUNCT_TABLE = str.maketrans({c: " " for c in string.punctuation})


def tokenize(text: str) -> list[str]:
    """把中英文混合文本切成小写内容 token。

    中文：jieba 分词；长度 > 3 的词额外补字符 bigram，让「奖学金」也能命中
    「国家奖学金」。英文/数字：按字母数字串切分并小写。停用词与单字虚线会被丢弃。
    """
    if not text:
        return []
    text = str(text).translate(_PUNCT_TABLE).lower()
    tokens: list[str] = []
    for raw in jieba.lcut(text):
        token = raw.strip()
        if not token or token in STOPWORDS:
            continue
        if _CJK_RE.search(token):
            token = token.lower()
            if len(token) <= 3:
                tokens.append(token)
                continue
            tokens.append(token)
            tokens.extend(token[i : i + 2] for i in range(len(token) - 1))
        else:
            for word in _ALNUM_RE.findall(token):
                if len(word) > 1 or word.isdigit():
                    tokens.append(word.lower())
    return [token for token in tokens if token and token not in STOPWORDS]


def normalize_title(text: str) -> str:
    """归一化标题/别名：全半角统一、小写、去掉空白与标点。

    例：``"国家奖学金（本科） "`` → ``"国家奖学金本科"``。
    """
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).strip().lower()
    value = _NORMALIZE_RE.sub("", value)
    return value


def slugify(title: str) -> str:
    """由标题生成稳定 slug（中文保留）；空标题回退为 ``page``。

    超长标题截断后追加短哈希，避免不同长标题截断后撞车。
    """
    normalized = normalize_title(title)
    if not normalized:
        normalized = "page"
    if len(normalized) > 80:
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]
        normalized = f"{normalized[:71]}-{digest}"
    return normalized


__all__ = ["STOPWORDS", "tokenize", "normalize_title", "slugify"]
