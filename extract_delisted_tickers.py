"""
delisted_candidates_2916.csv 재현 스크립트.

로직: pit-data 브랜치 pit_data_extra/sector_2014.parquet ~ sector_2026.parquet
(13개 파일)에서 종목별 최초/최종 등장일을 집계하고, main 브랜치
data/universe.parquet의 현재 439종목에 없는 티커만 추출.

결과 (2026-09-26 실행 기준, 실측):
  - sector_*.parquet 고유 티커 총계: 3,355
  - 현재 439 모델 유니버스에 없는 티커: 2,916
    - 이 중 last_date가 2026(최신 스냅샷)인 것: 2,393건 — 현재도 상장되어 있으나
      모델의 439 유니버스(유동성 등 필터 통과) 밖에 있는 종목
    - last_date가 2014~2025인 것: 523건 — 진짜 상장폐지/합병 등으로 이탈한 종목
      (연도별 17~75건, 특정 연도 쏠림 없음 — 기존 메모리 기록과 일치)

주의: sector_YYYY.parquet은 월간 스냅샷(월 1회)이므로 first_date/last_date는
"이 스냅샷에 마지막으로 잡힌 달" 근사치이지, 정확한 상장일/상장폐지일이 아니다.
"""
import glob
import pandas as pd

SECTOR_GLOB = "pit_data_extra/sector_*.parquet"  # pit-data 브랜치 체크아웃 기준 상대경로
UNIVERSE_PATH = "data/universe.parquet"  # main 브랜치 체크아웃 기준 상대경로
OUT_CSV = "delisted_candidates_2916.csv"


def main():
    sector_files = sorted(glob.glob(SECTOR_GLOB))
    if not sector_files:
        raise RuntimeError(f"{SECTOR_GLOB} 매칭 파일 없음 — pit-data 브랜치 체크아웃 확인")

    frames = [pd.read_parquet(f, columns=["date", "ticker", "종목명", "market"]) for f in sector_files]
    all_sector = pd.concat(frames, ignore_index=True)

    agg = all_sector.groupby("ticker").agg(
        name=("종목명", "last"), market=("market", "last"),
        first_date=("date", "min"), last_date=("date", "max"),
    ).reset_index()

    uni = pd.read_parquet(UNIVERSE_PATH)
    current_tickers = set(uni["ticker"])

    not_current = agg[~agg["ticker"].isin(current_tickers)].copy()
    not_current["last_year"] = not_current["last_date"].dt.year
    not_current = not_current.sort_values(["last_year", "ticker"])

    not_current.to_csv(OUT_CSV, index=False)
    print(f"전체 고유 티커: {len(agg)}, 현재 유니버스 제외 후: {len(not_current)}")
    print(not_current["last_year"].value_counts().sort_index())


if __name__ == "__main__":
    main()
