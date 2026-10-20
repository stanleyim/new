# [클론 셀] 새 정보원 3단계(잠정실적 본문 전체 파싱 집계) 준비. main(earn_s1c_cell.py 파서) + dart-earn(본문 원문 ZIP 9개). 읽기 전용, 레포 무변경.
# 점검: progress(completed·16,766·실패 0) / targets 16,766행·rcept_no 중복 없음 / ZIP 9개 열기 / ZIP 안 파일 = targets(연도별 집합 일치·중복 이름 없음·0바이트 없음) / 파서 파일·bs4
import os, glob, json, zipfile, subprocess, time
import pandas as pd
t0 = time.time()
REPO = os.environ.get("EARN_REPO", "https://github.com/stanleyim/new.git")
def sh(c): subprocess.run(c, shell=True, check=True)
for br, dst in (("main", "/content/main"), ("dart-earn", "/content/earn")):
    sh(f"rm -rf {dst}; git clone -q --depth 1 --single-branch -b {br} {REPO} {dst}")

ED = "/content/earn/earn_data"
ok_all = True
def chk(name, got, exp):
    global ok_all
    ok = got == exp
    ok_all &= ok
    print(("OK " if ok else "불일치 ") + f"{name}: {got} (기대 {exp})")

prog = json.load(open(f"{ED}/progress.json"))
chk("progress completed", prog.get("completed"), True)
chk("progress stored", prog.get("stored"), 16766)
chk("progress failed_permanent", prog.get("failed_permanent"), 0)
chk("progress limit_hit/halted", (prog.get("limit_hit"), prog.get("halted")), (False, False))

tg = pd.read_csv(f"{ED}/targets.csv", dtype=str, encoding="utf-8-sig")
chk("targets 행 수", len(tg), 16766)
chk("targets rcept_no 중복", int(tg["rcept_no"].duplicated().sum()), 0)
fl = pd.read_csv(f"{ED}/failures.csv", dtype=str, encoding="utf-8-sig")
chk("failures 행 수", len(fl), 0)

zips = sorted(glob.glob(f"{ED}/raw/*.zip"))
chk("ZIP 파일", [os.path.basename(z) for z in zips], [f"{y}.zip" for y in range(2018, 2027)])
tg["year"] = tg["rcept_dt"].str.replace("-", "").str[:4]
n_zip = 0
for z in zips:
    y = os.path.basename(z)[:4]
    try:
        with zipfile.ZipFile(z) as zf:
            infos = zf.infolist()
            bad = zf.testzip()
    except zipfile.BadZipFile as e:
        chk(f"{y}.zip 열기", str(e), "정상")
        continue
    names = [i.filename for i in infos]
    n_zip += len(names)
    want = set(tg.loc[tg["year"] == y, "rcept_no"] + ".xml")
    got = set(names)
    print(f"{y}: ZIP {len(names):,}개 · 대상 {len(want):,}개 · 빠짐 {len(want - got)} · 대상 아님 {len(got - want)} · "
          f"중복 이름 {len(names) - len(got)} · 0바이트 {sum(i.file_size == 0 for i in infos)} · CRC {'정상' if bad is None else '오류 ' + bad} · "
          f"압축 해제 {sum(i.file_size for i in infos)/1e6:.1f}MB")
    ok_all &= (want == got) and len(names) == len(got) and bad is None and not any(i.file_size == 0 for i in infos)
chk("ZIP 전체 파일 수", n_zip, 16766)

chk("파서 파일 earn_s1c_cell.py", os.path.exists("/content/main/earn_s1c_cell.py"), True)
try:
    import bs4
    print(f"OK bs4 {bs4.__version__}")
except ImportError:
    ok_all = False
    print("불일치 bs4 없음 (pip install beautifulsoup4 필요)")
for d in ("/content/main", "/content/earn"):
    print(d, subprocess.run(f"git -C {d} log -1 --format='%h %ci %s'", shell=True, capture_output=True, text=True).stdout.strip())
print(("전부 OK" if ok_all else "※ 불일치 있음 — 다음 셀 진행 전 보고") + f" · 클론 완료 {time.time()-t0:.0f}s")
