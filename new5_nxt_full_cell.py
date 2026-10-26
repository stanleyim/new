# [Colab 셀] New5 NXT 1년 실측 — 밤사이 수익 구간 분해 + 매도·매수 시점 비교. 읽기 전용(레포·운용 무변경).
# 사전 고정(10-26, 결과 보기 전):
#  대상: 레포 overnight_paper.py의 함수(pivots·top_list·kind_of) 그대로 만든 매수 명단, krx_daily(~10-23) 기준 전 기간.
#        제외도 코드와 같음: 매수일 상한가(+29.5%)·종가 없음·매도일 시가 없음·|밤사이| ≥ 30%.
#  수집(KIS FHKST03010230, 건마다 3회): ① NX 매수일 20:00 이전 마지막 봉 ② J 매수일 20:00 이전 마지막 봉
#        ③ NX 매도일 09:00 이전 봉(프리마켓 08:00~08:59). 초당 5회 이하. Drive에 저장하며 이어받기.
#  비교(비용 왕복 0.206%, 같은 종목 집합 = NX 세 가격이 모두 있는 건):
#        현재 = 종가 매수 → KRX 시가 매도 / A = 종가 → NX 프리 첫 가격(08:00) 매도 / B = 종가 → NX 프리 끝 가격(~08:50) 매도
#        C = NX 20:00 직전 가격 매수 → KRX 시가 매도 / Cj = KRX(J) 20:00 직전 가격 매수 → KRX 시가 매도
#  판정: 정식일(다음 거래일이 월요일이 아닌 날)만. 앞 절반·뒤 절반(정식일 수 기준) 모두 하루 순수익이 '현재'보다 높아야 채택.
#  자동 확인: 10-26 실측 셀(new5_nxt_probe_cell)과 같은 8개 가격이 다르면 중단.
import os, sys, json, time, getpass, subprocess
import numpy as np, pandas as pd, requests

COST = 0.00206
MOCK = os.environ.get("NXT_MOCK") == "1"          # 제 쪽 가짜 응답 시험용. Colab에서는 설정하지 마세요.
# ---------- 1. 레포 코드·자료 ----------
if not os.path.isdir("/content/main/overnight_paper"):
    subprocess.run("rm -rf /content/main; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git /content/main",
                   shell=True, check=True)
sys.path.insert(0, "/content/main")
import overnight_paper as OP
k = pd.read_parquet("/content/main/overnight_paper/krx_daily.parquet")
k["ticker"] = k["ticker"].astype(str).str.zfill(6)
k["date"] = k["date"].astype(str).str.replace("-", "").str[:8]
cal, op, cl, tv20 = OP.pivots(k)
items = []
for i, d in enumerate(cal[:-1]):
    if i == 0 or tv20.loc[cal[i - 1]].isna().all():
        continue
    kd = OP.kind_of(d, cal)
    if kd == "매수없음":
        continue
    prev, n = cal[i - 1], cal[i + 1]
    for t, rk in OP.top_list(tv20, prev):
        c, c0, o = cl.at[d, t], cl.at[prev, t], op.at[n, t]
        if not (c > 0):
            st = "종가없음"
        elif c0 > 0 and c / c0 - 1 >= OP.LIMIT_UP:
            st = "상한가제외"
        elif not (o > 0):
            st = "시가없음"
        elif abs(o / c - 1) >= OP.CA_ABS:
            st = "권리변경제외"
        else:
            st = "완료"
        items.append(dict(d=d, n=n, t=t, rank=rk, kind=kd, C=c, O=o, st=st))
it = pd.DataFrame(items)
print(f"KRX 일봉 {cal[0]}~{cal[-1]} · 매수일 {it.d.nunique()} ({it.d.min()}~{it.d.max()}) · 건 {len(it)} · 완료 {int((it.st == '완료').sum())} · 종목 {it.t.nunique()}")
it = it[it.st == "완료"].reset_index(drop=True)

# ---------- 2. 저장 위치 ----------
OUTD = "/content/new5_nxt_out"
if not MOCK:
    try:
        from google.colab import drive
        if not os.path.isdir("/content/drive/MyDrive"):
            drive.mount("/content/drive")
        OUTD = "/content/drive/MyDrive/new5_nxt_out"
    except Exception as e:
        print("Drive 연결 실패 → 런타임에만 저장:", type(e).__name__)
os.makedirs(OUTD, exist_ok=True)
CACHE = f"{OUTD}/kis_cache.json"
cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
print(f"저장 위치 {OUTD} · 이미 받은 호출 {len(cache)}")

# ---------- 3. KIS ----------
BASE = "https://openapi.koreainvestment.com:9443"
for key in ("KIS_APP_KEY", "KIS_APP_SECRET"):
    if not os.environ.get(key):
        try:
            from google.colab import userdata
            os.environ[key] = userdata.get(key)
        except Exception as e:
            print(f"{key} 보안 비밀 없음({type(e).__name__}) → 입력창")
            os.environ[key] = getpass.getpass(f"{key}: ").strip()
r = requests.post(f"{BASE}/oauth2/tokenP", timeout=30, json={"grant_type": "client_credentials",
                  "appkey": os.environ["KIS_APP_KEY"], "appsecret": os.environ["KIS_APP_SECRET"]})
if r.status_code != 200 or "access_token" not in r.json():
    raise SystemExit(f"KIS 토큰 실패 {r.status_code} {r.text[:200]}")
H = {"content-type": "application/json", "authorization": f"Bearer {r.json()['access_token']}",
     "appkey": os.environ["KIS_APP_KEY"], "appsecret": os.environ["KIS_APP_SECRET"], "tr_id": "FHKST03010230"}
S = requests.Session()
last_call = [0.0]
stat = {"calls": 0, "limit": 0, "err": 0}

def call(mkt, tk, ds, hour):
    """1회 조회(최대 120봉, ds 날짜만). 반환 [[시각, 시가, 종가], ...] 시각 오름차순. 업무 오류는 빈 목록."""
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
            resp = S.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", headers=H, timeout=30,
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
        rows = [[x["stck_cntg_hour"], float(x["stck_oprc"]), float(x["stck_prpr"])]
                for x in (d.get("output2") or [])
                if x.get("stck_bsop_date") == ds and x.get("stck_cntg_hour") and x["stck_cntg_hour"] <= hour]
        rows.sort()
        cache[key] = rows
        return rows
    save()
    raise SystemExit(f"KIS 반복 실패 {key} (진행분은 저장됨, 다시 실행하면 이어받음)")

def save():
    tmp = CACHE + ".tmp"
    json.dump(cache, open(tmp, "w"))
    os.replace(tmp, CACHE)

t0 = time.time()
rec = []
for j, x in enumerate(it.itertuples(index=False)):
    a_nx = call("NX", x.t, x.d, "200000")
    a_j = call("J", x.t, x.d, "200000")
    pre = [b for b in call("NX", x.t, x.n, "090000") if "080000" <= b[0] <= "085959"]
    rec.append(dict(A_nx=a_nx[-1][2] if a_nx else np.nan, A_nx_t=a_nx[-1][0] if a_nx else "",
                    A_j=a_j[-1][2] if a_j else np.nan, A_j_t=a_j[-1][0] if a_j else "",
                    P0=pre[0][1] if pre else np.nan, P0_t=pre[0][0] if pre else "",
                    P50=pre[-1][2] if pre else np.nan, P50_t=pre[-1][0] if pre else ""))
    if (j + 1) % 100 == 0 or j == len(it) - 1:
        save()
        el = time.time() - t0
        print(f"  {j + 1}/{len(it)} · 호출 {stat['calls']} · 한도초과 {stat['limit']} · 업무오류 {stat['err']} · {el / 60:.1f}분", flush=True)
df = pd.concat([it, pd.DataFrame(rec)], axis=1)
df.to_csv(f"{OUTD}/nxt_items.csv", index=False)

# ---------- 4. 자동 확인 (10-26 실측 셀과 같은 값) ----------
EXPECT = {("20261021", "005930"): dict(O=221500, P0=223500, P50=219000),
          ("20261021", "000660"): dict(O=1273000, P0=1262000, P50=1255000),
          ("20261022", "005930"): dict(O=206000, P0=207000, P50=206000, A_nx=214500, A_j=214500),
          ("20261022", "000660"): dict(O=1200000, P0=1204000, P50=1190000, A_nx=1234000, A_j=1234000)}
bad = []
for (d, t), ev in EXPECT.items():
    row = df[(df.d == d) & (df.t == t)]
    if not len(row):
        bad.append(f"{d} {t} 명단에 없음")
        continue
    for c, v in ev.items():
        if float(row.iloc[0][c]) != v:
            bad.append(f"{d} {t} {c}: 셀 {row.iloc[0][c]} · 실측 {v}")
print("자동 확인:", "통과 (실측 셀과 같은 값 18개)" if not bad else "불일치 → " + "; ".join(bad))
if bad and not MOCK:
    raise SystemExit("자동 확인 불일치로 중단 (수집분은 Drive에 저장됨)")

# ---------- 5. 분석 ----------
df["r_now"] = df.O / df.C - 1
df["r_A"] = df.P0 / df.C - 1
df["r_B"] = df.P50 / df.C - 1
df["r_C"] = df.O / df.A_nx - 1
df["r_Cj"] = df.O / df.A_j - 1
df["has_nx"] = df[["A_nx", "P0", "P50"]].gt(0).all(axis=1)
print(f"\n가격 확보: NX 매수일 20시 {int(df.A_nx.gt(0).sum())} · J 20시 {int(df.A_j.gt(0).sum())} · NX 프리 {int(df.P0.gt(0).sum())} · "
      f"NX 세 가격 모두 {int(df.has_nx.sum())} / {len(df)}건 (없는 건 = NXT 비거래 종목 등)")
aj = df[df.A_j.gt(0)].copy()
aj["p"] = np.where(aj.d >= "20260914", "9-14이후", "이전")
print("J 20시 직전 마지막 봉 시각(많은 순):", {g: s.value_counts().head(3).to_dict() for g, s in aj.groupby("p").A_j_t})
cm = df[df.has_nx & df.A_j.gt(0)].copy()
reg = cm[cm.kind == "정식"]
days = sorted(reg.d.unique())
h = len(days) // 2
M = {"현재(종가→KRX시가)": "r_now", "A(종가→NX 08:00)": "r_A", "B(종가→NX 프리끝)": "r_B",
     "C(NX 20시→KRX시가)": "r_C", "Cj(KRX 20시→KRX시가)": "r_Cj"}
day = reg.groupby("d")[list(M.values())].mean() - COST

def met(s):
    eq = (1 + s).cumprod()
    return f"하루 {s.mean() * 100:+.3f}% · Sharpe {s.mean() / s.std() * np.sqrt(252):.2f} · 누적 {(eq.iloc[-1] - 1) * 100:+.1f}% · MDD {(eq / eq.cummax() - 1).min() * 100:.1f}%"

print(f"\n[비교] 정식일 {len(days)}일 ({days[0]}~{days[-1]}), 같은 종목 집합 {len(reg)}건, 비용 {COST:.3%}")
print(f"  앞 절반 {days[0]}~{days[h - 1]} · 뒤 절반 {days[h]}~{days[-1]}")
for nm, c in M.items():
    s = day[c]
    print(f"  {nm:22} 전체 {met(s)}\n  {'':22} 앞 {s.iloc[:h].mean() * 100:+.3f}% · 뒤 {s.iloc[h:].mean() * 100:+.3f}%")
print("\n[판정] 앞·뒤 절반 모두 '현재'보다 높아야 채택")
for nm, c in list(M.items())[1:]:
    w1 = day[c].iloc[:h].mean() > day["r_now"].iloc[:h].mean()
    w2 = day[c].iloc[h:].mean() > day["r_now"].iloc[h:].mean()
    print(f"  {nm:22} 앞 {'우세' if w1 else '열세'} · 뒤 {'우세' if w2 else '열세'} → {'채택' if (w1 and w2) else '불채택'}")

lg = lambda a, b: np.log(reg[a] / reg[b])
leg = pd.DataFrame({"d": reg.d, "① 종가→NX 20시": lg("A_nx", "C"), "② NX 20시→다음날 08:00": lg("P0", "A_nx"),
                    "③ 프리 08:00→끝": lg("P50", "P0"), "④ 프리끝→KRX 시가": lg("O", "P50")}).groupby("d").mean()
print("\n[밤사이 수익 구간 분해] 정식일 하루 평균 (로그수익 %, 네 구간 합 = 종가→시가)")
for c in leg.columns:
    print(f"  {c:20} 전체 {leg[c].mean() * 100:+.3f}% · 앞 {leg[c].iloc[:h].mean() * 100:+.3f}% · 뒤 {leg[c].iloc[h:].mean() * 100:+.3f}%")
print(f"  {'합계':20} 전체 {leg.sum(axis=1).mean() * 100:+.3f}%")
sh = cm[cm.kind == "그림자"]
if len(sh):
    sd = sh.groupby("d")[["r_now", "r_B"]].mean() - COST
    print(f"\n(참고) 그림자일(주말 넘김) {len(sd)}일: 현재 하루 {sd.r_now.mean() * 100:+.3f}% · B {sd.r_B.mean() * 100:+.3f}%")
print(f"\n저장: {OUTD}/nxt_items.csv · 총 {(time.time() - t0) / 60:.1f}분. 출력 전체를 붙여 주세요.")
