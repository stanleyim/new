"""
verify_hold_recover.py — 시가가 매수가보다 낮으면 안 팔고, 매수가로 돌아오면 파는 규칙 검증
(한 번 실행, 읽기 전용, 레포에 쓰지 않음, 주문 없음)

규칙(주문 하나로 구현됨): 매수일 d 15:30 종가 C 매수 → 다음 거래일 n 시가 동시호가에 '지정가 매도 C'.
  시가 ≥ C → 시가에 체결 (= 기존 규칙과 같음)
  시가 <  C → 체결 안 됨, 장중 계속 걸어 둠 → 고가가 C에 닿으면 C에 체결(본전, 비용만 손해)
  V1     : n일 안에 못 닿으면 n일 15:30 종가에 매도 (돈이 겹치지 않음 → 복리 누적 가능)
  V2-5/20: 최대 5/20거래일 걸어 둠, 못 닿으면 그날 종가 매도 (돈이 겹침 → 평균·동시 보유 수 보고)
  V2-끝  : 자료 끝까지 걸어 둠 (못 닿은 건 마지막 종가로 평가, 개수 출력)
  체결 조건(주) 고가 > C, (참고) 고가 ≥ C. 비용 왕복 0.006%. 다음 거래일 월요일 밤은 쉼. |밤|≥30% 제외.
판정(실행 전 고정): 2025-01-02 이후 매수, KODEX 200·ACE 200 둘 다 V1 누적 > 기준(시가 매도) 누적 → "V1 이득".
  V2는 참고(밤 평균 비교). 2016~2024는 참고.
대조(자료 점검): 기준 누적 2025-01-02~2026-11-05 매수 361밤 KODEX +160.6% · ACE +195.0% (New5_now §5) 와 0.1%p 이내.
시험용: VR_FAKE=1 → 가짜 자료. 실행: GitHub Actions verify_hold_recover.yml (수동).
"""
import os
import sys
import time
import math
import random
import datetime as dt

ETFS = {"069500": "KODEX 200", "105190": "ACE 200"}
COST, CA_ABS, FROM_YEAR = 0.00006, 0.30, 2015
MAIN0 = "20250102"
CHECK = {"069500": 160.6, "105190": 195.0}
CHECK_END = "20261105"
VARS = [("V1", 1), ("V2-5", 5), ("V2-20", 20), ("V2-끝", 10**6)]


def log(*a):
    print(*a, flush=True)


def wd(ds):
    return dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:])).weekday()


def load(t):
    """[(날짜, 시가, 고가, 종가)] 오름차순"""
    if os.environ.get("VR_FAKE"):
        rnd = random.Random(int(t))
        out, p, d = [], 10000.0, dt.date(FROM_YEAR, 1, 2)
        while d <= dt.date(2026, 11, 6):
            if d.weekday() < 5:
                o = p * (1 + rnd.gauss(0.0006, 0.008))
                c = o * (1 + rnd.gauss(0, 0.01))
                h = max(o, c) * (1 + abs(rnd.gauss(0, 0.004)))
                out.append((d.strftime("%Y%m%d"), round(o), round(h), round(c)))
                p = c
            d += dt.timedelta(days=1)
        return out
    from pykrx import stock
    today = dt.date.today().strftime("%Y%m%d")
    out = []
    for y in range(FROM_YEAR, dt.date.today().year + 1):
        a, b = f"{y}0101", min(f"{y}1231", today)
        last = None
        for k in range(3):
            try:
                df = stock.get_etf_ohlcv_by_date(a, b, t)
                break
            except Exception as e:
                last = e
                time.sleep(3 + 3 * k)
        else:
            sys.exit(f"KRX 조회 실패 {t} {y}: {type(last).__name__}: {str(last)[:150]}")
        for idx, r in df.iterrows():
            out.append((idx.strftime("%Y%m%d"), float(r["시가"]), float(r["고가"]), float(r["종가"])))
        time.sleep(1)
    return sorted(set(out))


def trades(rows, maxd, strict=True):
    """밤마다 1건: (매수일, 기준 수익, 변형 수익, 보유 거래일 수, 회복 여부, 끝날짜 인덱스)"""
    res = []
    for i in range(len(rows) - 1):
        d, _, _, c = rows[i]
        n, o, h, cn = rows[i + 1]
        if wd(n) == 0:
            continue
        if not (c > 0 and o > 0):
            continue
        base = o / c - 1 - COST
        if abs(base) >= CA_ABS:
            continue
        if o >= c:
            res.append((d, base, base, 1, True, i + 1))
            continue
        got, j, k = None, i + 1, 0
        while j < len(rows):
            _, oj, hj, cj = rows[j]
            if hj > 0 and (hj > c if strict else hj >= c):
                got = (0.0 - COST, k + 1, True, j)
                break
            k += 1
            if k >= maxd and cj > 0:
                got = (cj / c - 1 - COST, k, False, j)
                break
            j += 1
        if got is None:  # 자료 끝: 마지막 종가로 평가
            jj = len(rows) - 1
            got = (rows[jj][3] / c - 1 - COST, jj - i, None, jj)
        res.append((d, base, got[0], got[1], got[2], got[3]))
    return res


def cum(v):
    e = 1.0
    for x in v:
        e *= 1 + x
    return (e - 1) * 100


def mdd(v):
    e, pk, m = 1.0, 1.0, 0.0
    for x in v:
        e *= 1 + x
        pk = max(pk, e)
        m = min(m, e / pk - 1)
    return m * 100


def report(nm, rows, d0, d1):
    log(f"\n--- {nm} 매수 {d0}~{d1} ---")
    out = {}
    for vn, md in VARS:
        for strict in (True, False):
            tr = [x for x in trades(rows, md, strict) if d0 <= x[0] <= d1]
            if not tr:
                log("자료 없음")
                return out
            b = [x[1] for x in tr]
            v = [x[2] for x in tr]
            negs = [x for x in tr if rows_open_below(x)]
            rec = sum(1 for x in negs if x[4] is True)
            unres = sum(1 for x in negs if x[4] is None)
            hold = [x[3] for x in negs]
            # 동시 보유 최대 = 같은 밤을 넘기는 보유 건수 (종가에 팔고 사는 날은 겹침 아님)
            ev = {}
            for x in tr:
                si = idx[x[0]]
                for j in range(si, x[5]):
                    ev[j] = ev.get(j, 0) + 1
            worst = min(tr, key=lambda x: x[2])
            tag = "주(고가>매수가)" if strict else "참고(고가≥매수가)"
            line = (f"{vn:6s} {tag}: 밤 {len(tr)} · 시가<매수가 {len(negs)} 중 본전 회복 {rec} · 미정 {unres} · "
                    f"보유 평균 {sum(hold)/max(len(hold),1):.1f}일 최대 {max(hold) if hold else 0}일 · 동시 보유 최대 {max(ev.values())}\n"
                    f"        기준 평균 {sum(b)/len(b)*100:+.4f}% · 변형 평균 {sum(v)/len(v)*100:+.4f}% · "
                    f"최악 1건 {worst[0]} {worst[2]*100:+.2f}% (기준 {worst[1]*100:+.2f}%)")
            if vn == "V1":
                line += f"\n        누적 기준 {cum(b):+.2f}% · V1 {cum(v):+.2f}% · MDD 기준 {mdd(b):.2f}% · V1 {mdd(v):.2f}%"
            log(line)
            out[(vn, strict)] = (cum(b), cum(v), sum(b) / len(b), sum(v) / len(v))
    return out


idx, OPEN = {}, {}


def rows_open_below(x):
    return OPEN[x[0]]


def main():
    global idx, OPEN
    verdict = {}
    for t, nm in ETFS.items():
        rows = load(t)
        idx = {r[0]: i for i, r in enumerate(rows)}
        OPEN = {}
        for i in range(len(rows) - 1):
            OPEN[rows[i][0]] = 0 < rows[i + 1][1] < rows[i][3]
        log(f"\n##### {nm} ({t}) 자료 {rows[0][0]}~{rows[-1][0]} · {len(rows)}일 · 마지막 {rows[-1]}")
        chk = [x[1] for x in trades(rows, 1) if MAIN0 <= x[0] <= CHECK_END]
        log(f"[대조] 기준 누적 {MAIN0}~{CHECK_END} 매수 {len(chk)}밤 {cum(chk):+.2f}% (기록 {CHECK[t]:+.1f}% · "
            f"{'일치' if abs(round(cum(chk),1)-CHECK[t]) <= 0.1 else '불일치'})")
        r = report(nm, rows, MAIN0, "99999999")
        report(nm + " (참고)", rows, "20160101", "20241231")
        if ("V1", True) in r:
            verdict[nm] = r[("V1", True)][1] > r[("V1", True)][0]
    log("\n=== 판정 (실행 전 고정: 2025-01-02 이후, 두 ETF 모두 V1 누적 > 기준 누적, 체결 조건 주) ===")
    for nm, g in verdict.items():
        log(f"{nm}: {'V1 이득' if g else 'V1 이득 아님'}")
    log(f"■ 종합: {'V1 이득' if verdict and all(verdict.values()) else 'V1 이득 아님'}")


if __name__ == "__main__":
    main()
