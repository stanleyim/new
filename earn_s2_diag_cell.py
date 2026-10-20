# [새 정보원 3단계 진단 셀] earn_s2 실패 건 사실 확인. 파서(earn_s1c_cell.py) 무수정·레포 무변경·API 호출 없음·수익률 미사용.
# 전제: 같은 런타임에 /content/main·/content/earn(earn_s2_clone_cell.py) + Drive new_earn_s2/parse_all.csv. 런타임이 초기화됐으면 클론 셀부터 다시 실행.
# ① 범위 안 실패 재분류(earn_s2의 NaN 분류 버그 수정): 예외 / 표 못 찾음 / 전년동기 값 없음 / 당기 값 없음 / 괄호 없는 증감율(끝 % 또는 마지막 숫자로 재검산 시 일치) / 값 불일치(재검산도 불일치)
#    → 연도·형식별 건수, 유형별 표 원문 예시. 참고: "괄호 없는 증감율을 성공으로, 전년동기 값 없음을 범위 밖으로" 가정 시 성공률(추정, 판정 아님)
# ② 표 못 찾음 원문 바이트 확인(앞 120바이트·XML 인코딩 선언·U+FFFD 바이트 수·cp949/euc-kr 엄격 디코딩 가능 여부)
# ③ 성공 중 영업이익 당기=전년동기 동일값·검산 불가 성공 건 표 원문
import os, re, io, json, time, zipfile, importlib.util
import pandas as pd

MAIN = os.environ.get("EARN_MAIN", "/content/main")
ED = os.environ.get("EARN_DATA", "/content/earn/earn_data")
OUT = os.environ.get("EARN_S2_OUT", "/content/drive/MyDrive/new_earn_s2")

os.environ["EARN_NO_MAIN"] = "1"
_spec = importlib.util.spec_from_file_location("earn_s1c", f"{MAIN}/earn_s1c_cell.py")
P1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P1)

# 검산 칸 원문 기록용 감싸기(값 계산은 원래 함수 그대로)
_orig_pct, _orig_check = P1.pct, P1.check
_cap = {"last_s": None, "calls": []}
def _pct(s):
    _cap["last_s"] = s
    return _orig_pct(s)
def _check(cur, yoy, p):
    r = _orig_check(cur, yoy, p)
    _cap["calls"].append((cur, yoy, _cap["last_s"], r))
    return r
P1.pct, P1.check = _pct, _check

PCT_TAIL = re.compile(r"([+△\-]?\s*\d[\d,]*(?:\.\d+)?)\s*%")
NUMS = re.compile(r"[+△\-]?\s*\d[\d,]*(?:\.\d+)?")


def alt_pct(s):
    """대안 읽기(진단용): 마지막 'n%' → 없으면 숫자가 2개 이상일 때 마지막 숫자."""
    s = s or ""
    m = PCT_TAIL.findall(s)
    if m:
        return P1.num(m[-1])
    n = NUMS.findall(s)
    return P1.num(n[-1]) if len(n) >= 2 else None


def b(v):
    return str(v) == "True"


def isnum(v):
    return v is not None and not (isinstance(v, float) and pd.isna(v))


def rows_text(rows, k=12):
    return "\n".join("  " + " | ".join(r) for r in (rows or [])[:k])


def run():
    t0 = time.time()
    R = pd.read_csv(f"{OUT}/parse_all.csv", dtype={"rcept_no": str, "rcept_dt": str, "stock_code": str, "year": str})
    for c in ("ok", "범위", "checked", "빈표", "표찾음"):
        R[c] = R[c].map(b)
    I = R[R["범위"]]
    F = I[~I["ok"]]
    S_same = I[I["ok"] & (I["영업이익_cur"] == I["영업이익_yoy"])]
    S_nochk = I[I["ok"] & ~I["checked"]]
    need = set(F["rcept_no"]) | set(S_same["rcept_no"]) | set(S_nochk["rcept_no"])
    print(f"범위 안 {len(I):,} · 실패 {len(F):,} · 동일값 성공 {len(S_same)} · 검산 불가 성공 {len(S_nochk)} · 원문 읽기 {len(need):,}건")

    raw = {}
    for y in sorted(R.loc[R["rcept_no"].isin(need), "year"].unique()):
        with zipfile.ZipFile(f"{ED}/raw/{y}.zip") as zf:
            for rn in R.loc[(R["year"] == y) & R["rcept_no"].isin(need), "rcept_no"]:
                raw[rn] = zf.read(f"{rn}.xml")

    recs, dumps = [], {}
    for x in F.to_dict("records"):
        rn = x["rcept_no"]
        rec = {"rcept_no": rn, "year": x["year"], "format": x["format"] if isinstance(x["format"], str) else "표 없음"}
        text, _ = P1.decode(raw[rn])
        _cap["calls"].clear()
        try:
            r = P1.parse_earn(text)
        except Exception as ex:
            rec["유형"] = "예외"; rec["detail"] = f"{type(ex).__name__}: {ex}"[:200]
            recs.append(rec); continue
        dumps[rn] = r.get("table_rows")
        cur, yoy = r.get("영업이익_cur"), r.get("영업이익_yoy")
        if r.get("table_rows") is None:
            rec["유형"] = "표 못 찾음"
        elif cur is not None and yoy is None:
            rec["유형"] = "전년동기 값 없음"
        elif cur is None:
            rec["유형"] = "당기 값 없음"
        else:
            bad, fixed = [], []
            for item in ("매출액", "영업이익"):
                if r.get(f"{item}_check") is False:
                    c_ = [c for c in _cap["calls"] if c[0] == r.get(f"{item}_cur") and c[1] == r.get(f"{item}_yoy")]
                    s = c_[-1][2] if c_ else None
                    ap = alt_pct(s)
                    ok2 = _orig_check(r.get(f"{item}_cur"), r.get(f"{item}_yoy"), ap)
                    bad.append(f"{item}: 칸 '{s}' → 원래 {_orig_pct(s)} / 대안 {ap}")
                    fixed.append(ok2 is True)
            rec["유형"] = "괄호 없는 증감율(재검산 일치)" if fixed and all(fixed) else "값 불일치(재검산도 불일치)" if fixed else "기타"
            rec["detail"] = " ; ".join(bad)
        recs.append(rec)
    D = pd.DataFrame(recs)
    D = D.merge(F[["rcept_no", "corp_name", "rcept_dt", "report_nm"]], on="rcept_no", how="left")

    print("\n[① 실패 재분류 · 범위 안] " + " · ".join(f"{k}: {v}" for k, v in D["유형"].value_counts().items()))
    ct = pd.crosstab(D["year"], D["유형"])
    ct2 = pd.crosstab(D["format"], D["유형"])
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print("\n[연도×유형]\n" + ct.to_string())
        print("\n[형식×유형]\n" + ct2.to_string())

    n_in, n_ok = len(I), int(I["ok"].sum())
    n_paren = int((D["유형"] == "괄호 없는 증감율(재검산 일치)").sum())
    n_noyoy = int((D["유형"] == "전년동기 값 없음").sum())
    est = (n_ok + n_paren) / (n_in - n_noyoy)
    print(f"\n[참고·추정, 판정 아님] 괄호 없는 증감율을 성공으로·전년동기 값 없음을 범위 밖으로 볼 때: 성공 {n_ok + n_paren:,}/{n_in - n_noyoy:,} = {100*est:.2f}%")
    yy = []
    for y, g in I.groupby("year"):
        d = D[D["year"] == y]
        a = int(g["ok"].sum()) + int((d["유형"] == "괄호 없는 증감율(재검산 일치)").sum())
        bse = len(g) - int((d["유형"] == "전년동기 값 없음").sum())
        yy.append(f"{y}:{100*a/bse:.2f}%")
    print("  연도별(추정) " + " ".join(yy))

    for t, g in D.groupby("유형"):
        print(f"\n===== 유형 [{t}] {len(g)}건 — 예시 최대 3건 =====")
        for x in g.sample(min(3, len(g)), random_state=20261020).to_dict("records"):
            print(f"### {x['rcept_no']} {x['corp_name']} {x['rcept_dt']} [{x['report_nm']}] {x['format']}")
            if isinstance(x.get("detail"), str):
                print("  검산: " + x["detail"])
            print(rows_text(dumps.get(x["rcept_no"])))

    print("\n[② 표 못 찾음 원문 바이트]")
    for x in D[D["유형"] == "표 못 찾음"].to_dict("records"):
        bt = raw[x["rcept_no"]]
        decl = re.search(rb"encoding\s*=\s*[\"']([^\"']+)", bt[:400])
        res = []
        for enc in ("utf-8", "cp949", "euc-kr"):
            try:
                bt.decode(enc); res.append(f"{enc} OK")
            except UnicodeDecodeError as e:
                res.append(f"{enc} 실패@{e.start}")
        print(f"- {x['rcept_no']} {x['corp_name']} {x['rcept_dt']}: {len(bt):,}B · 선언 {decl.group(1).decode() if decl else '없음'} · "
              f"U+FFFD {bt.count(bytes([0xEF, 0xBF, 0xBD]))}개 · {' / '.join(res)}")
    ex = D[D["유형"] == "표 못 찾음"]
    if len(ex):
        bt = raw[ex.iloc[0]["rcept_no"]]
        print(f"  첫 건 앞 120바이트: {bt[:120]!r}")
        i = bt.find(bytes([0xEF, 0xBF, 0xBD]))
        if i >= 0:
            print(f"  첫 U+FFFD 주변 바이트: {bt[max(0, i-30):i+30]!r}")

    for name, G in (("③ 성공 중 영업이익 당기=전년동기 동일값", S_same), ("③ 검산 불가 성공", S_nochk)):
        print(f"\n[{name}] {len(G)}건")
        for x in G.to_dict("records"):
            r = P1.parse_earn(P1.decode(raw[x["rcept_no"]])[0])
            print(f"### {x['rcept_no']} {x['corp_name']} {x['rcept_dt']} [{x['report_nm']}] 영업이익 {x['영업이익_cur']} / {x['영업이익_yoy']}")
            print(rows_text(r.get("table_rows"), 10))

    D.to_csv(f"{OUT}/diag_fail_class.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUT}/diag_fail_class.csv · 소요 {time.time()-t0:.0f}s")
    return D


if os.environ.get("EARN_DIAG_NO_MAIN") != "1":
    try:
        from google.colab import drive
        drive.mount("/content/drive")
    except Exception:
        pass
    D_diag = run()
