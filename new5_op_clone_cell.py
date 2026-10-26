# [클론 셀] Overnight Paper 점검용 — main 브랜치 읽기 전용(공개 레포, 토큰 불필요). 레포 변경 없음.
# 실행 순서: 런타임 → 세션 다시 시작 → 이 셀 → new5_krx_live_probe_cell.py
import glob, os, subprocess, time
import pandas as pd
t0 = time.time()
def sh(c): subprocess.run(c, shell=True, check=True)
sh("rm -rf /content/main; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git /content/main")
D = "/content/main/overnight_paper"
print("커밋:", subprocess.run("git -C /content/main log -1 --format='%h %ci %s'", shell=True, capture_output=True, text=True).stdout.strip())
print("overnight_paper 파일:", sorted(os.path.basename(p) for p in glob.glob(f"{D}/*")))
k = pd.read_parquet(f"{D}/krx_daily.parquet")
k["date"] = k["date"].astype(str).str.replace("-", "").str[:8]
last = k["date"].max()
print(f"krx_daily 마지막 날짜 {last} · 그날 행 수 {int((k['date'] == last).sum())} · 전체 거래일 {k['date'].nunique()}")
for k_ in ("KRX_ID", "KRX_PW"):                 # 값은 출력하지 않음
    try:
        from google.colab import userdata
        v = userdata.get(k_)
        print(f"OK Colab 보안 비밀 {k_} 읽기 (길이 {len(v)})")
    except Exception as e:
        print(f"실패 {k_} 보안 비밀 읽기: {type(e).__name__} {str(e)[:120]}")
print(f"클론 완료 {time.time() - t0:.0f}s")
