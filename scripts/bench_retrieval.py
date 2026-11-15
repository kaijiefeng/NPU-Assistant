import argparse, json, os, shutil, statistics, sys, tempfile, time
from pathlib import Path

QUERIES = [
    "国家奖学金怎么申请？需要哪些材料？", "请假流程是什么？病假需要什么证明？", "心理中心怎么预约？",
    "宿舍报修在哪里提交？", "校园卡丢了怎么补办？", "转专业需要什么条件？", "保研政策是什么？",
    "毕业生档案和报到证怎么办？", "医保报销流程", "补考和重修规定", "图书馆开放时间",
    "助学金申请材料", "户口迁移怎么办？", "就业协议怎么签？", "四六级报名时间",
    "学费怎么缴纳？", "军训什么时候开始？", "实验室安全规定", "交换生项目申请", "毕业审核要求",
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="npu-bench-"))
    shutil.copyfile(args.db, tmp / "app.db")
    os.environ["NPU_DATA_DIR"] = str(tmp)
    os.environ["NPU_DB_PATH"] = str(tmp / "app.db")
    os.environ["NPU_AUTO_SEED"] = "0"
    sys.path.insert(0, "backend")
    from app import db
    from app.services import wiki_nav
    wiki_nav._llm_available = lambda: False

    t0 = time.perf_counter()
    pages = wiki_nav._load_ready_pages()
    load_ms = (time.perf_counter() - t0) * 1000

    def bench(fn, n=5):
        for q in QUERIES[:2]:
            fn(q)
        times = []
        for q in QUERIES:
            start = time.perf_counter()
            for _ in range(n):
                fn(q)
            times.append((time.perf_counter() - start) / n * 1000)
        times.sort()
        return {
            "mean": round(statistics.fmean(times), 2),
            "p50": round(times[len(times)//2], 2),
            "p95": round(times[max(0, int(len(times)*0.95)-1)], 2),
            "max": round(times[-1], 2),
        }

    search = bench(lambda q: wiki_nav.search_pages(q, limit=15))
    select = bench(lambda q: wiki_nav.select_pages_with_method(q, max_pages=3))
    row = db.query_one("SELECT COUNT(*) n, SUM(LENGTH(body_md)) chars FROM wiki_pages WHERE status='ready'")
    print(json.dumps({"label": args.label, "pages": row["n"], "body_chars": row["chars"],
                      "load_ms": round(load_ms, 2), "search_pages": search, "select_pages": select}))

if __name__ == "__main__":
    main()
