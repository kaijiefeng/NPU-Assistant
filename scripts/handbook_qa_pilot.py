"""M4：生成 50 条学生手册试点 QA（自动引用校验，待人工复核）。"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "docs/npu-handbook/sources.json"
CORPUS = ROOT / "docs/npu-handbook/corpus"
OUT = ROOT / "docs/npu-handbook/qa-pilot.jsonl"
REPORT = ROOT / "docs/npu-handbook/QA-PILOT.md"

# (question, answer, category, doc_id, quote_hint)

SOURCE_URL_OVERRIDES = {
    "f65790e38f3446c699e7ba772bbf516b": "https://xxgk.nwpu.edu.cn/__local/F/B7/39/20D41D6A308873F67B60F81E423_1281287B_316BC.pdf",
    "2800693fea3c403a9350ffde2e54e0bf": "https://cailiao.nwpu.edu.cn/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=1874428052&wbfileid=1C178D8D6B6DD2B07F4F2C2CA6DD0A6A",
    "a3af07f17ace4d5e9226f82569b914a6": "https://cailiao.nwpu.edu.cn/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=1874428052&wbfileid=AEE6861E25D769987E61218F104C6592",
    "fd5675d717464c5b89e62b5bbffd6ee9": "https://houqin.nwpu.edu.cn/info/1011/177191.htm",
    "d6107b14ae4644f0ad3bc732c7de800e": "https://mp.weixin.qq.com/s?src=11&timestamp=1793065858&ver=7051&signature=05-TKxoKsQo9fsGFnUagoxfbfJcTKvVCbFT",

    "f80cf103b9384787b68a9adbf0a4ac2c": "https://xxgk.nwpu.edu.cn/__local/F/B7/39/20D41D6A308873F67B60F81E423_1281287B_316BC.pdf",
    "291e78af8e414b20b23fc55544a66704": "https://xxgk.nwpu.edu.cn/__local/F/B7/39/20D41D6A308873F67B60F81E423_1281287B_316BC.pdf",
    "42adb5ec35db411cb13267b19a07f168": "https://xxgk.nwpu.edu.cn/__local/F/B7/39/20D41D6A308873F67B60F81E423_1281287B_316BC.pdf",
    "725f364d6d5d4fc9a76e0d8da1ae7e68": "https://xl.nwpu.edu.cn/gzdt/rcgz/2.htm",
    "26094739a44b44188d7e4e5737a7beac": "https://dianzi.nwpu.edu.cn/info/1503/11568.htm",
    "7607e23973e3401681d7db6fb2c6dabb": "https://hangtian.nwpu.edu.cn/info/1804/23641.htm",
    "2869932c939142c8b7b1c2074cb32b4f": "https://yingxin.nwpu.edu.cn/info/1007/1021.htm",
    "44abd0d02c644d4aad5f43bd98941d97": "https://houqin.nwpu.edu.cn/info/1011/177191.htm",
    "07b160e793994d0cba736cdb212c5716": "https://yingxin.nwpu.edu.cn/info/1007/1021.htm",
    "ae44571fdbd345fda55b442cbaffba83": "https://houqin.nwpu.edu.cn/info/1011/154451.htm",
    "6b87aa64b08045b8998b7e1d88ee6c8e": "https://it.nwpu.edu.cn/info/2051/20881.htm",
}

SEEDS = [
    ("本科生什么情况下可以申请休学？", "学生因个人身心健康状况问题、自费出国留学或从事创新创业活动等原因不能在校学习，可以申请休学；休学一般由本人申请、学院签署意见、报教务部审批。", "学籍管理", "f65790e38f3446c699e7ba772bbf516b", "学生因个人身心健康状况问题、自费出国留学"),
    ("本科生休学后怎么复学？", "学生原则上应在保留学籍或休学期满当学期开学第一周内提交复学申请，经学院签署意见、教务部审批后办理复学；因病休学的还需提供二级甲等以上医院康复诊断。", "学籍管理", "f65790e38f3446c699e7ba772bbf516b", "学生原则上应当在保留学籍或休学期满当学期"),
    ("本科生转专业需要什么条件？", "学生在校期间对其他专业有兴趣和专长可以申请转专业；特殊招生形式录取或录取前有明确约定的不得转专业；在校期间只能转专业或调整专业一次。", "学籍管理", "42adb5ec35db411cb13267b19a07f168", "学生在校学习期间对其他专业有兴趣和专长"),
    ("本科生请假需要什么手续？", "学生因病或其他原因不能按时参加教学活动，必须事先办理请假手续，向学籍所在学院提交书面申请并附证明；30天以内由学院批准并报教务部、学工部备案，超过1个月应申请休学。", "教学与考试", "f80cf103b9384787b68a9adbf0a4ac2c", "学生请假应事先向学籍所在学院提交书面申请"),
    ("本科生请假结束后要销假吗？", "请假结束后，学生应按学院要求按时返校并办理销假，及时向辅导员报到，确认请假状态闭环；具体销假方式以学院通知为准。", "教学与考试", "f80cf103b9384787b68a9adbf0a4ac2c", "请假结束后，学生应按学院要求按时返校并办理销假"),
    ("本科生课程不及格可以补考吗？", "考核成绩不合格的课程，可以参加下一学期开学初同一课程的补考，成绩记录标注“补”；补考仍不合格的须重修。", "教学与考试", "291e78af8e414b20b23fc55544a66704", "学生考核成绩不合格的课程，可以参加下一学期开学初"),
    ("本科生什么情况可以申请缓考？", "学生因病或其他特殊原因不能参加期末考试，可以申请下一学期初的缓考，具体按《西北工业大学本科生考试管理办法》执行。", "教学与考试", "291e78af8e414b20b23fc55544a66704", "学生因病或其他特殊原因不能参加期末考试"),
    ("本科生重修有哪些规定？", "补考仍不合格的须重修；考试违纪、取消资格或缺考的课程不得补考，必须重修；重修成绩记录标注“重”。", "教学与考试", "291e78af8e414b20b23fc55544a66704", "补考仍不合格的须重修"),
    ("研究生课程调课需要什么手续？", "任课教师因故不能按时完成教学任务或需临时调整课程时，须提前3天登录管理系统提交调课申请，经学院主管院长审核批准；公共类课程还须经研究生院同意。", "教学与考试", "2800693fea3c403a9350ffde2e54e0bf", "任课教师因故不能按时完成教学任务"),
    ("研究生缓考怎么申请？", "研究生因故不能按时参加考核，应在考核前向学院教学管理部门申请缓考，经学院批准；公共课还须报研究生院审批备案并通知任课教师。", "教学与考试", "2800693fea3c403a9350ffde2e54e0bf", "缓考"),
    ("研究生课程不及格怎么办？", "研究生课程考核不及格可按学校规定补考或重修；具体申请时间和次数以研究生课程教学管理规定及学院通知为准。", "教学与考试", "2800693fea3c403a9350ffde2e54e0bf", "不及格"),
    ("学位论文评阅意见怎么处理？", "学位论文评阅意见按《西北工业大学学位四会关于学位论文评阅意见的处理办法（修订版）》处理，学校根据评阅结果决定修改、重新评阅或答辩安排。", "学位与论文", "a3af07f17ace4d5e9226f82569b914a6", "评阅意见"),
    ("学校什么时候审议学位授予？", "学校按年度安排校学位评定委员会审议学位授予工作，具体时间以研究生院发布的《2026年校学位评定委员会审议学位授予工作时间安排》为准。", "学位与论文", "3d7989f8c5c245528fc74cf5832f956a", "学位评定委员会"),
    ("国家奖学金申请的基本条件是什么？", "申请国家奖学金须具有中华人民共和国国籍和西北工业大学学籍，热爱祖国、遵守法律法规和校纪校规、诚实守信、道德品质优良，并满足相应学习成绩和综合考评要求。", "奖助与资助", "533f16faff554f6790b5900999073018", "国家奖学金"),
    ("国家助学金主要面向哪些学生？", "国家助学金面向家庭经济困难的全日制本专科学生，用于资助其基本生活支出；具体申请条件、等级和评审程序按学校国家奖助学金评审办法执行。", "奖助与资助", "533f16faff554f6790b5900999073018", "国家助学金"),
    ("家庭经济困难认定有哪些等级？", "家庭经济困难学生认定根据学生家庭经济状况、日常消费等因素综合评定，具体等级和认定依据按《西北工业大学家庭经济困难学生认定办法》执行。", "奖助与资助", "90e18e378faf4687a034bbae33b4daa6", "家庭经济困难"),
    ("学生临时困难补助的申请条件是什么？", "学生因突发变故、重大疾病等原因导致基本生活困难，可以申请临时困难补助；具体条件和审批程序按《学生临时困难补助发放管理办法》执行。", "奖助与资助", "e5863edd4e7341dfae71f654a3ae7857", "临时困难补助"),
    ("新生入学绿色通道怎么申请？", "家庭经济困难新生可通过“绿色通道”先办理入学手续，再按学校资助政策申请助学贷款、助学金等；具体流程按《新生入学“绿色通道”管理办法》执行。", "奖助与资助", "c0b191a9a0f549769179c6e6f4afc6fb", "绿色通道"),
    ("生源地助学贷款回执要交到哪里？", "学生申请生源地助学贷款后，需将助学贷款回执交至党委学生工作部学生资助服务中心，学校按批次汇总并冲抵学杂费。", "奖助与资助", "352b6c4dd08d4f34a226c1807099730a", "助学贷款回执"),
    ("校园地助学贷款怎么办理？", "校园地助学贷款由中国银行承办，学生通过中国银行手机银行申请当年国家助学贷款并签订电子合同，学杂费由银行分批转入学校贷款专用账户。", "奖助与资助", "352b6c4dd08d4f34a226c1807099730a", "校园地助学贷款"),
    ("学生违纪处分有哪几种？", "纪律处分包括警告、严重警告、记过、留校察看、开除学籍五种；学校还可视情节给予口头批评、通报批评等批评教育。", "纪律与申诉", "3c5ce6e3bcc048e5b6408fe5a7e940f4", "纪律处分包括以下五种"),
    ("学生对处分不服怎么申诉？", "学生对处分决定不服，可按学校学生申诉处理规定提出申诉；申诉应在规定期限内提交书面材料，学校按规定受理并作出复查结论。", "纪律与申诉", "3c5ce6e3bcc048e5b6408fe5a7e940f4", "申诉"),
    ("学生公寓可以调换宿舍吗？", "在校期间宿舍和床位原则上不作调换；确因特殊原因必须调换的，须提前申请，经学院审批、学生公寓管理中心备案后方可入住新床位。", "宿舍与生活", "d45631d951334583970c7d8875119cc5", "调换"),
    ("学生退宿怎么办理？", "学生退宿应按学生公寓管理办法办理退宿手续，结清费用、交还钥匙并接受公寓检查；具体流程以学生公寓管理中心通知为准。", "宿舍与生活", "d45631d951334583970c7d8875119cc5", "退宿"),
    ("宿舍洗浴热水什么时间供应？", "学生公寓楼内浴室热水供应时间通常为8:00—12:00、18:00—24:00；友谊校区学生区集中公共浴室为16:00—23:30，节假日或军训期间可能临时调整。", "宿舍与生活", "992d334c8aa043989d9c77f76ee0c922", "热水供应时间"),
    ("宿舍洗衣机怎么使用？", "每栋宿舍楼一楼设有公共洗衣房，配备全自动洗衣机；使用时打开支付宝扫描洗衣机上的二维码，选择清洗模式即可。", "宿舍与生活", "992d334c8aa043989d9c77f76ee0c922", "洗衣房"),
    ("宿舍空调怎么办理？", "学生宿舍空调需学生自行租赁，按学校后勤/宿管通知缴纳押金并办理租赁手续，支持微信、支付宝等方式付款；具体租赁点以公寓管理中心通知为准。", "宿舍与生活", "2869932c939142c8b7b1c2074cb32b4f", "空调"),
    ("宿舍水电设施坏了怎么报修？", "水、电、暖报修及突发事件应急处置电话：友谊校区88491244，长安校区88430914；物业服务电话88493834。", "宿舍与生活", "208908fa68af492eaad7ae5c92fd762b", "报修"),
    ("宿舍门禁和晚归怎么规定？", "宿舍楼门禁时间通常为6:00—23:30；大门夜间上锁后，晚归学生可呼叫值班员开门，公寓24小时供电。", "宿舍与生活", "208908fa68af492eaad7ae5c92fd762b", "门禁"),
    ("宿舍楼有自习室吗？", "宿舍楼每层设有一间自习室（云天苑在2楼），供学生复习、看书和安静学习使用；考试周建议错峰使用。", "宿舍与生活", "7607e23973e3401681d7db6fb2c6dabb", "学校宿舍楼每层设有一间自习室"),
    ("学校食堂（餐厅）有哪些？", "长安校区有云天苑、星天苑南、星天苑北、海天苑等餐厅；友谊校区有学生一餐厅、学生二餐厅、风味小吃城、民族餐厅、教工餐厅等。", "宿舍与生活", "07b160e793994d0cba736cdb212c5716", "餐厅"),
    ("学校超市营业到几点？", "长安校区大学生超市正常营业时间8:30—23:00，地点在星天苑南餐厅1楼；友谊校区大学生超市8:30—21:30。", "宿舍与生活", "44abd0d02c644d4aad5f43bd98941d97", "大学生超市"),
    ("学校快递点在哪里？", "长安校区快递驿站集中在云天苑B座和长安体育馆东侧：顺丰、申通在体育馆东侧，圆通、菜鸟、京东派等在云天苑B座。", "宿舍与生活", "fd5675d717464c5b89e62b5bbffd6ee9", "快递驿站"),
    ("校园里电动车在哪里充电？", "长安校区海天苑3号楼东北角自行车棚设有智能共享充电桩，12个插口，扫码付款使用；请勿将电池带入宿舍或私拉电线。", "宿舍与生活", "ae44571fdbd345fda55b442cbaffba83", "充电桩"),
    ("学校哪里可以打印复印？", "宿舍楼一楼设有自助打印机，可黑白、彩印和照片打印；打印量较大时可去星天苑A楼对面大学生综合超市进门左手边打印点。", "宿舍与生活", "d6107b14ae4644f0ad3bc732c7de800e", "自助打印机"),
    ("校园卡丢了怎么挂失和补办？", "校园卡丢失后可在“西北工业大学”APP自行挂失，然后到启真楼一楼学生一站式事务服务大厅补办；补办需本人有效身份证件，工本费按学校规定执行。", "网络与校园卡", "070a65b6db814db69327c9b720e2c7e3", "挂失"),
    ("校园卡怎么充值？", "可通过“西北工业大学”APP登录后点击“充值”线上充值，也可通过校园卡服务窗口、自助设备办理；校园卡须先充值后消费。", "网络与校园卡", "070a65b6db814db69327c9b720e2c7e3", "充值"),
    ("校园网怎么连接和认证？", "无线网连接NWPU-YOUYI（友谊校区）或NWPU-CHANGAN（长安校区），在认证框输入深澜用户名和密码；未激活账号先通过zizhu.nwpu.edu.cn激活。", "网络与校园卡", "6b87aa64b08045b8998b7e1d88ee6c8e", "NWPU-YOUYI"),
    ("校园网网费怎么交？", "校园网费可通过“西北工业大学”APP或翱翔门户的公寓网费缴纳入口线上缴纳；也可通过一卡通圈存机或财务统一支付平台缴费。", "网络与校园卡", "992d334c8aa043989d9c77f76ee0c922", "网费"),
    ("图书馆开放时间是什么？", "图书馆按冬春季（10月1日—4月30日）和夏秋季（5月1日—9月30日）执行不同开馆时间，具体各馆/阅览室时间见图书馆开馆时间表。", "图书馆与文体", "1b13951b18624c1e81ae016e5b418cb8", "开馆时间"),
    ("体育场馆怎么预约？", "两校区体育场馆可通过学校体育场馆个人预约平台预约，具体开放时段、收费和预约规则以体育部平台说明为准。", "图书馆与文体", "94ad9924d83e4f71b08dad6672e05365", "预约"),
    ("校车时刻表在哪里查？", "校车时刻表可在迎新网、网络空间安全学院“校车时刻”页面查看；寒暑假和节假日运行时间会另行通知。", "校历与出行", "6cf7205468574f5bbe28c16cb9f5ab5d", "校车"),
    ("学校校历在哪里查？", "校历可在迎新网“西北工业大学校历、校车时刻表”页面查看，具体学期安排以教务处/学校最新通知为准。", "校历与出行", "0f351eb86e3343ada8241c55a55ba37e", "校历"),
    ("大学生医保门诊怎么报销？", "正常参保学生在校医院两校区门诊就医时，持医保电子凭证到收费窗口缴费可直接医保报销；未能直报的，携带医保电子凭证、发票等材料到校医院医保办审核报销。", "医疗与医保", "98253266b990456bbc4922f9fb62525d", "门诊"),
    ("大学生医保住院怎么报销？", "正常参保学生持医保电子凭证在西安市医保定点医院办理入院，出院结算时可直接医保报销；待遇期内未报销的住院费用可提交材料到校医院医保办审核。", "医疗与医保", "98253266b990456bbc4922f9fb62525d", "住院"),
    ("心理咨询怎么预约？", "学生心理健康教育中心实行预约制度，需提前一周预约；预约后按时到达，不能如期来访应提前电话说明，紧急情况可向接待人员说明及时安排。", "心理健康", "725f364d6d5d4fc9a76e0d8da1ae7e68", "提前一周预约"),
    ("就业三方协议怎么签？", "毕业生登录就业签约平台填写推荐表，与用人单位双向选择后提交电子协议书，单位签字盖章，学院核对纸质协议与电子协议后签章留存，最后提交就业中心编制就业计划。", "就业与档案", "26094739a44b44188d7e4e5737a7beac", "三方协议"),
    ("毕业生档案和报到证怎么办理？", "毕业生档案由学校档案馆按就业去向转递；报到证由就业中心编制就业计划后统一签发，学院在离校手续齐全后发放给本人。", "就业与档案", "ff783dd077ea445f9df4b18f0390ebf2", "档案"),
    ("新生户口迁入怎么办理？", "新生户口迁入按保卫处《新生户口迁入须知》办理，需准备户口迁移证等材料，具体时间、地点和材料以保卫处及录取通知书随附须知为准。", "安全与户籍", "b4c60051fb5a4dab9585bba8ca8c319c", "新生报道后一个月内可办理落户"),
    ("校园内遇到紧急情况怎么报警？", "校园内遇到紧急情况可拨打学校保卫处24小时报警值班电话，或直接联系辅导员、公寓值班员；具体号码以保卫处最新公布为准。", "安全与户籍", "57a4d89e85ec4390b762d0cd7ccc94da", "报警值班电话"),
]


def load_corpus(doc_id: str) -> tuple[str, dict, str]:
    path = CORPUS / f"{doc_id}.md"
    text = path.read_text(encoding="utf-8")
    meta = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip().strip('"')
            body = parts[2]
    return body, meta, path.name


def page_at(body: str, pos: int) -> int | None:
    matches = list(re.finditer(r"<!-- page: (\d+) -->", body[:pos]))
    return int(matches[-1].group(1)) if matches else None


def sentence_around(body: str, pos: int) -> str:
    start = max(body.rfind("。", 0, pos), body.rfind("\n", 0, pos)) + 1
    end_candidates = [x for x in (body.find("。", pos), body.find("\n", pos)) if x != -1]
    end = min(end_candidates) + 1 if end_candidates else min(len(body), pos + 200)
    quote = re.sub(r"\s+", " ", body[start:end]).strip()
    if len(quote) < 10:
        quote = re.sub(r"\s+", " ", body[max(0, pos - 80) : pos + 160]).strip()
    return quote


def main() -> int:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in sources["documents"]}
    records = []
    errors = []
    for index, (question, answer, category, doc_id, hint) in enumerate(SEEDS, start=1):
        try:
            body, meta, _ = load_corpus(doc_id)
            pos = body.find(hint)
            if pos < 0:
                pos = body.lower().find(hint.lower())
            if pos < 0:
                raise ValueError(f"quote hint not found: {hint}")
            quote = sentence_around(body, pos)
            if hint not in quote and hint.lower() not in quote.lower():
                quote = re.sub(r"\s+", " ", body[pos : pos + 220]).strip()
            source = by_id.get(doc_id, {})
            record = {
                "id": f"npu-handbook-{index:04d}",
                "question": question,
                "answer": answer,
                "category": category,
                "answerable": True,
                "source": {
                    "document_title": source.get("title") or meta.get("title") or "",
                    "source_url": source.get("source_url") or SOURCE_URL_OVERRIDES.get(doc_id) or meta.get("source_url") or "",
                    "page": page_at(body, pos),
                    "section": "",
                    "quote": quote,
                    "sha256": meta.get("sha256") or "",
                    "effective_date": None,
                },
                "review": {"status": "draft", "reviewer": "", "reviewed_at": ""},
                "version": "2026.1-draft",
                "notes": "自动引用校验通过；待人工复核",
            }
            records.append(record)
        except Exception as exc:
            errors.append({"index": index, "question": question, "error": str(exc)})

    with OUT.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    categories = Counter(record["category"] for record in records)
    lines = [
        "# 学生手册试点 QA（M4 草案）",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- QA 数量：{len(records)}",
        f"- 引用校验通过：{len(records)}/{len(SEEDS)}",
        f"- 待人工复核：{len(records)}",
        "",
        "| 类别 | 数量 |",
        "|---|---:|",
    ]
    for category, count in categories.most_common():
        lines.append(f"| {category} | {count} |")
    if errors:
        lines += ["", "## 未生成", ""]
        for error in errors:
            lines.append(f"- #{error['index']} {error['question']}：{error['error']}")
    (ROOT / "docs/npu-handbook/QA-PILOT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "errors": errors, "categories": categories}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
