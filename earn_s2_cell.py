# [새 정보원 3단계 셀] 잠정실적 본문 16,766건 전체 파싱 집계. 파서 = main의 earn_s1c_cell.py parse_earn 그대로(수정 없음). 읽기 전용, API 호출 없음, 수익률 등 결과 데이터 미사용.
# 전제: earn_s2_clone_cell.py 실행 완료(/content/main, /content/earn). 결과: Drive new_earn_s2(parse_all.csv·fail_dump.txt·summary.json).
# 범위: 공시명에 '자회사'·'월별' 있으면 범위 밖(수집 대상 규칙상 0건이어야 함) · 빈 표(영업이익 행 숫자 없음) = 범위 밖.
# 성공(1단계와 동일): 영업이익 당기·전년동기 숫자 ∧ 검산 가능한 매출액·영업이익 증감율 검산(1%p) 모두 일치.
# 판정(사전 고정): 범위 안 성공 ≥95% ∧ 모든 연도 범위 안 성공 ≥90% → 통과. 검산 일치율·검산 불가 성공·당기=전년동기 동일값은 참고 표시.
# 실패는 유형별로 분류만 하고 파서는 고치지 않음(수정 필요 시 별도 승인).
import os, re, io, json, time, zipfile, importlib.util
from multiprocessing import Pool
import numpy as np
import pandas as pd

MAIN = os.environ.get("EARN_MAIN", "/content/main")
ED = os.environ.get("EARN_DATA", "/content/earn/earn_data")
OUT = os.environ.get("EARN_S2_OUT", "/content/drive/MyDrive/new_earn_s2")
PASS_ALL, PASS_YEAR = 0.95, 0.90

os.environ["EARN_NO_MAIN"] = "1"
_spec = importlib.util.spec_from_file_location("earn_s1c", f"{MAIN}/earn_s1c_cell.py")
P1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P1)


def fmt_of(rows):
    hi = P1.find_header(rows)
    if hi is None:
        return None
    h = "".join(P1.nz(c) for c in rows[hi])
    if "흑자" in h or "적자" in h:
        return "흑자적자전환 열"
    if "증감액" in h:
        return "증감액(증감율)"
    if "증감" in h or "대비" in h:
        return "증감율 열"
    return "증감 열 없음"


def work(arg):
    year, rn, raw = arg
    rec = {"rcept_no": rn, "zip_year": year, "빈표": False, "err": None, "format": None, "enc": None}
    try:
        text, enc = P1.decode(raw)
        rec["enc"] = enc
        r = P1.parse_earn(text)
        rows = r.pop("table_rows")
        rec.update(r)
        rec["빈표"] = bool(r.get("empty"))
        rec["format"] = fmt_of(rows) if rows else None
        rec["표찾음"] = rows is not None
        if not rec["ok"] and not rec["빈표"]:
            rec["dump"] = "\n".join("  " + " | ".join(x) for x in (rows or [])[:14]) if rows else \
                "  (영업이익 표 못 찾음) 본문 앞부분: " + P1.tables(text)[1][:400]
    except Exception as ex:  # 파싱 예외도 실패로 기록
        rec.update(ok=False, checked=False, 표찾음=False, err=f"{type(ex).__name__}: {ex}"[:300])
    return rec


def sv(v):
    return v if isinstance(v, str) else ""


def fail_type(x):
    if isinstance(x.get("err"), str):
        return "예외"
    if not x.get("표찾음"):
        return "영업이익 표 못 찾음"
    if x.get("영업이익_cur") is None or x.get("영업이익_yoy") is None:
        return "영업이익 값 없음"
    return "검산 불일치"


def run():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    tg = pd.read_csv(f"{ED}/targets.csv", dtype=str, encoding="utf-8-sig")
    tg["year"] = tg["rcept_dt"].str.replace("-", "").str[:4]

    def jobs():
        for y in sorted(tg["year"].unique()):
            with zipfile.ZipFile(f"{ED}/raw/{y}.zip") as zf:
                for n in zf.namelist():
                    yield y, n[:-4], zf.read(n)

    recs, n = [], len(tg)
    with Pool(os.cpu_count() or 2) as pool:
        for i, rec in enumerate(pool.imap(work, jobs(), chunksize=16), 1):
            recs.append(rec)
            if i % 2000 == 0 or i == n:
                print(f"진행 {i:,}/{n:,} · {time.time()-t0:.0f}s", flush=True)
    R = pd.DataFrame(recs).merge(tg, on="rcept_no", how="left")
    for c in ["unit", "period", "매출액_cur", "매출액_yoy", "영업이익_cur", "영업이익_yoy", "당기순이익_cur", "당기순이익_yoy",
              "매출액_check", "영업이익_check", "dump", "empty"]:
        if c not in R:
            R[c] = None
    R["범위"] = ~R["report_nm"].fillna("").str.contains("자회사|월별") & ~R["빈표"]
    R["ok"] = R["ok"].fillna(False).astype(bool)
    R["checked"] = R["checked"].fillna(False).astype(bool)
    R["연결(공시명)"] = R["report_nm"].fillna("").str.replace(" ", "").str.contains("연결")
    assert len(R) == n and R["rcept_dt"].notna().all() and (R["zip_year"] == R["year"]).all(), "ZIP↔targets 대응 오류"

    I = R[R["범위"]].copy()
    I["fail"] = [None if o else fail_type(x) for o, x in zip(I["ok"], I.to_dict("records"))]
    print(f"\n전체 {n:,}건 · 공시명 범위 밖(자회사·월별) {int(R['report_nm'].fillna('').str.contains('자회사|월별').sum())} · "
          f"빈 표 {int(R['빈표'].sum())} · 범위 안 {len(I):,}")

    ys = []
    for y, g in I.groupby("year"):
        ch = pd.concat([g["매출액_check"], g["영업이익_check"]]).dropna().astype(bool)
        a = R[R["year"] == y]
        ys.append({"연도": y, "전체": len(a), "빈표": int(a["빈표"].sum()), "범위안": len(g), "성공": int(g["ok"].sum()),
                   "성공률%": round(100 * g["ok"].mean(), 2), "검산수": len(ch), "검산일치%": round(100 * ch.mean(), 2) if len(ch) else None,
                   "검산불가성공": int((g["ok"] & ~g["checked"]).sum()), "기간추출%": round(100 * g["period"].notna().mean(), 1),
                   "연결%(공시명)": round(100 * g["연결(공시명)"].mean(), 1)})
    Y = pd.DataFrame(ys)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print("\n[연도별 · 범위 안]\n" + Y.to_string(index=False))

    ch = pd.concat([I["매출액_check"], I["영업이익_check"]]).dropna().astype(bool)
    rate = I["ok"].mean()
    print(f"\n[범위 안 전체] 성공 {int(I['ok'].sum()):,}/{len(I):,} ({100*rate:.2f}%) · 검산 {len(ch):,}개 중 일치 {100*ch.mean():.2f}% · "
          f"검산 가능 공시 {int(I['checked'].sum()):,} · 검산 불가 성공 {int((I['ok'] & ~I['checked']).sum()):,} · 기간 추출 {100*I['period'].notna().mean():.1f}%")
    same = I["ok"] & (I["영업이익_cur"] == I["영업이익_yoy"])
    print(f"[참고] 성공 중 영업이익 당기=전년동기 동일값 {int(same.sum())}건 (열 어긋남 의심 지표)")

    print("\n[형식별 · 범위 안] " + " · ".join(f"{k}: {int(g['ok'].sum())}/{len(g)}" for k, g in I.groupby(I["format"].fillna("표 없음"))))
    print("[형식×연도] " + " | ".join(f"{y} " + ",".join(f"{k}{v}" for k, v in g["format"].fillna("표없음").value_counts().items())
                                     for y, g in I.groupby("year")))
    print("[단위 · 범위 안] " + " · ".join(f"{k}: {v:,}" for k, v in I["unit"].fillna("미추출").value_counts().items()))
    print("[인코딩] " + " · ".join(f"{k}: {v:,}" for k, v in R["enc"].fillna("예외").value_counts().items()))
    print("[실패 유형 · 범위 안] " + (" · ".join(f"{k}: {v}" for k, v in I["fail"].value_counts().items()) or "없음"))
    if I["fail"].notna().any():
        print("[실패 유형×연도] " + " | ".join(f"{y} " + ",".join(f"{k}{v}" for k, v in g["fail"].value_counts().items())
                                         for y, g in I[I["fail"].notna()].groupby("year")))
    dup = R.duplicated(["stock_code", "report_nm", "rcept_dt"], keep=False)
    print(f"[참고] (종목·공시명·접수일) 중복 공시 {int(dup.sum())}건 — 처리 규칙은 피처 사전등록에서")

    by_year_ok = (Y["성공률%"] >= 100 * PASS_YEAR).all()
    passed = rate >= PASS_ALL and by_year_ok
    print(f"\n3단계 판정: {'통과' if passed else '미달'} (기준: 범위 안 성공 ≥{100*PASS_ALL:.0f}% → {100*rate:.2f}% · "
          f"모든 연도 ≥{100*PASS_YEAR:.0f}% → 최저 {Y['성공률%'].min():.2f}%)")

    F = I[I["fail"].notna()]
    with open(f"{OUT}/fail_dump.txt", "w", encoding="utf-8") as f:
        for (y, t), g in F.groupby(["year", "fail"]):
            for _, x in g.head(5).iterrows():
                f.write(f"### [{y} {t}] {x['rcept_no']} {x['corp_name']} {x['rcept_dt']} [{x['report_nm']}] {sv(x['err'])}\n{sv(x['dump'])}\n\n")
    shown = 0
    for t, g in F.groupby("fail"):
        for _, x in g.head(2).iterrows():
            print(f"\n### 실패 예시 [{t}] {x['rcept_no']} {x['corp_name']} {x['rcept_dt']} [{x['report_nm']}] {sv(x['err'])}")
            print("\n".join(sv(x["dump"]).splitlines()[:10]))
            shown += 1
    keep = [c for c in R.columns if c != "dump"]
    R[keep].to_csv(f"{OUT}/parse_all.csv", index=False, encoding="utf-8-sig")
    json.dump({"n": n, "in_scope": len(I), "ok": int(I["ok"].sum()), "rate": rate, "passed": bool(passed),
               "by_year": Y.to_dict("records"), "fail_types": I["fail"].value_counts().to_dict()},
              open(f"{OUT}/summary.json", "w"), ensure_ascii=False, indent=1, default=str)
    print(f"\n저장: {OUT}/parse_all.csv · fail_dump.txt · summary.json · 소요 {time.time()-t0:.0f}s")
    return R


if os.environ.get("EARN_S2_NO_MAIN") != "1":
    try:
        from google.colab import drive
        drive.mount("/content/drive")
    except Exception:
        pass
    R_all = run()
