# [Colab 셀] New5 A안 홀드아웃 검증 — 종가 매수 → NX 프리마켓 08:00 첫 가격 매도 vs 현재(KRX 시가 매도). 읽기 전용.
# 사전 고정(10-26, 결과 보기 전):
#  기간: 매수일 2025-03-31 ~ 2025-11-06 (지금까지 쓴 2025-11-07~ 이전, 한 번도 안 쓴 자료).
#  명단·제외: 레포 overnight_paper.py 함수 그대로(KRX 일봉은 같은 krx_day 함수로 2025-02-03부터 받음). 비용 0.206%. 정식일만.
#  어긋남 제외: KIS J 매수일 15:30 가격 / KRX 종가 ±0.5% 밖, 또는 NX 프리 가격 / KRX 다음날 시가 ±25% 밖, 또는 KIS 가격 없음.
#  A 채택 조건(셋 다): Sharpe(A) > Sharpe(현재) · MDD(A)가 현재보다 얕음 · A 하루 순수익 > 0.  B(프리 끝 매도)는 참고.
#  함께: NX 08:00 첫 1분 거래대금(실제 매도 가능 물량 판단용).
#  먼저 KIS가 그 시기 NX 분봉을 주는지 확인 → 안 주면 즉시 중단.
import os, sys, json, time, getpass, subprocess, datetime as dt
import numpy as np, pandas as pd, requests

COST, H0, H1 = 0.00206, "20250331", "20251106"
MOCK = os.environ.get("NXT_MOCK") == "1"          # 제 쪽 시험용. Colab에서는 설정하지 마세요.
OUTD = "/content/new5_nxt_out" if MOCK else "/content/drive/MyDrive/new5_nxt_out"
if not MOCK and not os.path.isdir("/content/drive/MyDrive"):
    from google.colab import drive
    drive.mount("/content/drive")
os.makedirs(OUTD, exist_ok=True)

def secret(k):
    if not os.environ.get(k):
        try:
            from google.colab import userdata
            os.environ[k] = userdata.get(k)
        except Exception as e:
            print(f"{k} 보안 비밀 없음({type(e).__name__}) → 입력창")
            os.environ[k] = getpass.getpass(f"{k}: ").strip()
for k in ("KRX_ID", "KRX_PW", "KIS_APP_KEY", "KIS_APP_SECRET"):
    secret(k)

# ---------- 1. KIS 준비 + 그 시기 NX 자료 유무 확인 ----------
BASE = "https://openapi.koreainvestment.com:9443"
r = requests.post(f"{BASE}/oauth2/tokenP", timeout=30, json={"grant_type": "client_credentials",
                  "appkey": os.environ["KIS_APP_KEY"], "appsecret": os.environ["KIS_APP_SECRET"]})
if r.status_code != 200 or "access_token" not in r.json():
    raise SystemExit(f"KIS 토큰 실패 {r.status_code} {r.text[:200]}")
HD = {"content-type": "application/json", "authorization": f"Bearer {r.json()['access_token']}",
      "appkey": os.environ["KIS_APP_KEY"], "appsecret": os.environ["KIS_APP_SECRET"], "tr_id": "FHKST03010230"}
S = requests.Session()
CACHE = f"{OUTD}/kis_cache_holdout.json"
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
last_call, stat = [0.0], {"calls": 0, "limit": 0, "err": 0}

def save():
    json.dump(cache, open(CACHE + ".tmp", "w"))
    os.replace(CACHE + ".tmp", CACHE)

def call(mkt, tk, ds, hour):
    """1회 조회(최대 120봉, ds 날짜만). 반환 [[시각, 시가, 종가, 거래량], ...] 오름차순."""
    key = f"{mkt}|{tk}|{ds}|{hour}"
    if key in cache:
        return cache[key]
    for attempt in range(6):
        w = 0.2 - (time.time() - last_call[0])
        if w > 0:
            time.sleep(w)
        last_call[0] = time.time()
        stat["calls"] += 1
        try:
            resp = S.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", headers=HD, timeout=30,
                         params={"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": tk, "FID_INPUT_HOUR_1": hour,
                                 "FID_INPUT_DATE_1": ds, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "N"})
            d = resp.json()
        except Exception:
            time.sleep(1 + attempt)
            continue
        if d.get("msg_cd") == "EGW00201":
            stat["limit"] += 1
            time.sleep(1 + 0.5 * attempt)
            continue
        if resp.status_code != 200:
            time.sleep(1 + attempt)
            continue
        if d.get("rt_cd") != "0":
            stat["err"] += 1
        rows = sorted([[x["stck_cntg_hour"], float(x["stck_oprc"]), float(x["stck_prpr"]), float(x.get("cntg_vol") or 0)]
                       for x in (d.get("output2") or [])
                       if x.get("stck_bsop_date") == ds and x.get("stck_cntg_hour") and x["stck_cntg_hour"] <= hour])
        cache[key] = rows
        return rows
    save()
    raise SystemExit(f"KIS 반복 실패 {key} (진행분 저장됨, 다시 실행하면 이어받음)")

print("KIS 토큰 OK · 그 시기 NX 자료 유무:")
ok_months = 0
for ds in ("20250401", "20250602", "20250901", "20251104"):
    nx = [b for b in call("NX", "005930", ds, "090000") if b[0] < "090000"]
    print(f"  {ds} 삼성전자 NX 프리마켓 {len(nx)}봉" + (f" ({nx[0][0]}~{nx[-1][0]})" if nx else ""))
    ok_months += bool(nx)
save()
if ok_months == 0:
    raise SystemExit("KIS가 이 시기 NX 분봉을 주지 않음 → 홀드아웃 불가. 결과를 붙여 주세요.")

# ---------- 2. KRX 일봉 (레포 krx_day 그대로) ----------
if not os.path.isdir("/content/main/overnight_paper"):
    subprocess.run("rm -rf /content/main; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git /content/main",
                   shell=True, check=True)
sys.path.insert(0, "/content/main")
import overnight_paper as OP
k_now = pd.read_parquet("/content/main/overnight_paper/krx_daily.parquet")
k_now["ticker"] = k_now["ticker"].astype(str).str.zfill(6)
k_now["date"] = k_now["date"].astype(str).str.replace("-", "").str[:8]
first = min(k_now["date"])
KP = f"{OUTD}/krx_daily_2025.parquet"
old = pd.read_parquet(KP) if os.path.exists(KP) else pd.DataFrame()
have = set(old["date"]) if len(old) else set()
done_empty = set(json.load(open(KP + ".empty.json"))) if os.path.exists(KP + ".empty.json") else set()
days = [d.strftime("%Y%m%d") for d in pd.bdate_range("2025-02-03", dt.datetime.strptime(first, "%Y%m%d") - dt.timedelta(days=1))]
todo = [d for d in days if d not in have and d not in done_empty]
print(f"\nKRX 일봉 2025-02-03~{first} 전날: 평일 {len(days)} · 받을 날 {len(todo)}")
fr = [old] if len(old) else []
for i, ds in enumerate(todo):
    x = OP.krx_day(ds)
    if x is None:
        done_empty.add(ds)
    else:
        x["ticker"] = x["ticker"].astype(str).str.zfill(6)
        fr.append(x)
    if (i + 1) % 20 == 0 or i == len(todo) - 1:
        pd.concat(fr, ignore_index=True).to_parquet(KP, index=False) if fr else None
        json.dump(sorted(done_empty), open(KP + ".empty.json", "w"))
        print(f"  {i + 1}/{len(todo)}", flush=True)
old = pd.read_parquet(KP)
k = pd.concat([old, k_now], ignore_index=True).drop_duplicates(["ticker", "date"], keep="last")
cal, op, cl, tv20 = OP.pivots(k)
gaps = [(a, b) for a, b in zip(cal, cal[1:]) if (dt.datetime.strptime(b, "%Y%m%d") - dt.datetime.strptime(a, "%Y%m%d")).days > 5]
print(f"KRX 거래일 {cal[0]}~{cal[-1]} {len(cal)}일 · 5일 넘는 빈틈 {gaps if gaps else '없음'}")

# ---------- 3. 홀드아웃 명단 ----------
items = []
for i, d in enumerate(cal[:-1]):
    if not (H0 <= d <= H1) or i == 0 or tv20.loc[cal[i - 1]].isna().all():
        continue
    kd = OP.kind_of(d, cal)
    if kd == "매수없음":
        continue
    prev, n = cal[i - 1], cal[i + 1]
    for t, rk in OP.top_list(tv20, prev):
        c, c0, o = cl.at[d, t], cl.at[prev, t], op.at[n, t]
        if not (c > 0) or (c0 > 0 and c / c0 - 1 >= OP.LIMIT_UP) or not (o > 0) or abs(o / c - 1) >= OP.CA_ABS:
            continue
        items.append(dict(d=d, n=n, t=t, kind=kd, C=c, O=o))
it = pd.DataFrame(items)
print(f"\n홀드아웃 매수일 {it.d.nunique()} ({it.d.min()}~{it.d.max()}) · 건 {len(it)} · 종목 {it.t.nunique()} · KIS 호출 예상 {2 * len(it)}")

# ---------- 4. KIS 수집 (건마다 2회) ----------
t0, rec = time.time(), []
for j, x in enumerate(it.itertuples(index=False)):
    aj = call("J", x.t, x.d, "200000")
    pre = [b for b in call("NX", x.t, x.n, "090000") if "080000" <= b[0] <= "085959"]
    rec.append(dict(Aj=aj[-1][2] if aj else np.nan, Aj_t=aj[-1][0] if aj else "",
                    P0=pre[0][1] if pre else np.nan, P0_t=pre[0][0] if pre else "", P0_val=pre[0][1] * pre[0][3] if pre else np.nan,
                    P50=pre[-1][2] if pre else np.nan))
    if (j + 1) % 100 == 0 or j == len(it) - 1:
        save()
        print(f"  {j + 1}/{len(it)} · 호출 {stat['calls']} · 한도초과 {stat['limit']} · 업무오류 {stat['err']} · {(time.time() - t0) / 60:.1f}분", flush=True)
df = pd.concat([it, pd.DataFrame(rec)], axis=1)
df.to_csv(f"{OUTD}/nxt_holdout_items.csv", index=False)

# ---------- 5. 어긋남 제외 + 판정 ----------
lim = np.log(1.25)
okj = (df.Aj_t == "153000") & ((df.Aj / df.C - 1).abs() <= 0.005)
okp = (np.log(df.P0 / df.O).abs() <= lim) & (np.log(df.P50 / df.O).abs() <= lim)
df["clean"] = okj & okp.fillna(False)
print(f"\n가격 확보·어긋남: 전체 {len(df)} · NX 프리 있음 {int(df.P0.gt(0).sum())} · J 15:30 일치 {int(okj.sum())} · 최종 사용 {int(df.clean.sum())}")
bad = df[df.P0.gt(0) & ~df.clean]
if len(bad):
    print("  제외 종목(건 많은 순):", bad.t.value_counts().head(10).to_dict())
reg = df[df.clean & (df.kind == "정식")].copy()
reg["r_now"], reg["r_A"], reg["r_B"] = reg.O / reg.C - 1, reg.P0 / reg.C - 1, reg.P50 / reg.C - 1
day = reg.groupby("d")[["r_now", "r_A", "r_B"]].mean() - COST
nd = len(day)
h = nd // 2

def met(s):
    eq = (1 + s).cumprod()
    return dict(m=s.mean() * 100, sh=s.mean() / s.std() * np.sqrt(252), cum=(eq.iloc[-1] - 1) * 100, mdd=(eq / eq.cummax() - 1).min() * 100)

R = {c: met(day[c]) for c in ("r_now", "r_A", "r_B")}
print(f"\n[홀드아웃] 정식일 {nd}일 ({day.index[0]}~{day.index[-1]}) · {len(reg)}건 · 비용 {COST:.3%}")
for nm, c in (("현재(종가→KRX시가)", "r_now"), ("A(종가→NX 08:00)", "r_A"), ("B(종가→NX 프리끝, 참고)", "r_B")):
    v = R[c]
    print(f"  {nm:22} 하루 {v['m']:+.3f}% · Sharpe {v['sh']:.2f} · 누적 {v['cum']:+.1f}% · MDD {v['mdd']:.1f}%"
          f" · 앞 {day[c].iloc[:h].mean() * 100:+.3f}% 뒤 {day[c].iloc[h:].mean() * 100:+.3f}%")
c1, c2, c3 = R["r_A"]["sh"] > R["r_now"]["sh"], R["r_A"]["mdd"] > R["r_now"]["mdd"], R["r_A"]["m"] > 0
print(f"\n[판정 A] Sharpe 높음 {'O' if c1 else 'X'} · MDD 얕음 {'O' if c2 else 'X'} · 순수익 플러스 {'O' if c3 else 'X'} → {'통과' if (c1 and c2 and c3) else '불통과'}")
v = reg.P0_val.dropna()
print(f"[매도 가능 물량] NX 08:00 첫 1분 거래대금: 중앙값 {v.median() / 1e6:,.0f}백만원 · 하위 10% {v.quantile(.1) / 1e6:,.0f}백만원 · 0원 {int((v == 0).sum())}건"
      f" · 첫 봉이 08:00 아닌 건 {int((reg.P0_t != '080000').sum())}")
print(f"\n저장: {OUTD}/nxt_holdout_items.csv. 출력 전체를 붙여 주세요.")
