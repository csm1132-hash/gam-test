# -*- coding: utf-8 -*-
"""공개 데이터 내려받기 — 10주제의 원본을 raw/ 에 저장한다.

**KOSIS·서울 열린데이터광장을 빼면 키도 로그인도 필요 없습니다.** 수업 중에 20명이
동시에 받아도 막히지 않는 곳만 골랐습니다. 두 곳은 무료 인증키를 .env 에
(KOSIS_API_KEY=... · SEOUL_API_KEY=...) 넣어야 받아지고, 키가 없으면 그 파일만 건너뜁니다.

한 번 받으면 raw/ 에 남으므로 다시 돌려도 새로 받지 않습니다.
(--force 를 주면 다시 받습니다)

실행 (저장소 최상위에서):
    python tools/fetch_all.py
    python tools/fetch_all.py --force
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 윈도우 콘솔(cp949)은 한글 · 기호를 못 찍고 멈출 때가 있다. 출력만 UTF-8 로 맞춘다
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
# HTTP 헤더는 latin-1 만 담는다. 한글을 넣으면 UnicodeEncodeError 가 난다
UA = {"User-Agent": "gam-test-class/1.0 (data analysis class)"}
TIMEOUT = 90

SEOUL = (37.5665, 126.9780)

# 날씨는 **모델을 ERA5로 못 박는다** (&models=era5).
# 모델을 적지 않으면 Open-Meteo가 그때그때 '가장 알맞은 모델'(best_match)을 골라 섞어 준다.
# 그 구성이 바뀌면 같은 주소로 받아도 지난 값이 달라진다 — 2026-10에 실제로 2024년 서울
# 열대야가 15일에서 12일로 바뀌었다. 출처에 모델까지 적어야 다시 받아도 같은 값이 나온다.
ERA5 = "&models=era5"

SOURCES: list[tuple[str, str, str | list[str]]] = [
    # (저장 파일명, 설명, URL) — URL 이 목록이면 차례로 받아 한 파일로 잇는다
    ("seoul_weather.json",
     "서울 일별 기온·강수 1950~2024 (Open-Meteo Archive · ERA5 고정)",
     "https://archive-api.open-meteo.com/v1/archive"
     f"?latitude={SEOUL[0]}&longitude={SEOUL[1]}"
     "&start_date=1950-01-01&end_date=2024-12-31"
     "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
     "&timezone=Asia%2FSeoul" + ERA5),

    ("seoul_air.json",
     "서울 시간별 미세먼지 2023~2024 (Open-Meteo Air Quality · CAMS)",
     "https://air-quality-api.open-meteo.com/v1/air-quality"
     f"?latitude={SEOUL[0]}&longitude={SEOUL[1]}"
     "&start_date=2023-01-01&end_date=2024-12-31"
     "&hourly=pm10,pm2_5&timezone=Asia%2FSeoul"),

    ("working_hours.csv",
     "연간 노동시간 (Our World in Data)",
     "https://ourworldindata.org/grapher/annual-working-hours-per-worker.csv"
     "?csvType=full&useColumnShortNames=true"),

    ("life_expectancy.csv",
     "기대수명 (Our World in Data)",
     "https://ourworldindata.org/grapher/life-expectancy.csv"
     "?csvType=full&useColumnShortNames=true"),

    ("wb_fertility.json",
     "합계출산율 전세계 (World Bank SP.DYN.TFRT.IN)",
     "https://api.worldbank.org/v2/country/all/indicator/SP.DYN.TFRT.IN"
     "?format=json&per_page=20000&date=1960:2023"),

    ("wb_pop_kor.json",
     "한국 인구·고령화 (World Bank)",
     "https://api.worldbank.org/v2/country/KOR/indicator/"
     "SP.POP.TOTL;SP.POP.65UP.TO.ZS;SP.URB.TOTL.IN.ZS"
     "?format=json&per_page=2000&source=2"),

    ("wb_internet.json",
     "인터넷 사용률 전세계 (World Bank IT.NET.USER.ZS)",
     "https://api.worldbank.org/v2/country/all/indicator/IT.NET.USER.ZS"
     "?format=json&per_page=20000&date=1990:2023"),

    ("usdkrw.json",
     "원달러 환율 1999~ (Frankfurter · ECB)",
     "https://api.frankfurter.dev/v1/1999-01-04..2024-12-31"
     "?base=USD&symbols=KRW"),

    ("wb_countries.json",
     "World Bank 국가 목록 — 대륙·소득군 같은 집계치를 걸러내는 데 씁니다",
     "https://api.worldbank.org/v2/country?format=json&per_page=400"),

    *[(f"city_{cid}.json", f"{cn} 일별 기온·강수 1991~2020 (Open-Meteo Archive · ERA5 고정)",
       "https://archive-api.open-meteo.com/v1/archive"
       f"?latitude={lat}&longitude={lon}"
       "&start_date=1991-01-01&end_date=2020-12-31"
       "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
       f"&timezone=auto" + ERA5)
      for cid, cn, lat, lon in (
          ("seoul", "서울", 37.5665, 126.9780),
          ("tokyo", "도쿄", 35.6895, 139.6917),
          ("london", "런던", 51.5072, -0.1276),
          ("singapore", "싱가포르", 1.3521, 103.8198),
      )],

    ("quakes.geojson",
     "규모 5.5 이상 지진 2000~2024 (USGS) — 2만건 상한에 걸리지 않는 기준",
     "https://earthquake.usgs.gov/fdsnws/event/1/query"
     "?format=geojson&starttime=2000-01-01&endtime=2024-12-31"
     "&minmagnitude=5.5&orderby=time&limit=20000"),

    ("quakes_korea.geojson",
     "한반도 주변 지진 2000~2024 (USGS · 규모 3.0 이상)",
     "https://earthquake.usgs.gov/fdsnws/event/1/query"
     "?format=geojson&starttime=2000-01-01&endtime=2024-12-31"
     "&minmagnitude=3.0&minlatitude=33&maxlatitude=39.5"
     "&minlongitude=124&maxlongitude=132&limit=20000"),

    # 위키백과는 표만 따로 주지 않는다. 문서 전체가 JSON 안에 HTML 로 들어 있으므로
    # 우승자·국적 표는 build 단계에서 뽑는다. 문서는 수시로 고쳐지므로 다시 받으면 내용이 바뀔 수 있다
    ("miss_universe.json",
     "미스 유니버스 역대 우승자·국적 1952~ (Wikipedia · List of Miss Universe titleholders)",
     "https://en.wikipedia.org/w/api.php?action=parse"
     "&page=List_of_Miss_Universe_titleholders&prop=text&format=json"),

    ("owid_continents.csv",
     "나라별 대륙 (Our World in Data) — 위키백과 표에 대륙이 없어 붙이는 데 씁니다",
     "https://ourworldindata.org/grapher/continents-according-to-our-world-in-data.csv"
     "?csvType=full&useColumnShortNames=true"),

    # 세계 소득 분포. PIP 는 '하루 X달러 아래에 사는 사람의 비율'을 소득선 하나씩만 준다
    # (여러 개를 한 번에 주면 숫자를 이어 붙여 엉뚱한 선으로 읽는다). 그래서 소득선을
    # 0.5~500달러 사이에 12%씩 벌려 62번 받아 한 파일로 잇는다 — 이것이 분포 곡선이다.
    # 발표판(release_version)을 못 박는다. 새 판이 나오면 지난 해 값도 고쳐진다.
    # 연도도 하나로 좁힌다 — year=all 은 한 번에 1분씩 걸려 62번이면 1시간이다.
    # 2024년은 이 발표판에서 추정(nowcast)이 아닌 마지막 해다
    ("pip_world.json",
     "세계 소득 분포 곡선 2024 (World Bank PIP · 2021 PPP · 2026-09 발표판) — 62번 나눠 받습니다",
     [f"https://api.worldbank.org/pip/v1/pip-grp?country=WLD&year=2024&group_by=none"
      f"&povline={round(0.5 * 1.12 ** i, 2)}"
      "&ppp_version=2021&release_version=20260922&format=json"
      for i in range(62)]),

    # per_page 를 너무 크게 주거나 연도 범위를 비워 두면 400 을 돌려준다. 범위를 적는다
    ("wb_money_kor.json",
     "한국 소비 PPP·소비자물가 — 원화를 PIP 달러로 바꾸는 데 씁니다 (World Bank)",
     "https://api.worldbank.org/v2/country/KOR/indicator/PA.NUS.PRVT.PP;FP.CPI.TOTL"
     "?format=json&per_page=1000&source=2&date=2015:2025"),

    ("wb_gni.json",
     "1인당 국민소득 전세계 (World Bank NY.GNP.PCAP.CD · Atlas)",
     "https://api.worldbank.org/v2/country/all/indicator/NY.GNP.PCAP.CD"
     "?format=json&per_page=5000&date=2019:2025"),

    ("wb_pop_all.json",
     "인구 전세계 (World Bank SP.POP.TOTL)",
     "https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL"
     "?format=json&per_page=5000&date=2019:2025"),

    ("happiness.csv",
     "나라별 삶의 만족도 0~10 (World Happiness Report · Our World in Data)",
     "https://ourworldindata.org/grapher/happiness-cantril-ladder.csv"
     "?csvType=full&useColumnShortNames=true"),

    ("gdp_pcap_ppp.csv",
     "1인당 GDP, 구매력 기준 (World Bank · Our World in Data)",
     "https://ourworldindata.org/grapher/gdp-per-capita-worldbank.csv"
     "?csvType=full&useColumnShortNames=true"),

    ("alcohol.csv",
     "15세 이상 1인당 순수 알코올 소비량 (WHO · Our World in Data)",
     "https://ourworldindata.org/grapher/total-alcohol-consumption-per-capita-litres-of-pure-alcohol.csv"
     "?csvType=full&useColumnShortNames=true"),

    # 올림픽도 위키백과 표를 읽는다. 대회가 끝날 때마다 고쳐지므로 받은 시점이 곧 기준이다
    ("olympics_korea.json",
     "한국의 대회별·종목별 올림픽 메달 (Wikipedia · South Korea at the Olympics)",
     "https://en.wikipedia.org/w/api.php?action=parse"
     "&page=South_Korea_at_the_Olympics&prop=text&format=json"),

    ("olympics_alltime.json",
     "역대 나라별 올림픽 메달 (Wikipedia · All-time Olympic Games medal table)",
     "https://en.wikipedia.org/w/api.php?action=parse"
     "&page=All-time_Olympic_Games_medal_table&prop=text&format=json"),

    # 주소 대신 함수를 두면 그 함수가 받는다 — 여러 번 나눠 받아야 하는 곳 (get_lotto 참고)
    ("lotto.json",
     "로또 6/45 회차별 당첨번호·당첨금 (동행복권 결과 화면 · 비공식 경로) — 약 125번 나눠 받습니다",
     "get_lotto"),

    # ── 여기부터는 인증키가 필요합니다 (KOSIS · 무료 · kosis.kr/openapi) ──
    # 키는 저장소 최상위 .env 에 KOSIS_API_KEY=... 로 둔다. .env 는 git·배포에서 빠진다.
    # 키가 없으면 이 파일만 건너뛴다. 주소의 {KOSIS_API_KEY} 자리에 실행할 때 끼워 넣는다
    # 시작 연도만 주면 그해 하나만 온다. '최근 10개 연도'로 받아야 새 발표분이 따라온다
    ("kosis_household_income.json",
     "가구주 종사상지위별 가구 경상소득 최근 10년 (통계청 가계금융복지조사 · KOSIS 101/DT_1HDAAA09)",
     "https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList"
     "&apiKey={KOSIS_API_KEY}&orgId=101&tblId=DT_1HDAAA09"
     "&itmId=T01+T03&objL1=A0100&objL2=ALL&objL3=C04"
     "&format=json&jsonVD=Y&prdSe=Y&newEstPrdCnt=10"),

    # 품목 코드: 0 총지수 · K01119 자장면 · A01110 라면 · K01133 커피(외식) · G03201 시내버스료
    # G03203 택시료 · I01101 TV · B01101 소주 · K01129 치킨 · A01101 쌀 · H02101 휴대전화기
    ("kosis_cpi.json",
     "품목별 소비자물가지수 1966~ (통계청 소비자물가조사 · KOSIS 101/DT_1J22001 · 전국)",
     "https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList"
     "&apiKey={KOSIS_API_KEY}&orgId=101&tblId=DT_1J22001&itmId=T&objL1=T10"
     "&objL2=0+K01119+A01110+K01133+G03201+G03203+I01101+B01101+K01129+A01101+H02101"
     "&format=json&jsonVD=Y&prdSe=Y&newEstPrdCnt=60"),

    ("kosis_vital.json",
     "혼인·이혼 건수와 조이혼율 1981~ (통계청 인구동향조사 · KOSIS 101/DT_1B8000G)",
     "https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList"
     "&apiKey={KOSIS_API_KEY}&orgId=101&tblId=DT_1B8000G&itmId=T1&objL1=00&objL2=20+30+31"
     "&format=json&jsonVD=Y&prdSe=Y&newEstPrdCnt=50"),

    ("kosis_marriage_age.json",
     "평균 초혼 연령 1990~ (통계청 인구동향조사 · KOSIS 101/DT_1B83A05)",
     "https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList"
     "&apiKey={KOSIS_API_KEY}&orgId=101&tblId=DT_1B83A05&itmId=T1+T2&objL1=00"
     "&format=json&jsonVD=Y&prdSe=Y&newEstPrdCnt=40"),

    # 시도별 표(DT_1B85006)는 동거기간이 5년 단위라 '1년 미만'이 없다. 연령별 표는 9년까지
    # 1년 단위로 나눈다. 연령 '계'(000)의 남편 수(T1)가 그해 이혼 건수다
    ("kosis_divorce_duration.json",
     "동거기간별 이혼 1990~ (통계청 인구동향조사 · KOSIS 101/DT_1B85008 · 연령 계)",
     "https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList"
     "&apiKey={KOSIS_API_KEY}&orgId=101&tblId=DT_1B85008&itmId=T1&objL1=000&objL2=ALL"
     "&format=json&jsonVD=Y&prdSe=Y&newEstPrdCnt=40"),

    # ── 서울 열린데이터광장 (인증키 · 무료 · data.seoul.go.kr) ──
    # 교통카드로 찍은 역별·시간대별 승하차. {month} 는 자료가 있는 가장 최근 달로 채운다.
    # 한 번에 1,000줄까지 준다 (역×노선 약 620줄)
    ("seoul_subway_hourly.json",
     "서울 지하철 역별·시간대별 승하차 한 달치 (서울 열린데이터광장 CardSubwayTime)",
     "http://openapi.seoul.go.kr:8088/{SEOUL_API_KEY}/json/CardSubwayTime/1/1000/{month}"),

    # 생활시간조사는 5년마다(1999~). 주기가 해마다가 아니라 prdSe=F 로 받는다.
    # 평균시간(T10)은 표마다 뜻이 다르다 — 202001 은 10세 이상 모두의 평균,
    # 202003 은 그 행동을 한 사람만의 평균(행위자 평균). 출근은 행위자 평균이라야 뜻이 있다
    *[(f"kosis_timeuse_{kind}.json",
       f"생활시간 {desc} 1999~ (통계청 생활시간조사 · KOSIS 101/{tbl})",
       "https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList"
       f"&apiKey={{KOSIS_API_KEY}}&orgId=101&tblId={tbl}&itmId=T10"
       "&objL1=A0+A1&objL2=S0+S1+S2&objL3=C1&objL4=S11+S921+S922+S4+S82+S8+S2"
       "&format=json&jsonVD=Y&prdSe=F&newEstPrdCnt=6")
      for kind, desc, tbl in (("all", "전체 평균", "DT_1TS202001"),
                              ("doer", "행위자 평균", "DT_1TS202003"))],
]


LOTTO = "https://www.dhlottery.co.kr/lt645/selectPstLt645InfoNew.do"
LOTTO_OLDER = LOTTO + "?srchDir=older&srchCursorLtEpsd={n}"      # n 보다 앞선 10회차
LOTTO_AT = LOTTO + "?srchDir=center&srchLtEpsd={n}"             # n 을 둘러싼 10회차


def get_lotto(dest: Path) -> int:
    """로또 1회(2002-12-07)부터 최신 회차까지 당첨번호·당첨금을 모은다.

    **공식 API 가 아닙니다.** 동행복권은 2025년 사이트를 바꾸며 당첨번호 API 와 엑셀
    내려받기를 없앴다. 이 주소는 새 결과 화면이 쓰는 내부 요청이다. 사이트가 바뀌면 깨진다.

    'older' 는 n 보다 앞선 10회차를 주지만, n 이 최신 회차보다 크면 빈 목록을 준다
    (최신+1 을 줘도 비어서 최신 회차를 놓친다). 그래서 날짜로 최신 회차를 어림하고,
    'center'(n 을 둘러싼 10회차)로 결과가 나올 때까지 한 칸씩 내려가 최신 회차를 잡은 뒤
    'older' 로 1회까지 거슬러 간다.
    """
    def page(url):
        get(url, part)
        return (json.loads(part.read_text(encoding="utf-8")).get("data") or {}).get("list") or []

    n = (time.time() - time.mktime((2002, 12, 7, 0, 0, 0, 0, 0, -1))) // (7 * 86400) + 3
    n, rows, part = int(n), {}, dest.with_suffix(".part")
    while not rows and n > 1:             # 아직 추첨 전인 회차면 빈 목록 — 한 칸 내려간다
        rows.update({r["ltEpsd"]: r for r in page(LOTTO_AT.format(n=n))})
        n -= 1
    while min(rows) > 1:
        got = page(LOTTO_OLDER.format(n=min(rows)))
        if not got:
            break
        rows.update({r["ltEpsd"]: r for r in got})
        time.sleep(0.3)
    part.unlink(missing_ok=True)
    assert min(rows) == 1, f"1회까지 받지 못했습니다 (가장 앞 {min(rows)}회)"
    dest.write_text(json.dumps([rows[k] for k in sorted(rows)], ensure_ascii=False),
                    encoding="utf-8")
    return dest.stat().st_size


def get_latest_month(url: str, dest: Path) -> int:
    """{month} 자리에 지난달부터 거슬러 YYYYMM 을 넣어, 자료가 있는 가장 최근 달을 받는다.

    서울 열린데이터광장은 달마다 자료가 늦게 올라온다(보통 다음 달 중순). 키가 틀리거나
    요청이 잘못돼도 HTTP 200 으로 {"RESULT": {"CODE": "INFO-100", ...}} 를 주므로 본문을 본다.
    """
    part = dest.with_suffix(".part")
    y, m = time.localtime()[:2]
    for _ in range(6):
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        get(url.replace("{month}", f"{y}{m:02d}"), part)
        body = json.loads(part.read_text(encoding="utf-8"))
        svc = next((v for v in body.values() if isinstance(v, dict) and "row" in v), None)
        if svc:
            assert svc["list_total_count"] <= len(svc["row"]), "한 번에 다 받지 못했습니다 — 범위를 늘리세요"
            part.replace(dest)
            return dest.stat().st_size
        code = (body.get("RESULT") or {}).get("CODE", "")
        if code != "INFO-200":                       # INFO-200 = 그달 자료 없음. 그 밖은 진짜 오류
            part.unlink()
            raise ValueError(f"서울 열린데이터 오류 {code}: {(body.get('RESULT') or {}).get('MESSAGE')}")
        time.sleep(0.3)
    part.unlink(missing_ok=True)
    raise ValueError("최근 6개월 안에 자료가 있는 달이 없습니다")


def env(name: str) -> str | None:
    """환경변수 → 없으면 저장소 최상위 .env 에서 찾는다."""
    if os.environ.get(name):
        return os.environ[name]
    f = RAW.parent.parent / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name and v.strip():
                return v.strip()
    return None


def get(url: str, dest: Path, tries: int = 4) -> int:
    """받는다. 잠깐의 실패면 기다렸다가 다시 시도한다.

    공개 API 는 대부분 호출 제한이 있고(429), 큰 파일은 받다가 끊기기도 한다
    (IncompleteRead · 시간 초과). 한 번 실패했다고 포기하지 말고, 간격을 늘려 가며
    다시 부른다. 주소가 틀린 것(404 같은)은 다시 불러도 소용없으니 바로 알린다.
    """
    wait = 5
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = r.read()
            dest.write_bytes(data)
            return len(data)
        except urllib.error.HTTPError as e:
            if (e.code != 429 and e.code < 500) or i == tries - 1:
                raise
            why = str(e.code)
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if i == tries - 1:
                raise
            why = type(e).__name__
        print(f"          {why} — {wait}초 뒤 다시 시도합니다 ({i+1}/{tries-1})")
        time.sleep(wait)
        wait *= 2
    raise RuntimeError("도달 불가")


def get_many(urls: list[str], dest: Path) -> int:
    """여러 주소에서 받은 JSON 목록을 이어 붙여 한 파일로 둔다.

    하나라도 실패하면 반쪽 곡선이 남지 않게 아무것도 쓰지 않는다.
    """
    rows = []
    part = dest.with_suffix(".part")
    for u in urls:
        get(u, part)
        rows += json.loads(part.read_text(encoding="utf-8"))
        time.sleep(0.4)
    part.unlink()
    dest.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return dest.stat().st_size


def main() -> int:
    force = "--force" in sys.argv
    RAW.mkdir(parents=True, exist_ok=True)
    fail = 0
    for name, desc, url in SOURCES:
        dest = RAW / name
        if dest.exists() and not force:
            print(f"  건너뜀  {name:<22} 이미 있음 ({dest.stat().st_size/1024:,.0f}KB)")
            continue
        # 주소의 {KOSIS_API_KEY}·{SEOUL_API_KEY} 자리에 .env 의 키를 끼워 넣는다
        need = re.findall(r"\{([A-Z]+_API_KEY)\}", url) if isinstance(url, str) else []
        missing = [k for k in need if not env(k)]
        if missing:
            print(f"  건너뜀  {name:<22} 키가 없습니다 (.env 에 {missing[0]}=...)")
            continue
        for k in need:
            url = url.replace("{" + k + "}", urllib.parse.quote(env(k), safe=""))
        t0 = time.time()
        try:
            if url == "get_lotto":
                n = get_lotto(dest)
            elif isinstance(url, list):
                n = get_many(url, dest)
            elif "{month}" in url:
                n = get_latest_month(url, dest)
            else:
                n = get(url, dest)
            # KOSIS 는 키가 틀려도 200 으로 {"err": ...} 를 준다. 그대로 두면 build 에서 터진다
            body = json.loads(dest.read_text(encoding="utf-8")) if name.endswith(".json") else None
            if isinstance(body, dict) and "err" in body:
                dest.unlink()
                raise ValueError(f"KOSIS 오류 {body['err']}: {body.get('errMsg')}")
            print(f"  받음    {name:<22} {n/1024:>7,.0f}KB  {time.time()-t0:.1f}s  {desc}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead,
                ValueError, AssertionError) as e:
            print(f"  실패    {name:<22} {e}")
            fail += 1
        time.sleep(0.4)          # 상대 서버에 예의를 지킨다

    print()
    print(f"총 {len(SOURCES)}건 / 실패 {fail}건 · 저장 위치 {RAW}")
    if fail:
        print("실패한 것은 잠시 뒤 다시 실행하면 대개 받아집니다.")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
