"""限定范围远程检索：纯解析测试（不访问网络）。"""
from __future__ import annotations

from app.services import remote_search

AOP_RESPONSE = {
    "code": "0000",
    "data": {
        "page": {
            "records": [
                {
                    "title": "关于 研究生 培养的通知",
                    "url": "//gs.nwpu.edu.cn/info/2141/1.htm",
                    "intro": "培养方案与课程安排",
                    "createtime": "2026-10-01",
                }
            ]
        }
    },
}

SOGOU_HTML = """
<ul class="news-list">
  <li>
    <div class="txt-box">
      <h3><a href="/link?url=abc">集成电路学院 研究生 导师培训</a></h3>
      <p class="txt-info">学院开展研究生导师培训，提升培养质量。</p>
      <div class="s-p"><span class="all-time-y2">西工大集成电路学院</span></div>
    </div>
  </li>
  <li>
    <div class="txt-box">
      <h3><a href="/link?url=def">其他账号文章</a></h3>
      <p class="txt-info">不应命中。</p>
      <div class="s-p"><span class="all-time-y2">其他公众号</span></div>
    </div>
  </li>
</ul>
"""

SOGOU_REDIRECT = """
<script>
setTimeout(function () {
  var url = '';
  url += 'https://mp.';
  url += 'weixin.qq.c';
  url += 'om/s?src=11';
  url += '&timestamp=1';
  window.location.replace(url)
},100);
</script>
"""

WECHAT_HTML = """
<html><body>
<h1 id="activity-name">集成电路学院研究生导师培训</h1>
<a id="js_name">西工大集成电路学院</a>
<em id="publish_time">2026-10-01</em>
<div id="js_content"><p>学院开展研究生导师培训，提升研究生培养质量。</p></div>
</body></html>
"""


def test_parse_aop_records():
    rows = remote_search.parse_aop_records(AOP_RESPONSE, "研究生院")
    assert rows[0]["url"] == "https://gs.nwpu.edu.cn/info/2141/1.htm"
    assert rows[0]["title"] == "关于 研究生 培养的通知"
    assert rows[0]["origin"] == "remote"


def test_parse_sogou_results_filters_account():
    rows = remote_search.parse_sogou_results(SOGOU_HTML, ["西工大集成电路学院"])
    assert len(rows) == 1
    assert rows[0]["account"] == "西工大集成电路学院"
    assert rows[0]["link"].startswith("https://weixin.sogou.com/link")


def test_parse_sogou_redirect_and_article():
    assert remote_search.parse_sogou_redirect(SOGOU_REDIRECT) == "https://mp.weixin.qq.com/s?src=11&timestamp=1"
    article = remote_search.parse_wechat_article(WECHAT_HTML)
    assert article["account"] == "西工大集成电路学院"
    assert "研究生培养质量" in article["content"]


def test_remote_sources_registry():
    data = remote_search.load_sources()
    assert len(data["sites"]) == 4
    assert len(data["wechat"]) == 4
    assert all(site["host"].startswith("https://") for site in data["sites"])
