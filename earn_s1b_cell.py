# [새 정보원 1단계-b 셀] 1단계에서 Drive에 저장한 원문 30건(raw/*.bin)만 다시 파싱. API 호출 없음. 읽기 전용. 수익률 등 결과 데이터 미사용.
# 1단계 미달 원인(사실): ① 2026 형식은 '흑자적자전환여부' 열이 추가돼 머리글이 colspan으로 합쳐짐 -> 열 어긋남(2026 4건 전부 오값, 그중 2건은 성공으로 잘못 집계)
#   ② '증감액(증감율)' 형식은 증감율 칸이 "2,090 (15.8%)"라 검산이 증감액을 읽음.
# 수정(같은 30건으로만 확인, 사전 고정):
#   a) 표 칸을 colspan만큼 펼친 뒤 오른쪽 기준 열 매핑   b) 증감율 칸에 괄호 속 %가 있으면 그 값 사용
#   c) 성공 기준 강화: 영업이익 당기·전년동기 숫자 ∧ (검산 가능한 매출액·영업이익 검산이 모두 일치, 검산 불가면 숫자만)
#   d) 대상 범위: 공시명에 '자회사'·'월별'이 있으면 범위 밖(회사 자신의 분기 실적이 아님)으로 분리 집계
# 판정: 범위 안 표본에서 성공 ≥95% ∧ 모든 연도 ≥1 ∧ 검산 가능 건 중 일치 ≥95% -> 1단계 통과. 실패 건은 펼친 표 원문 출력.
import os, io, re, glob, zipfile
import numpy as np
import pandas as pd

S1_DIR = os.environ.get("EARN_S1", "/content/drive/MyDrive/new_earn_s1")
ITEMS = ("매출액", "영업이익", "당기순이익")


def decode(b):
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return b.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace"), "utf-8?"


def extract_main_xml(content, rcept_no):
    if content[:2] != b"PK":
        return None, decode(content[:300])[0]
    z = zipfile.ZipFile(io.BytesIO(content))
    names = z.namelist()
    main = f"{rcept_no}.xml" if f"{rcept_no}.xml" in names else max(names, key=lambda n: z.getinfo(n).file_size)
    return z.read(main), f"zip 파일 {len(names)}개, 사용 {main}"


def clean(s):
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def nz(s):
    return re.sub(r"\s+", "", s)


def span(c):
    try:
        return max(1, min(50, int(str(c.get("colspan", "1")).strip() or 1)))
    except ValueError:
        return 1


def tables(text):
    """표별 행 목록. 각 칸은 colspan만큼 반복(펼침)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for t in soup.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            cells = []
            for c in tr.find_all(["td", "th", "te", "tu"], recursive=False):
                cells += [clean(c.get_text(" "))] * span(c)
            if cells:
                rows.append(cells)
        if rows:
            out.append(rows)
    return out, clean(soup.get_text(" "))


def num(s):
    s = (s or "").strip().replace(",", "").replace(" ", "")
    if not re.search(r"\d", s):
        return None
    neg = s.startswith(("△", "-", "▲-", "("))
    v = float(re.search(r"\d+(\.\d+)?", s).group(0))
    return -v if neg else v


def pct(s):
    """증감율 칸: 괄호 속 %가 있으면 그 값(예 '2,090 (15.8%)' -> 15.8), 아니면 숫자."""
    s = s or ""
    m = re.search(r"\(\s*([△\-]?\s*[\d,]+(?:\.\d+)?)\s*%\s*\)", s)
    return num(m.group(1)) if m else num(s)


def find_header(rows):
    for i, r in enumerate(rows):
        z = [nz(c) for c in r]
        if any("전년동기" in c and "증감" not in c and "대비" not in c for c in z) and any(("당기" in c or "당해" in c) for c in z):
            return i
    return None


def check(cur, yoy, p):
    if cur is None or yoy is None or p is None or yoy <= 0:
        return None
    return abs((cur - yoy) / yoy * 100 - p) <= 1.0


def parse_earn(text):
    tbs, full = tables(text)
    res = {"unit": None, "period": None, "table_rows": None}
    u = re.search(r"단위\s*[:：]?\s*(천\s*원|백만\s*원|십억\s*원|억\s*원|원)", full)
    res["unit"] = nz(u.group(1)) if u else None
    for rows in tbs:
        hi = find_header(rows)
        if hi is None or not any("영업이익" in nz(c) for r in rows for c in r):
            continue
        hdr = [nz(c) for c in rows[hi]]
        L = len(hdr)
        i_cur = next(i for i, c in enumerate(hdr) if ("당기" in c or "당해" in c) and "증감" not in c and "대비" not in c)
        i_yoy = next(i for i, c in enumerate(hdr) if "전년동기" in c and "증감" not in c and "대비" not in c)
        i_pct = next((i for i, c in enumerate(hdr) if "전년동기" in c and ("증감" in c or "대비" in c)), None)
        k_cur, k_yoy = L - i_cur, L - i_yoy
        k_pct = L - i_pct if i_pct is not None else None
        res["table_rows"] = rows
        if hi + 1 < len(rows):
            m = re.search(r"\(?\s*'?\d{2,4}\s*[.년]?\s*[1-4]\s*(?:Q|분기)\s*\)?", " ".join(rows[hi + 1]))
            res["period"] = m.group(0) if m else None
        for item in ITEMS:
            r_i = None
            for j in range(hi + 1, len(rows)):
                first = nz(rows[j][0])
                if first.startswith(item) and not (item == "당기순이익" and "지배" in first):
                    r_i = j
                    break
            if r_i is None:
                continue
            row = rows[r_i]
            if any("누계" in nz(c) for c in row) and not any("당해" in nz(c) for c in row):
                for j in range(r_i + 1, min(r_i + 3, len(rows))):
                    if any("당해" in nz(c) for c in rows[j]):
                        row = rows[j]
                        break
            if len(row) < max(k_cur, k_yoy):
                continue
            cur, yoy = num(row[-k_cur]), num(row[-k_yoy])
            res[f"{item}_cur"], res[f"{item}_yoy"] = cur, yoy
            if item in ("매출액", "영업이익") and k_pct is not None and len(row) >= k_pct:
                res[f"{item}_check"] = check(cur, yoy, pct(row[-k_pct]))
        if res.get("영업이익_cur") is not None:
            break
    vals = res.get("영업이익_cur") is not None and res.get("영업이익_yoy") is not None
    chks = [res.get(k) for k in ("매출액_check", "영업이익_check") if res.get(k) is not None]
    res["ok"] = bool(vals and all(chks))
    res["checked"] = len(chks) > 0
    return res


def run(s1_dir=S1_DIR, verbose=True):
    P = print if verbose else (lambda *a, **k: None)
    meta = pd.read_csv(f"{s1_dir}/parse_results.csv", dtype={"rcept_no": str, "rcept_dt": str, "stock_code": str})
    recs, dumps = [], {}
    for _, x in meta.iterrows():
        rn = x["rcept_no"]
        rec = {"year": int(x["year"]), "rcept_dt": x["rcept_dt"], "corp_name": x["corp_name"], "report_nm": x["report_nm"],
               "범위": not re.search(r"자회사|월별", str(x["report_nm"])), "rcept_no": rn}
        content = open(f"{s1_dir}/raw/{rn}.bin", "rb").read()
        xml, info = extract_main_xml(content, rn)
        if xml is None:
            rec.update(ok=False, checked=False, err=f"ZIP 아님: {info[:100]}")
        else:
            try:
                r = parse_earn(decode(xml)[0])
                dumps[rn] = r.pop("table_rows")
                rec.update(r)
            except Exception as ex:
                rec.update(ok=False, checked=False, err=f"{type(ex).__name__}: {ex}")
        recs.append(rec)
    R = pd.DataFrame(recs)
    for c in ["unit", "period", "매출액_cur", "매출액_yoy", "영업이익_cur", "영업이익_yoy", "당기순이익_cur", "당기순이익_yoy",
              "매출액_check", "영업이익_check", "err"]:
        if c not in R:
            R[c] = None
    R.to_csv(f"{s1_dir}/parse_results_b.csv", index=False)
    cols = ["year", "corp_name", "범위", "unit", "period", "매출액_cur", "매출액_yoy", "영업이익_cur", "영업이익_yoy",
            "당기순이익_cur", "당기순이익_yoy", "매출액_check", "영업이익_check", "ok"]
    with pd.option_context("display.width", 260, "display.max_columns", 30, "display.max_colwidth", 16):
        P(R[cols].to_string(index=False))
    out = R[~R["범위"]]
    P(f"\n범위 밖(자회사·월별) {len(out)}건: " + (", ".join(f"{a}({b})" for a, b in zip(out['corp_name'], out['report_nm'])) or "없음"))
    I = R[R["범위"]]
    ok = I["ok"].fillna(False).astype(bool)
    ch = pd.concat([I["매출액_check"], I["영업이익_check"]]).dropna().astype(bool)
    by_year = I.groupby("year")["ok"].apply(lambda v: int(v.fillna(False).astype(bool).sum()))
    need = int(np.ceil(0.95 * len(I)))
    P("\n[범위 안] 연도별 성공: " + " ".join(f"{y}:{n}/{int((I['year'] == y).sum())}" for y, n in by_year.items()))
    P(f"[범위 안] 성공 {int(ok.sum())}/{len(I)} · 검산 {len(ch)}개 중 일치 {int(ch.sum())} · 검산 가능 공시 {int(I['checked'].fillna(False).sum())}/{len(I)}")
    passed = ok.sum() >= need and (by_year > 0).all() and (len(ch) == 0 or ch.mean() >= 0.95)
    P(f"\n1단계 판정(b): {'통과' if passed else '미달'} (기준: 성공 ≥{need}/{len(I)} · 모든 연도 ≥1 · 검산 일치 ≥95%)")
    bad = R[~R["ok"].fillna(False).astype(bool)]
    for _, x in bad.iterrows():
        P(f"\n### 실패 {x['rcept_no']} {x['corp_name']} {x['rcept_dt']} [{x['report_nm']}] {x['err'] if isinstance(x['err'], str) else ''}")
        for row in (dumps.get(x["rcept_no"]) or [])[:14]:
            P("  " + " | ".join(row))
        if not dumps.get(x["rcept_no"]):
            raw = open(f"{s1_dir}/raw/{x['rcept_no']}.bin", "rb").read()
            xml, _ = extract_main_xml(raw, x["rcept_no"])
            if xml is not None:
                tb, full = tables(decode(xml)[0])
                P(f"  (영업이익 표 못 찾음) 표 {len(tb)}개 · 본문 앞부분: {full[:400]}")
                for rows in tb[:2]:
                    for row in rows[:8]:
                        P("    " + " | ".join(row))
    P(f"\n저장: {s1_dir}/parse_results_b.csv")
    return R


if os.environ.get("EARN_NO_MAIN") != "1":
    try:
        from google.colab import drive
        drive.mount("/content/drive")
    except Exception:
        pass
    run()
