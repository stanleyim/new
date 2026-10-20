# [새 정보원 1단계 셀] DART 잠정실적 공시 본문 숫자의 PIT 수집 가능성 확인. 읽기 전용, 레포·운용 무변경. 성과·수익률 계산 없음.
# 선행: earn_s1_clone_cell.py 실행, Colab 보안 비밀 DART_API_KEY.
# ===== 사전 고정 =====
# 대상 공시: dart_data의 report_nm(공백 제거)에 '잠정'과 '실적'이 모두 있고 '['로 시작하지 않는 행(정정·첨부 등 접두 공시 제외).
# 표본: 2018~2026 연도별 3건(seed 20261020) + 남은 행에서 3건 = 30건. 같은 표본으로만 판정(표본 교체·추가 금지).
# 다운로드: OpenDART document.xml(rcept_no), 호출 간 0.5초, 30회(일 한도 20,000 대비 무시 가능). 원문은 Drive에 그대로 저장.
# 파싱(판정 대상 숫자): 매출액·영업이익·당기순이익의 당기(당해) 실적과 전년동기 실적, 단위, 기간 문자열, 연결/별도(공시명 기준).
#   열 매핑은 '전년동기'가 들어간 머리글 행을 오른쪽 기준으로 맞춤(rowspan으로 앞 칸이 빠지는 행 대응).
#   검산: 표에 적힌 '전년동기대비증감율'과 (당기-전년동기)/|전년동기| 계산값이 1%p 이내인지(전년동기>0이고 둘 다 숫자인 경우).
# 판정: 영업이익 당기·전년동기 숫자 추출 성공 ≥95%(30건 중 29건) ∧ 모든 연도 1건 이상 성공 ∧ 검산 가능 건 중 일치 ≥95% -> "1단계 통과"(2단계 계획안으로).
#   미달 -> 원문 덤프로 원인 보고(파서 수정은 같은 30건으로만 재확인, 수익률 등 결과 데이터는 보지 않음).
import os, io, re, glob, json, time, zipfile
import numpy as np
import pandas as pd

DART_DIR = os.environ.get("EARN_DART", "/content/dart/dart_data")
MAIN_DIR = os.environ.get("EARN_MAIN", "/content/main")
PIT_DIR = os.environ.get("EARN_PIT", "/content/pit/pit_data_extra")
URL = "https://opendart.fss.or.kr/api/document.xml"
SEED = 20261020
N_PER_YEAR, N_EXTRA = 3, 3
SLEEP = 0.5
ITEMS = ("매출액", "영업이익", "당기순이익")


# ---------- 목록 ----------
def load_dart(d=DART_DIR):
    fr = []
    for f in sorted(glob.glob(f"{d}/*.csv")):
        x = pd.read_csv(f, dtype=str, low_memory=False, keep_default_na=False)
        fr.append(x)
    df = pd.concat(fr, ignore_index=True)
    sc = df["stock_code"].str.replace(r"\.0$", "", regex=True)
    df["stock_code"] = np.where(sc.str.fullmatch(r"\d+"), sc.str.zfill(6), sc)
    return df


def earn_rows(df):
    nm = df["report_nm"].str.replace(r"\s+", "", regex=True)
    m = nm.str.contains("잠정") & nm.str.contains("실적") & ~df["report_nm"].str.lstrip().str.startswith("[")
    out = df[m].copy()
    out["nm_clean"] = nm[m]
    out["year"] = out["rcept_dt"].str[:4].astype(int)
    out["consolidated"] = out["nm_clean"].str.contains("연결")
    return out


def pick_sample(e, seed=SEED):
    rng = np.random.default_rng(seed)
    idx = []
    for y in sorted(e["year"].unique()):
        pool = e.index[e["year"] == y].to_numpy()
        idx += list(rng.choice(pool, size=min(N_PER_YEAR, len(pool)), replace=False))
    rest = np.setdiff1d(e.index.to_numpy(), np.array(idx))
    idx += list(rng.choice(rest, size=min(N_EXTRA, len(rest)), replace=False))
    return e.loc[idx].reset_index(drop=True)


# ---------- 본문 ----------
def decode(b):
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return b.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace"), "utf-8?"


def extract_main_xml(content, rcept_no):
    """ZIP이면 {rcept_no}.xml(없으면 가장 큰 파일) 반환. ZIP이 아니면 오류 응답 문자열과 함께 None."""
    if content[:2] != b"PK":
        return None, decode(content[:300])[0]
    z = zipfile.ZipFile(io.BytesIO(content))
    names = z.namelist()
    main = f"{rcept_no}.xml" if f"{rcept_no}.xml" in names else max(names, key=lambda n: z.getinfo(n).file_size)
    return z.read(main), f"zip 파일 {len(names)}개, 사용 {main}"


def clean(s):
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def tables(text):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(text, "html.parser")
    out = []
    for t in soup.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            cells = [clean(c.get_text(" ")) for c in tr.find_all(["td", "th", "te", "tu"], recursive=False)]
            if cells:
                rows.append(cells)
        if rows:
            out.append(rows)
    return out, clean(soup.get_text(" "))


def num(s):
    s = (s or "").strip().replace(",", "").replace(" ", "")
    if not re.search(r"\d", s):
        return None
    neg = s.startswith(("△", "-", "▲-", "(")) or (s.startswith("(") and s.endswith(")"))
    m = re.search(r"\d+(\.\d+)?", s)
    v = float(m.group(0))
    return -v if neg else v


def nz(s):
    return re.sub(r"\s+", "", s)


def find_header(rows):
    for i, r in enumerate(rows):
        z = [nz(c) for c in r]
        if any("전년동기" in c and "증감" not in c and "대비" not in c for c in z) and any(("당기" in c or "당해" in c) for c in z):
            return i
    return None


def parse_earn(text):
    tbs, full = tables(text)
    res = {"unit": None, "period": None, "ok": False, "check": None, "table_rows": None}
    u = re.search(r"단위\s*[:：]?\s*(천\s*원|백만\s*원|십억\s*원|억\s*원|원)", full)
    res["unit"] = nz(u.group(1)) if u else None
    for rows in tbs:
        hi = find_header(rows)
        if hi is None or not any("영업이익" in nz(c) for r in rows for c in r):
            continue
        hdr = [nz(c) for c in rows[hi]]
        L = len(hdr)
        i_cur = next(i for i, c in enumerate(hdr) if ("당기" in c or "당해" in c) and "증감" not in c)
        i_yoy = next(i for i, c in enumerate(hdr) if "전년동기" in c and "증감" not in c and "대비" not in c)
        i_pct = next((i for i, c in enumerate(hdr) if "전년동기" in c and "증감" in c), None)
        k_cur, k_yoy = L - i_cur, L - i_yoy
        k_pct = L - i_pct if i_pct is not None else None
        per = rows[hi][i_cur]
        if hi + 1 < len(rows) and not any(nz(rows[hi + 1][0]).startswith(it) for it in ITEMS) and len(rows[hi + 1]) >= k_cur:
            per += " " + rows[hi + 1][-k_cur]
        res["period"] = per
        res["table_rows"] = rows
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
            if item == "영업이익" and k_pct is not None and len(row) >= k_pct:
                pct = num(row[-k_pct])
                if cur is not None and yoy is not None and yoy > 0 and pct is not None:
                    res["check"] = abs((cur - yoy) / yoy * 100 - pct) <= 1.0
        if res.get("영업이익_cur") is not None:
            break
    res["ok"] = res.get("영업이익_cur") is not None and res.get("영업이익_yoy") is not None
    if res["period"] is None:
        m = re.search(r"(\d{2,4})\s*[.년]\s*([1-4])\s*(?:Q|분기)", full)
        res["period"] = m.group(0) if m else None
    return res


def fetch(rcept_no, key, get=None):
    if get is None:
        import requests
        get = requests.get
    for a in range(3):
        try:
            r = get(URL, params={"crtfc_key": key, "rcept_no": rcept_no}, timeout=30)
            return r.content
        except Exception as ex:
            if a == 2:
                raise
            time.sleep(2 * (a + 1))


# ---------- 실행 ----------
def main(key=None, out_dir=None, get=None, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    df = load_dart()
    e = earn_rows(df)
    u439 = set(pd.read_parquet(f"{MAIN_DIR}/data/ohlcv_full.parquet", columns=["ticker"])["ticker"].astype(str).str.zfill(6))
    roster = set()
    for f in sorted(glob.glob(f"{PIT_DIR}/sector_*.parquet")):
        roster |= set(pd.read_parquet(f, columns=["ticker"])["ticker"].astype(str))
    P(f"DART 전체 {len(df):,}행 · 잠정실적 대상 {len(e):,}행 · 종목 {e['stock_code'].nunique():,} · 439 포함 {len(set(e['stock_code']) & u439)}/{len(u439)} · "
      f"3,355 로스터 포함 {len(set(e['stock_code']) & roster):,}/{len(roster):,}")
    P("연도별 건수: " + " ".join(f"{y}:{n}" for y, n in e.groupby('year').size().items()))
    P("연도별 종목수: " + " ".join(f"{y}:{n}" for y, n in e.groupby('year')['stock_code'].nunique().items()))
    P(f"연결 공시명 비중 {e['consolidated'].mean():.3f} · rcept_no 앞8자리≠rcept_dt {int((e['rcept_no'].str[:8] != e['rcept_dt']).sum())}건")
    P("공시명 변형(상위 12):")
    for n_, c in e["nm_clean"].value_counts().head(12).items():
        P(f"  {c:>6}  {n_}")
    g = e.groupby(["stock_code", "nm_clean", "rcept_dt"]).size()
    P(f"(종목·공시명·접수일) 같은 키 복수: {int((g > 1).sum())}건")

    s = pick_sample(e)
    if key is None:
        from google.colab import userdata
        key = userdata.get("DART_API_KEY")
    if out_dir is None:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_earn_s1"
        except Exception as ex:
            out_dir = "/content/new_earn_s1"
            P(f"Drive 마운트 실패({ex}) -> 로컬 {out_dir}")
    os.makedirs(f"{out_dir}/raw", exist_ok=True)
    recs, dumps = [], []
    for i, x in s.iterrows():
        content = fetch(x["rcept_no"], key, get)
        open(f"{out_dir}/raw/{x['rcept_no']}.bin", "wb").write(content)
        rec = {"rcept_no": x["rcept_no"], "rcept_dt": x["rcept_dt"], "year": int(x["year"]), "stock_code": x["stock_code"],
               "corp_name": x["corp_name"], "corp_cls": x["corp_cls"], "report_nm": x["nm_clean"], "연결": bool(x["consolidated"])}
        try:
            xml, info = extract_main_xml(content, x["rcept_no"])
            if xml is None:
                rec.update(ok=False, err=f"ZIP 아님: {info[:120]}")
            else:
                text, enc = decode(xml)
                r = parse_earn(text)
                rows = r.pop("table_rows")
                rec.update({k: v for k, v in r.items()}, enc=enc, info=info)
                dumps.append((x["rcept_no"], x["corp_name"], x["rcept_dt"], rows))
        except Exception as ex:
            rec.update(ok=False, err=f"{type(ex).__name__}: {ex}")
        recs.append(rec)
        time.sleep(SLEEP)
    R = pd.DataFrame(recs)
    R.to_csv(f"{out_dir}/parse_results.csv", index=False)
    with open(f"{out_dir}/tables_dump.txt", "w", encoding="utf-8") as f:
        for rn, cn, dt, rows in dumps:
            f.write(f"### {rn} {cn} {dt}\n")
            for row in rows or []:
                f.write(" | ".join(row) + "\n")
            f.write("\n")

    P(f"\n===== 표본 {len(R)}건 파싱 결과 =====")
    cols = ["year", "rcept_dt", "corp_name", "연결", "unit", "period", "매출액_cur", "매출액_yoy", "영업이익_cur", "영업이익_yoy",
            "당기순이익_cur", "당기순이익_yoy", "check", "ok"]
    for c in cols:
        if c not in R:
            R[c] = None
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 22):
        P(R[cols].to_string(index=False))
    if "err" in R and R["err"].notna().any():
        P("\n오류:")
        for _, x in R[R["err"].notna()].iterrows():
            P(f"  {x['rcept_no']} {x['corp_name']}: {x['err']}")
    ok = R["ok"].fillna(False).astype(bool)
    by_year = R.groupby("year")["ok"].apply(lambda v: int(v.fillna(False).astype(bool).sum()))
    chk = R["check"].dropna().astype(bool)
    P("\n연도별 성공: " + " ".join(f"{y}:{n}/{int((R['year'] == y).sum())}" for y, n in by_year.items()))
    P(f"성공 {int(ok.sum())}/{len(R)} · 검산 가능 {len(chk)}건 중 일치 {int(chk.sum())} · 단위 분포 {R['unit'].value_counts(dropna=False).to_dict()}")
    passed = ok.sum() >= int(np.ceil(0.95 * len(R))) and (by_year > 0).all() and (len(chk) == 0 or chk.mean() >= 0.95)
    P(f"\n1단계 판정: {'통과' if passed else '미달'} (기준: 성공 ≥{int(np.ceil(0.95*len(R)))}/{len(R)} · 모든 연도 ≥1 · 검산 일치 ≥95%)")
    P("\n===== 원문 표 예시 3건 (2018·2022·2026 순, 없으면 다른 연도) =====")
    shown = 0
    for y in (2018, 2022, 2026, 2020, 2024):
        for rn, cn, dt, rows in dumps:
            if dt[:4] == str(y) and shown < 3:
                P(f"\n### {rn} {cn} {dt}")
                for row in (rows or [])[:16]:
                    P("  " + " | ".join(row))
                shown += 1
                break
    P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir} (raw/*.bin 원문, parse_results.csv, tables_dump.txt)")
    return R


if os.environ.get("EARN_NO_MAIN") != "1":
    main()
