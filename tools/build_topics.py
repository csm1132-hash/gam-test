# -*- coding: utf-8 -*-
"""공개 데이터 → 주제마다 5문항.

**모든 숫자는 raw/ 의 원본에서 계산합니다.** 지어낸 값이 하나도 없어야 합니다.
결과는 앱이 읽는 topics/*.json 과, 사람이 읽는 tools/정답지.md 로 나갑니다.

정답지와 이 스크립트는 .vercelignore 에 들어 있어 **배포되지 않습니다.**
공개 URL 에 정답지가 올라가면 게임이 성립하지 않습니다.

실행 (저장소 최상위에서):
    python tools/build_topics.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import datetime as dt
from collections import Counter
from html.parser import HTMLParser
from math import exp, log
from pathlib import Path
from statistics import mean

# 윈도우 콘솔(cp949)은 이모지 · 기호를 못 찍고 멈춘다. 출력만 UTF-8 로 맞춘다
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = Path(__file__).resolve().parent          # tools/
APP = HERE.parent                                # 저장소 최상위 = 배포되는 곳
RAW = APP / "data" / "raw"
OUT = APP / "topics"

TOPICS: list[dict] = []

# 화면의 주제 목록에 내보낼 주제. **적은 순서대로** 화면에 나온다. 나머지도 계산은 그대로
# 하고 topics/*.json 도 남지만 목록(topics.json)에서 빠져 보이지 않는다.
# 다시 보이게 하려면 여기에 id 를 더한다
SHOWN = ["money", "prices", "marriage", "daily", "happiness",
         "alcohol", "olympics", "lotto", "subway", "miss-universe"]


def load_json(name):
    return json.load(io.open(RAW / name, encoding="utf-8"))


def load_csv(name):
    return list(csv.DictReader(io.open(RAW / name, encoding="utf-8")))


# World Bank·OWID 응답에는 '유럽연합' '고소득국' 같은 **집계치**가 섞여 있다.
# 그대로 순위를 매기면 나라와 대륙을 한 줄에 놓고 세는 셈이 된다.
REAL = {x["id"] for x in load_json("wb_countries.json")[1]
        if x["region"]["id"] != "NA"}


def wb_countries(rows, year):
    """그 해의 실제 국가 값만 남긴다 (집계치 제외)."""
    return {r["country"]["value"]: r["value"] for r in rows
            if int(r["date"]) == year and r["countryiso3code"] in REAL}


def owid_countries(rows, year, col):
    """OWID 도 마찬가지. code 가 비었거나 OWID_ 로 시작하면 집계치다.
    단 코소보는 ISO 코드가 없어 OWID_KOS 로 적혀 있을 뿐 나라다 — 빼면 그 아래 순위가
    한 칸씩 어긋난다 (세계행복보고서 대만 26위가 25위로 나왔다)."""
    return {r["entity"]: float(r[col]) for r in rows
            if int(r["year"]) == year and r["code"]
            and (not r["code"].startswith("OWID") or r["code"] == "OWID_KOS")}


def rank_of(values, key, high_first=True):
    """같은 값은 같은 순위. OWID 는 점수를 소수 둘째 자리로 줘서 동점이 생긴다
    (한국·콜롬비아 6.04) — 정렬 순서에 맡기면 동점끼리 순위가 갈린다."""
    v = values[key]
    return 1 + sum(1 for x in values.values() if (x > v if high_first else x < v))


def topic(tid, emoji, title, blurb, source, source_url, questions, caveat=""):
    """caveat = 이 데이터를 믿을 때의 한계. 앱 화면에도 그대로 나간다.

    출처가 다르면 같은 '서울 기온'도 값이 다르다. 그 사실을 감추지 않는다.
    """
    TOPICS.append({"id": tid, "emoji": emoji, "title": title, "blurb": blurb,
                   "source": source, "source_url": source_url,
                   "caveat": caveat, "questions": questions})


# '더 많을까 적을까' 모드의 문제. 주제마다 계산한 값 둘을 맞붙인다 → extras/versus.json
VERSUS: list[dict] = []
KEPT: set[str] = set()          # 원본이 없어 지난번 계산값을 그대로 쓴 주제


def vs(tid, tag, q, a, b):
    """a·b = (이름, 값, 화면에 보일 글자). 값이 큰 쪽이 정답이다. 같으면 문제가 안 된다."""
    assert a[1] != b[1], f"{q} {a[0]}·{b[0]}: 두 값이 같습니다"
    VERSUS.append({"tid": tid, "tag": tag, "q": q,
                   "a": {"label": a[0], "value": round(float(a[1]), 3), "show": a[2]},
                   "b": {"label": b[0], "value": round(float(b[1]), 3), "show": b[2]}})


def slider(qid, text, answer, unit, lo, hi, step, basis, why, hint=None):
    """눈금(step)이 정수면 정답도 정수로 맞춘다 — 화면의 정답이 고르는 값과 같은 꼴이 되게.

    hint 는 문항 바로 아래 작게 보이는 실마리. 없으면 아무것도 나오지 않는다.
    """
    q = {"id": qid, "type": "slider", "text": text, "unit": unit,
         "min": lo, "max": hi, "step": step,
         "answer": round(float(answer)) if float(step).is_integer()
         else round(float(answer), 1),
         "basis": basis, "why": why}
    if hint:
        q["hint"] = hint
    return q


def choice(qid, text, options, answer, basis, why, hint=None):
    """answer 를 목록으로 주면 공동 정답이다 (공동 1위처럼 하나로 고를 수 없을 때).
    하나뿐이면 문자열로 펴서, 정답이 하나인 문항의 모양은 그대로 둔다."""
    if isinstance(answer, list) and len(answer) == 1:
        answer = answer[0]
    for a in answer if isinstance(answer, list) else [answer]:
        assert a in options, f"{qid}: 정답이 선택지에 없습니다"
    q = {"id": qid, "type": "choice", "text": text, "options": options,
         "answer": answer, "basis": basis, "why": why}
    if hint:
        q["hint"] = hint
    return q


# ════════════════════════════════════════════════════════════════
# 기상 공통
# ════════════════════════════════════════════════════════════════
w = load_json("seoul_weather.json")["daily"]
W = [{"d": dt.date.fromisoformat(t), "hi": hi, "lo": lo, "rain": r}
     for t, hi, lo, r in zip(w["time"], w["temperature_2m_max"],
                             w["temperature_2m_min"], w["precipitation_sum"])]
W24 = [x for x in W if x["d"].year == 2024]


def years(a, b):
    return [x for x in W if a <= x["d"].year <= b]


# ── 1. 서울 기온 ─────────────────────────────────────────────────
# ERA5 는 1950~70년대 구간의 품질이 낮고(초기 확장), 격자 평균이라 도심
# 관측값보다 낮게 나온다. 그래서 '역대 최고기온'이나 '폭염일 추세'처럼
# 그 구간에 기대는 문항은 쓰지 않는다. 열대야·연평균처럼 신호가 뚜렷한 것만 쓴다.
tropical_24 = sum(1 for x in W24 if x["lo"] >= 25)
trop_50s = sum(1 for x in years(1950, 1959) if x["lo"] >= 25) / 10
trop_10s = sum(1 for x in years(2015, 2024) if x["lo"] >= 25) / 10
t_ratio = trop_10s / trop_50s if trop_50s else 0
mean_50s = mean((x["hi"] + x["lo"]) / 2 for x in years(1950, 1959))
mean_10s = mean((x["hi"] + x["lo"]) / 2 for x in years(2015, 2024))
warming = mean_10s - mean_50s
below0_24 = sum(1 for x in W24 if x["lo"] < 0)
hot_days = Counter((x["d"].month, x["d"].day)
                   for x in years(1995, 2024) if x["hi"] >= 33)
peak_day = hot_days.most_common(1)[0]
# 한계 문장의 숫자도 손으로 적지 않는다 — 원본에서 가장 더웠던 날을 찾는다
hottest = max(W, key=lambda x: x["hi"])

topic(
    "seoul-heat", "🌡️", "서울은 얼마나 더워졌나",
    "1950년부터 2024년까지, 서울의 매일 기온",
    "Open-Meteo Archive (ERA5 재분석)", "https://open-meteo.com/",
    [
        slider("h1", "2024년 서울에서 열대야(밤 최저기온 25도 이상)는 며칠이었을까요?",
               tropical_24, "일", 0, 60, 1,
               f"2024년 일최저기온 25도 이상인 날 {tropical_24}일",
               "유난히 더웠던 밤 몇 번이 기억을 지배합니다. 실제 일수는 세어 봐야 "
               "압니다."),
        # 보기는 배수에 가장 가까운 것이 정답이 되게 경계를 둔다 (2.8배 → «약 3배»)
        choice("h2", "1950년대와 2015~2024년, 열대야는 몇 배 차이일까요?",
               ["거의 같다", "약 2배", "약 3배", "약 5배"],
               ["거의 같다", "약 2배", "약 3배", "약 5배"][
                   0 if t_ratio < 1.5 else 1 if t_ratio < 2.5
                   else 2 if t_ratio < 4 else 3],
               f"연평균 열대야 1950년대 {trop_50s:.1f}일 → 2015~2024년 "
               f"{trop_10s:.1f}일 ({t_ratio:.1f}배)",
               "'옛날에도 더웠다'는 느낌과 기록은 다릅니다. 기억은 유난히 더웠던 "
               "몇 밤으로 남지만, 기록은 해마다 빠짐없이 셉니다."),
        slider("h3", "1950년대와 비교해 서울의 연평균 기온은 몇 도 올랐을까요?",
               warming, "도", 0, 4, 0.1,
               f"1950년대 {mean_50s:.2f}도 → 2015~2024년 {mean_10s:.2f}도 "
               f"(+{warming:.2f}도)",
               "1~2도는 작아 보이지만, 그 변화가 열대야 일수를 몇 배로 바꿉니다. "
               "평균의 변화와 극단의 변화는 크기가 다릅니다."),
        choice("h4", "최근 30년간 33도 이상인 날이 가장 잦았던 날짜는?",
               ["7월 초", "7월 말", "8월 초", "8월 말"],
               "7월 말" if peak_day[0][0] == 7 and peak_day[0][1] >= 20
               else "8월 초" if peak_day[0][0] == 8 and peak_day[0][1] <= 10
               else "7월 초" if peak_day[0][0] == 7 else "8월 말",
               f"1995~2024년 중 가장 잦았던 날짜는 {peak_day[0][0]}월 "
               f"{peak_day[0][1]}일 ({peak_day[1]}회)",
               "장마가 끝나는 시점과 가장 더운 시점은 다릅니다."),
        slider("h5", "2024년 서울에서 밤 기온이 영하로 내려간 날은 며칠이었을까요?",
               below0_24, "일", 30, 150, 5,
               f"2024년 일최저기온 0도 미만인 날 {below0_24}일",
               "겨울이 짧아졌다고 느끼지만, 영하로 내려가는 날은 여전히 석 달 "
               "치가 넘습니다."),
    ],
    caveat="ERA5는 넓은 격자의 재분석 값이라 도심 관측소와 다르게 나옵니다. "
           f"예컨대 여기서는 역대 최고기온이 {hottest['hi']:.1f}도"
           f"({hottest['d']:%Y년})지만 기상청 서울 관측 기록은 2018년 39.6도입니다. "
           "같은 '서울 기온'도 출처에 따라 다릅니다.")

# ── 2. 비 ────────────────────────────────────────────────────────
rain_days = [sum(1 for x in W if x["d"].year == y and x["rain"] >= 1.0)
             for y in range(1995, 2025)]
rain_avg = mean(rain_days)
by_month = {m: mean([sum(x["rain"] for x in W
                         if x["d"].year == y and x["d"].month == m)
                     for y in range(1995, 2025)]) for m in range(1, 13)}
wettest_m = max(by_month, key=by_month.get)
max_day = max(W, key=lambda x: x["rain"])
ann = {y: sum(x["rain"] for x in W if x["d"].year == y) for y in range(1995, 2025)}
norm = mean(ann.values())
r2024 = ann[2024]


def days_to_half(y):
    v = sorted((x["rain"] for x in W if x["d"].year == y), reverse=True)
    half, s = sum(v) / 2, 0
    for i, r in enumerate(v, 1):
        s += r
        if s >= half:
            return i
    return len(v)


half_days = round(mean(days_to_half(y) for y in range(1995, 2025)))

topic(
    "seoul-rain", "☔", "비는 언제, 얼마나",
    "서울의 하루치 강수량 30년",
    "Open-Meteo Archive (ERA5 재분석)", "https://open-meteo.com/",
    [
        slider("r1", "서울에서 1년 중 비가 온 날(1mm 이상)은 며칠쯤일까요?",
               rain_avg, "일", 40, 160, 5,
               f"1995~2024년 평균 {rain_avg:.1f}일 "
               f"(최소 {min(rain_days)}일 · 최대 {max(rain_days)}일)",
               "비 오는 날은 기억에 오래 남아서 실제보다 많게 느껴집니다."),
        choice("r2", "1년 강수량이 가장 많은 달은?",
               ["6월", "7월", "8월", "9월"], f"{wettest_m}월",
               " · ".join(f"{m}월 {by_month[m]:.0f}mm" for m in (6, 7, 8, 9)),
               "장마가 낀 달과 비가 가장 많이 오는 달이 같지 않을 수 있습니다."),
        slider("r3", "하루에 내린 비의 최대 기록은?",
               max_day["rain"], "mm", 100, 500, 10,
               f"{max_day['d']:%Y년 %m월 %d일} {max_day['rain']:.1f}mm",
               f"가장 많이 온 날 하루에, 비가 가장 많은 {wettest_m}월 한 달 평균"
               f"({by_month[wettest_m]:.0f}mm)의 {max_day['rain'] / by_month[wettest_m] * 100:.0f}%가 "
               "쏟아졌습니다. 비는 고르게 오지 않습니다."),
        slider("r4", "1년 강수량의 절반은 '비 온 날' 며칠 만에 채워질까요?",
               half_days, "일", 3, 40, 1,
               f"많이 온 날부터 더해서 연 강수량의 절반에 도달하는 데 "
               f"평균 {half_days}일 (1995~2024년)",
               "비는 고르게 오지 않습니다. 열흘 남짓이 1년의 절반을 만듭니다."),
        slider("r5", "2024년 서울 강수량은 30년 평균의 몇 %였을까요?",
               r2024 / norm * 100, "%", 50, 180, 5,
               f"2024년 {r2024:.0f}mm · 1995~2024년 평균 {norm:.0f}mm",
               "'올해 비가 많았다'는 체감과 실제 총량은 자주 어긋납니다."),
    ],
    caveat="ERA5 재분석 값입니다. 기상청 관측 강수량과 조금씩 다릅니다.")

# ── 3. 미세먼지 ──────────────────────────────────────────────────
a = load_json("seoul_air.json")["hourly"]
A = [{"t": dt.datetime.fromisoformat(t), "pm25": p25, "pm10": p10}
     for t, p25, p10 in zip(a["time"], a["pm2_5"], a["pm10"])]
A24 = [x for x in A if x["t"].year == 2024]
pm_month = {m: mean([x["pm25"] for x in A if x["t"].month == m])
            for m in range(1, 13)}
worst_m = max(pm_month, key=pm_month.get)
pm_hour = {h: mean([x["pm25"] for x in A if x["t"].hour == h])
           for h in range(24)}
worst_h = max(pm_hour, key=pm_hour.get)
pm24_avg = mean(x["pm25"] for x in A24)


def daily_avg(year):
    d = {}
    for x in A:
        if x["t"].year == year:
            d.setdefault(x["t"].date(), []).append(x["pm25"])
    return {k: mean(v) for k, v in d.items()}


d24 = daily_avg(2024)
d23 = daily_avg(2023)
bad_ratio = sum(1 for v in d24.values() if v > 35) / len(d24) * 100
worse_year = "2023년" if mean(d23.values()) > mean(d24.values()) else "2024년"

topic(
    "seoul-air", "😷", "미세먼지, 언제 나쁜가",
    "서울의 시간별 초미세먼지 2년치",
    "Open-Meteo Air Quality (CAMS)", "https://open-meteo.com/",
    [
        choice("a1", "초미세먼지(PM2.5)가 가장 나쁜 달은?",
               ["1월", "3월", "5월", "11월"],
               f"{worst_m}월" if f"{worst_m}월" in ["1월", "3월", "5월", "11월"]
               else "3월",
               " · ".join(f"{m}월 {pm_month[m]:.1f}" for m in (1, 3, 5, 11))
               + f" (최악은 {worst_m}월 {pm_month[worst_m]:.1f})",
               "황사 뉴스가 많은 달과 실제 수치가 높은 달은 다를 수 있습니다."),
        slider("a2", "2024년 서울의 초미세먼지 연평균 농도는?",
               pm24_avg, "㎍/㎥", 5, 60, 1,
               f"2024년 시간값 {len(A24):,}개의 평균 {pm24_avg:.1f}㎍/㎥ "
               f"(WHO 권고 연평균 5, 국내 기준 15)",
               "'나쁨'인 날이 인상에 남아서 연평균을 높게 잡게 됩니다."),
        choice("a3", "하루 중 초미세먼지가 가장 높은 시간대는?",
               ["새벽 4시", "오전 10시", "오후 3시", "밤 9시"],
               {4: "새벽 4시", 10: "오전 10시", 15: "오후 3시",
                21: "밤 9시"}.get(worst_h, "오전 10시"),
               " · ".join(f"{h}시 {pm_hour[h]:.1f}" for h in (4, 10, 15, 21))
               + f" (최고는 {worst_h}시)",
               "출퇴근 시간이 가장 나쁠 것 같지만 대기가 정체되는 시간은 따로 "
               "있습니다."),
        slider("a4", "2024년 하루 평균이 '나쁨'(35㎍/㎥ 초과)이었던 날의 비율은?",
               bad_ratio, "%", 0, 60, 1,
               f"2024년 {len(d24)}일 중 "
               f"{sum(1 for v in d24.values() if v > 35)}일",
               "마스크를 챙긴 날은 강하게 기억되지만, 1년 전체로 보면 비율이 "
               "다릅니다."),
        choice("a5", "2023년과 2024년 중 초미세먼지가 더 나빴던 해는?",
               ["2023년", "2024년"], worse_year,
               f"연평균 2023년 {mean(d23.values()):.1f} · "
               f"2024년 {mean(d24.values()):.1f}㎍/㎥",
               "체감은 최근 쪽으로 쏠립니다. 두 해 차이는 생각보다 작습니다."),
    ],
    caveat="CAMS 대기질 모델의 추정값입니다. 에어코리아 측정소 실측값과 "
           "다를 수 있습니다.")

# ── 4. 노동시간 ──────────────────────────────────────────────────
wh = [r for r in load_csv("working_hours.csv") if r["working_hours_omm"]]
latest_year = max(int(r["year"]) for r in wh if r["code"] == "KOR")
cur = owid_countries(wh, latest_year, "working_hours_omm")
kor = cur["South Korea"]
rank = sorted(cur.items(), key=lambda kv: -kv[1])
kor_rank = [k for k, _ in rank].index("South Korea") + 1
longer = kor_rank - 1
past = {int(r["year"]): float(r["working_hours_omm"])
        for r in wh if r["code"] == "KOR"}
y30 = latest_year - 30
drop = past[y30] - kor
trio = {c: cur[c] for c in ("South Korea", "Japan", "Germany") if c in cur}
shortest = min(trio, key=trio.get)
name_ko = {"South Korea": "한국", "Japan": "일본", "Germany": "독일"}

topic(
    "working-hours", "⏰", "우리는 얼마나 일하나",
    "나라별 1인당 연간 노동시간",
    "Our World in Data", "https://ourworldindata.org/grapher/annual-working-hours-per-worker",
    [
        slider("w1", f"{latest_year}년 한국의 1인당 연간 노동시간은?",
               kor, "시간", 1200, 2600, 50,
               f"{latest_year}년 한국 {kor:,.0f}시간 "
               f"(주 5일 기준 하루 약 {kor / 250:.1f}시간)",
               "연 단위로 물으면 감이 잘 안 옵니다. 하루로 나눠 보면 체감과 "
               "맞는지 확인할 수 있습니다."),
        slider("w2", f"한국보다 더 오래 일하는 나라는 {len(cur)}개국 중 몇 개일까요?",
               longer, "개국", 0, 60, 1,
               f"{latest_year}년 기준 {len(cur)}개국 중 한국은 {kor_rank}위",
               "'한국이 제일 많이 일한다'는 인상이 강하지만, 비교 대상에 따라 "
               "순위가 달라집니다."),
        slider("w3", f"한국의 노동시간은 30년 전({y30}년)보다 몇 시간 줄었을까요?",
               drop, "시간", 0, 900, 50,
               f"{y30}년 {past[y30]:,.0f}시간 → {latest_year}년 {kor:,.0f}시간",
               "줄어든 폭은 대개 과소평가됩니다. 하루로 치면 크게 달라진 값입니다."),
        choice("w4", "한국·일본·독일 중 가장 적게 일하는 나라는?",
               ["한국", "일본", "독일"], name_ko[shortest],
               " · ".join(f"{name_ko[c]} {trio[c]:,.0f}시간" for c in trio),
               "나라마다 통계 기준이 다릅니다. 순위보다 격차의 크기를 봅니다."),
        slider("w5", "한국과 독일의 연간 노동시간 차이는?",
               abs(cur["South Korea"] - cur["Germany"]), "시간", 0, 900, 50,
               f"한국 {cur['South Korea']:,.0f} · 독일 "
               f"{cur['Germany']:,.0f}시간",
               "두 나라 차이를 주 단위로 나눠 보면 체감이 확 달라집니다."),
    ],
    caveat="이 자료에는 개발도상국이 많이 포함돼 있어, OECD 안에서의 순위와 "
           "전체 순위가 다릅니다. '어느 집단과 비교하느냐'가 순위를 만듭니다.")

# ── 5. 인구와 출산 ───────────────────────────────────────────────
fert = [r for r in load_json("wb_fertility.json")[1] if r["value"] is not None]
f_latest = max(int(r["date"]) for r in fert if r["countryiso3code"] == "KOR")
f_cur = wb_countries(fert, f_latest)
kor_f = f_cur["Korea, Rep."]
f_rank = sorted(f_cur.items(), key=lambda kv: kv[1])
kor_f_rank = [k for k, _ in f_rank].index("Korea, Rep.") + 1
kor_1970 = next(r["value"] for r in fert
                if r["countryiso3code"] == "KOR" and r["date"] == "1970")

pop = load_json("wb_pop_kor.json")[1]
tot = {int(r["date"]): r["value"] for r in pop
       if r["indicator"]["id"] == "SP.POP.TOTL" and r["value"]}
old = {int(r["date"]): r["value"] for r in pop
       if r["indicator"]["id"] == "SP.POP.65UP.TO.ZS" and r["value"]}
urb = {int(r["date"]): r["value"] for r in pop
       if r["indicator"]["id"] == "SP.URB.TOTL.IN.ZS" and r["value"]}
peak_year = max(tot, key=tot.get)
old_latest = max(old)
urb_latest = max(urb)

topic(
    "population", "👶", "인구와 출산",
    "전 세계 출산율과 한국의 인구 구조",
    "World Bank Open Data", "https://data.worldbank.org/",
    [
        slider("p1", f"{f_latest}년 한국의 합계출산율(여성 1명당 출생아 수)은?",
               kor_f, "명", 0.5, 3.0, 0.05,
               f"{f_latest}년 한국 {kor_f:.2f}명",
               "숫자는 자주 들었어도 '여성 1명당'이라는 단위는 잘 안 와닿습니다."),
        slider("p2", f"출산율이 낮은 순으로 세면 한국은 {len(f_cur)}개 나라·지역 중 몇 위일까요?",
               kor_f_rank, "위", 1, 30, 1,
               f"{f_latest}년 기준 {len(f_cur)}곳 중 {kor_f_rank}위 "
               f"(1위 {f_rank[0][0]} {f_rank[0][1]:.2f})",
               "'꼴찌'라고 알고 있지만, 비교 목록에 어떤 나라·지역이 들어가느냐에 "
               "따라 순위가 달라집니다."),
        slider("p3", "1970년 한국의 출산율은 몇 명이었을까요?",
               kor_1970, "명", 1.0, 7.0, 0.1,
               f"1970년 {kor_1970:.2f}명 → {f_latest}년 {kor_f:.2f}명",
               "한 세대 만에 일어난 변화의 크기는 대개 과소평가됩니다."),
        slider("p4", f"{old_latest}년 한국에서 65세 이상 인구의 비율은?",
               old[old_latest], "%", 5, 35, 1,
               f"{old_latest}년 {old[old_latest]:.1f}% "
               f"(World Bank 최신 공표치. 최근 연도는 추계값입니다)",
               "고령화는 '다가올 일'로 느껴지지만 이미 지나온 숫자입니다."),
        slider("p5", f"{urb_latest}년 한국에서 도시에 사는 사람의 비율은?",
               urb[urb_latest], "%", 50, 100, 1,
               f"{urb_latest}년 {urb[urb_latest]:.1f}% "
               f"(World Bank 최신 공표치. 최근 연도는 추계값입니다)",
               "'절반쯤'이라고 답하기 쉽지만 한국은 세계에서도 손꼽히는 도시 국가입니다."),
    ])

# ── 6. 인터넷 ────────────────────────────────────────────────────
net = [r for r in load_json("wb_internet.json")[1] if r["value"] is not None]
n_latest = max(int(r["date"]) for r in net if r["countryiso3code"] == "KOR")
n_cur = wb_countries(net, n_latest)
kor_n = n_cur["Korea, Rep."]
n_rank = sorted(n_cur.items(), key=lambda kv: -kv[1])
kor_n_rank = [k for k, _ in n_rank].index("Korea, Rep.") + 1
kor_2000 = next(r["value"] for r in net
                if r["countryiso3code"] == "KOR" and r["date"] == "2000")
world = next((r["value"] for r in net
              if r["countryiso3code"] == "WLD" and int(r["date"]) == n_latest), None)
if world is None:
    world = mean(n_cur.values())
offline = 100 - world

topic(
    "internet", "🌐", "인터넷과 연결",
    "나라별 인터넷 사용 인구 비율",
    "World Bank Open Data", "https://data.worldbank.org/",
    [
        slider("i1", f"{n_latest}년 한국에서 인터넷을 쓰는 사람의 비율은?",
               kor_n, "%", 60, 100, 1,
               f"{n_latest}년 한국 {kor_n:.1f}%",
               "'거의 다'라고 생각하지만 100%는 아닙니다. 남은 몇 %가 누구인지가 "
               "정책의 대상입니다."),
        slider("i2", f"한국보다 인터넷 사용률이 높은 나라는 {len(n_cur)}곳 중 몇 곳일까요?",
               kor_n_rank - 1, "곳", 0, 60, 1,
               f"{n_latest}년 {len(n_cur)}곳 중 한국 {kor_n_rank}위",
               "'IT 강국'이라는 말과 실제 보급률 순위는 다른 이야기입니다."),
        slider("i3", "2000년 한국의 인터넷 사용률은?",
               kor_2000, "%", 0, 100, 5,
               f"2000년 {kor_2000:.1f}% → {n_latest}년 {kor_n:.1f}%",
               "20여 년 전을 떠올릴 때는 지금의 기준으로 과대평가하기 쉽습니다."),
        slider("i4", f"{n_latest}년 전 세계 평균 인터넷 사용률은?",
               world, "%", 20, 100, 5,
               f"{n_latest}년 세계 {world:.1f}%",
               "내 주변이 전부 연결돼 있으면 세계도 그럴 거라고 생각하게 됩니다."),
        slider("i5", "전 세계에서 인터넷을 쓰지 않는 사람은 몇 %일까요?",
               offline, "%", 0, 70, 5,
               f"100 - {world:.1f} = {offline:.1f}%",
               "같은 사실을 뒤집어 물으면 전혀 다른 크기로 느껴집니다."),
    ])

# ── 7. 환율 ──────────────────────────────────────────────────────
fx = load_json("usdkrw.json")["rates"]
FX = sorted((d, v["KRW"]) for d, v in fx.items() if "KRW" in v)
hi_d, hi_v = max(FX, key=lambda x: x[1])
lo_d, lo_v = min(FX, key=lambda x: x[1])
avg_fx = mean(v for _, v in FX)
under1200 = sum(1 for _, v in FX if v < 1200) / len(FX) * 100
last_d, last_v = FX[-1]
era = ("1997~1999년 외환위기" if hi_d < "2005" else
       "2008~2009년 금융위기" if hi_d < "2012" else
       "2020년 코로나" if hi_d < "2022" else "2022년 이후")

topic(
    "usdkrw", "💵", "원달러 환율 26년",
    "1999년부터 오늘까지의 원달러 환율",
    "Frankfurter (유럽중앙은행 고시)", "https://frankfurter.dev/",
    [
        slider("x1", "1999년 이후 원달러 환율의 최고 기록은?",
               hi_v, "원", 1100, 1800, 25,
               f"{hi_d} {hi_v:,.2f}원",
               "최고치는 위기의 기억과 함께 남지만, 정확한 시점은 자주 헷갈립니다."),
        choice("x2", "그 최고치를 찍은 시기는?",
               ["1997~1999년 외환위기", "2008~2009년 금융위기",
                "2020년 코로나", "2022년 이후"], era,
               f"최고 {hi_v:,.2f}원 ({hi_d}). 이 데이터는 1999년부터라 "
               f"1997~98년 외환위기 구간은 포함되어 있지 않습니다",
               "데이터가 시작하는 시점을 확인하지 않으면 '역대 최고'를 잘못 "
               "말하게 됩니다."),
        slider("x3", "26년간의 평균 환율은?",
               avg_fx, "원", 900, 1500, 25,
               f"{FX[0][0]} ~ {FX[-1][0]} 거래일 {len(FX):,}일 평균 "
               f"{avg_fx:,.0f}원 (최저 {lo_v:,.0f}원 · {lo_d})",
               "최근 값이 기준점이 되어 과거 평균을 높게 잡게 됩니다."),
        slider("x4", "환율이 1,200원 아래였던 날은 전체의 몇 %일까요?",
               under1200, "%", 0, 100, 5,
               f"거래일 {len(FX):,}일 중 1,200원 미만은 "
               f"{sum(1 for _, v in FX if v < 1200):,}일",
               "'원래 1,100원대였는데'라는 감각이 맞는지 비율로 확인해 봅니다."),
        slider("x5", f"{last_d} 기준 환율은?",
               last_v, "원", 1100, 1800, 25,
               f"{last_d} {last_v:,.2f}원",
               "가장 최근 값조차 기억은 며칠 전 뉴스에 묶여 있습니다."),
    ])

# ── 8. 도시 비교 ─────────────────────────────────────────────────
CITY = {}
for cid in ("seoul", "tokyo", "london", "singapore"):
    _d = load_json(f"city_{cid}.json")["daily"]
    CITY[cid] = [{"d": dt.date.fromisoformat(t), "hi": a, "lo": b, "r": c}
                 for t, a, b, c in zip(_d["time"], _d["temperature_2m_max"],
                                       _d["temperature_2m_min"],
                                       _d["precipitation_sum"])]
CN = {"seoul": "서울", "tokyo": "도쿄", "london": "런던", "singapore": "싱가포르"}
YRS = 30
c_rain = {k: sum(x["r"] for x in v) / YRS for k, v in CITY.items()}
c_days = {k: sum(1 for x in v if x["r"] >= 1) / YRS for k, v in CITY.items()}
c_temp = {k: mean((x["hi"] + x["lo"]) / 2 for x in v) for k, v in CITY.items()}
c_hot = {k: max(x["hi"] for x in v) for k, v in CITY.items()}
c_jan = {k: mean(x["lo"] for x in v if x["d"].month == 1) for k, v in CITY.items()}
dry = min(c_rain, key=c_rain.get)
hottest = max(c_hot, key=c_hot.get)
colder = "서울" if c_jan["seoul"] < c_jan["tokyo"] else "도쿄"

topic(
    "cities", "🏙️", "서울·도쿄·런던·싱가포르",
    "네 도시의 30년 평년값 (1991~2020)",
    "Open-Meteo Archive (ERA5 재분석)", "https://open-meteo.com/",
    [
        choice("c1", "1년 강수량이 가장 적은 도시는?",
               ["서울", "도쿄", "런던", "싱가포르"], CN[dry],
               " · ".join(f"{CN[k]} {c_rain[k]:,.0f}mm" for k in CITY),
               "비가 자주 오는 것과 많이 오는 것은 다릅니다. 런던은 자주 오지만 "
               "적게 옵니다."),
        slider("c2", "런던에서 1년에 비가 오는 날(1mm 이상)은 며칠일까요?",
               c_days["london"], "일", 40, 220, 5,
               f"런던 {c_days['london']:.0f}일 · 서울 {c_days['seoul']:.0f}일 "
               f"(강수량은 런던 {c_rain['london']:,.0f}mm · "
               f"서울 {c_rain['seoul']:,.0f}mm)",
               "런던은 서울보다 비 오는 날이 많은데 총량은 절반입니다. "
               "'며칠 왔나'와 '얼마나 왔나'는 다른 질문입니다."),
        choice("c3", "30년간 가장 높은 기온을 기록한 도시는?",
               ["서울", "도쿄", "런던", "싱가포르"], CN[hottest],
               " · ".join(f"{CN[k]} {c_hot[k]:.1f}도" for k in CITY),
               "적도에 가까울수록 덥다고 생각하지만, 최고기온 기록은 중위도 "
               "도시에서 나옵니다."),
        slider("c4", "서울의 연평균 기온은?",
               c_temp["seoul"], "도", 5, 30, 0.5,
               " · ".join(f"{CN[k]} {c_temp[k]:.1f}도" for k in CITY),
               "여름과 겨울의 기억이 강해서 연평균은 감이 잘 안 옵니다."),
        choice("c5", "1월 밤이 더 추운 도시는?",
               ["서울", "도쿄"], colder,
               f"1월 평균 최저기온 서울 {c_jan['seoul']:.1f}도 · "
               f"도쿄 {c_jan['tokyo']:.1f}도",
               "위도가 비슷해도 대륙의 영향을 받는 도시가 훨씬 춥습니다."),
    ],
    caveat="ERA5 재분석 값이고, 도시별로 관측소 위치와 기준이 다릅니다.")

# ── 9. 지진 ──────────────────────────────────────────────────────
Q = [f["properties"] for f in load_json("quakes.geojson")["features"]]
QK = [f["properties"] for f in load_json("quakes_korea.geojson")["features"]]
n_years = 25
per_year = len(Q) / n_years
m7 = sum(1 for p in Q if p["mag"] >= 7.0) / n_years
biggest = max(Q, key=lambda p: p["mag"])


def region(place):
    return (place or "").split(",")[-1].strip()


top_region = Counter(region(p["place"]) for p in Q).most_common(1)[0]
region_ko = {"Japan": "일본", "Indonesia": "인도네시아", "Chile": "칠레",
             "Philippines": "필리핀"}
kr_band = ("50건 미만" if len(QK) < 50 else "50~150건" if len(QK) <= 150
           else "150~400건" if len(QK) <= 400 else "400건 이상")

topic(
    "quakes", "🌎", "지진은 얼마나 자주",
    "2000~2024년 전 세계 지진 기록",
    "USGS 지진 카탈로그", "https://earthquake.usgs.gov/",
    [
        slider("q1", "규모 5.5 이상 지진은 전 세계에서 1년에 몇 번쯤 일어날까요?",
               per_year, "회", 50, 1200, 50,
               f"2000~2024년 {len(Q):,}건 ÷ {n_years}년 = "
               f"연평균 {per_year:.0f}회",
               "뉴스에 나오는 지진만 세면 실제보다 훨씬 적게 잡게 됩니다."),
        slider("q2", "규모 7.0 이상은 1년에 몇 번쯤일까요?",
               m7, "회", 0, 40, 1,
               f"2000~2024년 규모 7.0 이상 "
               f"{sum(1 for p in Q if p['mag'] >= 7.0)}건, 연평균 {m7:.1f}회",
               "규모는 로그 척도입니다. 0.5만 올라가도 횟수가 크게 줄어듭니다."),
        choice("q3", "규모 5.5 이상 지진이 가장 자주 기록된 나라는?",
               ["일본", "인도네시아", "칠레", "필리핀"],
               region_ko.get(top_region[0], "인도네시아"),
               f"1위 {top_region[0]} {top_region[1]:,}건",
               "지진 하면 떠오르는 나라와 실제로 가장 많이 기록되는 곳은 "
               "다를 수 있습니다."),
        slider("q4", "2000년 이후 기록된 가장 큰 지진의 규모는?",
               biggest["mag"], "", 7.0, 9.9, 0.1,
               f"규모 {biggest['mag']} · "
               f"{dt.datetime.fromtimestamp(biggest['time'] / 1000, dt.UTC):%Y-%m-%d} "
               f"{biggest['place']}",
               "규모 9는 8보다 약 32배 큰 에너지입니다."),
        choice("q5", "한반도 주변(위도 33~39.5도)에서 25년간 기록된 규모 3.0 이상 지진은?",
               ["50건 미만", "50~150건", "150~400건", "400건 이상"], kr_band,
               f"USGS 기준 {len(QK)}건. 이 관측망은 작은 지진을 모두 잡지는 "
               f"못하므로 기상청 집계와 다릅니다",
               "'어느 기관이 센 숫자인가'를 확인하지 않으면 같은 현상도 다른 "
               "값이 됩니다."),
    ],
    caveat="USGS 카탈로그는 작은 지진을 모두 담지 못합니다. 한반도 지진은 "
           "기상청 집계가 더 많습니다.")

# ── 10. 수명 ─────────────────────────────────────────────────────
le = [r for r in load_csv("life_expectancy.csv") if r["life_expectancy_0"]]
l_latest = max(int(r["year"]) for r in le if r["code"] == "KOR")
l_cur = owid_countries(le, l_latest, "life_expectancy_0")
kor_l = l_cur["South Korea"]
l_rank = sorted(l_cur.items(), key=lambda kv: -kv[1])
kor_l_rank = [k for k, _ in l_rank].index("South Korea") + 1
_kle = {r["year"]: float(r["life_expectancy_0"]) for r in le if r["code"] == "KOR"}
kor_1950, kor_1953, kor_1955 = _kle["1950"], _kle["1953"], _kle["1955"]
wld = next(float(r["life_expectancy_0"]) for r in le
           if r["code"] == "OWID_WRL" and int(r["year"]) == l_latest)
trio_l = {c: l_cur[c] for c in ("South Korea", "Japan", "United States")
          if c in l_cur}
top_l = max(trio_l, key=trio_l.get)
name_l = {"South Korea": "한국", "Japan": "일본", "United States": "미국"}

topic(
    "life", "🩺", "얼마나 오래 사나",
    "나라별 기대수명 1950~현재",
    "Our World in Data", "https://ourworldindata.org/grapher/life-expectancy",
    [
        slider("l1", f"{l_latest}년 한국의 기대수명은?",
               kor_l, "세", 60, 95, 1,
               f"{l_latest}년 한국 {kor_l:.1f}세",
               "기대수명은 '지금 태어난 아이가 평균적으로 살 햇수'입니다. "
               "지금 살아 있는 사람의 예상 수명과는 다릅니다."),
        slider("l2", f"기대수명이 긴 순으로 세면 한국은 {len(l_cur)}곳 중 몇 위일까요?",
               kor_l_rank, "위", 1, 60, 1,
               f"{l_latest}년 {len(l_cur)}곳 중 {kor_l_rank}위 "
               f"(1위 {l_rank[0][0]} {l_rank[0][1]:.1f}세)",
               "상위권인 것은 맞지만 몇 위인지는 대개 크게 빗나갑니다."),
        slider("l3", "1950년 한국의 기대수명은?",
               kor_1950, "세", 20, 70, 1,
               f"1950년 {kor_1950:.1f}세. 한국전쟁 기간의 사망이 반영된 값이라 "
               f"1953년 {kor_1953:.1f}세, 1955년 {kor_1955:.1f}세로 빠르게 "
               f"회복합니다. {l_latest}년은 {kor_l:.1f}세",
               "숫자가 튀면 사건을 의심해야 합니다. 이 값은 오류가 아니라 "
               "전쟁의 기록입니다."),
        choice("l4", "한국·일본·미국 중 기대수명이 가장 긴 나라는?",
               ["한국", "일본", "미국"], name_l[top_l],
               " · ".join(f"{name_l[c]} {trio_l[c]:.1f}세" for c in trio_l),
               "가장 잘사는 나라가 가장 오래 사는 나라는 아닙니다."),
        slider("l5", f"{l_latest}년 세계 평균 기대수명은?",
               wld, "세", 50, 90, 1,
               f"{l_latest}년 세계 평균 {wld:.1f}세 · 한국 {kor_l:.1f}세",
               "내가 사는 곳의 수준을 세계 평균으로 착각하기 쉽습니다."),
    ])

# ── 11. 미스 유니버스 ────────────────────────────────────────────
# 위키백과는 문서 전체를 HTML 로 준다. 표는 직접 읽는다.
# 우승자 표에는 위아래로 합친 칸(rowspan)이 있다 — 2008·2009년 베네수엘라는 나라 칸
# 하나를 두 해가 같이 쓰고, 2002년은 연도 칸 하나에 두 줄이 걸린다. 합친 칸을 펼치지
# 않으면 사람 이름이 나라 자리에 들어가 '나라'로 세어진다.
class _Cells(HTMLParser):
    """문서 첫 번째 표의 칸을 (글자, rowspan) 으로 모은다. 각주(<sup>)는 뺀다."""

    def __init__(self):
        super().__init__()
        self.depth, self.n, self.skip = 0, -1, 0
        self.rows, self.cell = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            self.n += self.depth == 1
        if self.n != 0 or self.depth != 1:
            return
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell = ["", int(dict(attrs).get("rowspan") or 1)]
        elif tag == "sup":
            self.skip += 1

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
        elif self.n == 0 and self.depth == 1:
            if tag in ("td", "th") and self.cell:
                self.rows[-1].append((" ".join(self.cell[0].split()), self.cell[1]))
                self.cell = None
            elif tag == "sup":
                self.skip -= 1

    def handle_data(self, s):
        if self.cell is not None and not self.skip:
            self.cell[0] += s


def _grid(rows):
    """합친 칸을 아래 줄에도 채워 넣어, 모든 줄의 칸 위치를 맞춘다."""
    out, carry = [], {}
    for raw in rows:
        row, cells, c = [], iter(raw), 0
        while True:
            if c in carry:
                v, left = carry.pop(c)
                if left > 1:
                    carry[c] = (v, left - 1)
            else:
                nxt = next(cells, None)
                if nxt is None:
                    break
                v, span = nxt
                if span > 1:
                    carry[c] = (v, span - 1)
            row.append(v)
            c += 1
        out.append(row)
    return out


class _Nav(HTMLParser):
    """안내 상자의 머리(th)와 항목(li)을 나온 순서대로 모은다."""

    def __init__(self):
        super().__init__()
        self.items, self.cur = [], None

    def handle_starttag(self, tag, attrs):
        if tag in ("th", "li"):
            self.cur = [tag, ""]

    def handle_endtag(self, tag):
        if tag in ("th", "li") and self.cur:
            self.items.append((self.cur[0], " ".join(self.cur[1].split())))
            self.cur = None

    def handle_data(self, s):
        if self.cur is not None:
            self.cur[1] += s


def leaders(cnt):
    """가장 많은 것을 모두 돌려준다. 공동 1위면 여럿이고, 모두 정답이다."""
    top = max(cnt.values())
    return [k for k, v in cnt.items() if v == top]


mu = load_json("miss_universe.json")["parse"]["text"]["*"]
_c = _Cells()
_c.feed(mu)
_g = _grid(_c.rows)
assert _g[0][:3] == ["Year", "Country/Territory", "Titleholder"], "우승자 표 모양이 바뀌었습니다"
MU = [{"y": int(r[0]), "c": r[1], "who": r[2]} for r in _g[1:]]
mu_y0, mu_y1 = MU[0]["y"], MU[-1]["y"]
# 한 해에 우승자가 둘인 해(박탈 후 승계)는 표에 오른 두 사람을 모두 센다
mu_twice = [y for y, n in Counter(x["y"] for x in MU).items() if n > 1]

mu_cnt = Counter(x["c"] for x in MU)
mu_win = leaders(mu_cnt)

# 대륙은 위키백과 표에 없다. Our World in Data 구분을 붙인다 (북·남아메리카를 나눈다)
CONT = {r["entity"]: r["owid_region"] for r in load_csv("owid_continents.csv")}
_nocont = {x["c"] for x in MU} - CONT.keys()
assert not _nocont, f"대륙을 모르는 나라가 있습니다: {_nocont}"
CONT_KO = {"Asia": "아시아", "Europe": "유럽", "Africa": "아프리카",
           "North America": "북아메리카", "South America": "남아메리카",
           "Oceania": "오세아니아"}
cont_cnt = Counter(CONT[x["c"]] for x in MU)
cont_win = leaders(cont_cnt)

R0, R1 = 2016, 2024
recent = [x for x in MU if R0 <= x["y"] <= R1]
rec_cnt = Counter(x["c"] for x in recent)
rec_win = leaders(rec_cnt)

# 출전국은 문서 아래 안내 상자에만 있다. 대륙별 목록만 센다 — 지금은 빠진 나라는
# 넣고, 옛 나라·옛 자치령 묶음과 나라가 아닌 출전 단위(Latina)는 뺀다
_i = mu.index("participant countries and territories")
_s = mu.rfind("<table", 0, _i)
_depth = 0
for _m in re.finditer(r"<(/?)table\b", mu[_s:]):
    _depth += -1 if _m.group(1) else 1
    if _depth == 0:
        break
_n = _Nav()
_n.feed(mu[_s:_s + _m.end()])
NAV_CONT = {"Africa", "Americas", "Asia", "Europe", "Oceania"}
NOT_COUNTRY = {"Latina"}
_grp, entrants, nav_listed = None, set(), set()
for _tag, _txt in _n.items:
    if _tag == "th":
        _grp = _txt
    elif _grp is not None:                          # 머리 앞의 v·t·e 링크는 항목이 아니다
        _name = re.sub(r"\s*\([^)]*\)$", "", _txt)      # 'Benin (1962)' → 'Benin'
        nav_listed.add(_name)
        if _grp in NAV_CONT and _name not in NOT_COUNTRY:
            entrants.add(_name)
nav_old = len(nav_listed) - len(entrants) - len(NOT_COUNTRY & nav_listed)

kor_wins = mu_cnt.get("South Korea", 0)
kor_in = "South Korea" in entrants

KO = {"United States": "미국", "Venezuela": "베네수엘라", "Puerto Rico": "푸에르토리코",
      "Philippines": "필리핀", "Mexico": "멕시코", "Sweden": "스웨덴", "India": "인도",
      "Colombia": "콜롬비아", "Japan": "일본", "Finland": "핀란드", "France": "프랑스",
      "Peru": "페루", "Germany": "독일", "Argentina": "아르헨티나", "Brazil": "브라질",
      "Thailand": "태국", "Lebanon": "레바논", "Israel": "이스라엘",
      "Australia": "오스트레일리아", "Greece": "그리스", "Spain": "스페인",
      "Norway": "노르웨이", "Netherlands": "네덜란드", "Denmark": "덴마크",
      "Canada": "캐나다", "Chile": "칠레", "New Zealand": "뉴질랜드",
      "Namibia": "나미비아", "Botswana": "보츠와나", "Angola": "앙골라",
      "Trinidad and Tobago": "트리니다드 토바고", "Panama": "파나마",
      "Dominican Republic": "도미니카 공화국", "Russia": "러시아",
      "South Africa": "남아프리카 공화국", "Nicaragua": "니카라과",
      "South Korea": "대한민국"}


def ko(c):
    return KO.get(c, c)


def top_opts(cnt, win, k=4):
    """보기 = 정답(공동 포함) + 횟수 순으로 다음 나라들. 순서는 가나다로 섞는다."""
    pick = list(win) + [c for c, _ in cnt.most_common() if c not in win]
    return sorted(ko(c) for c in pick[:max(k, len(win))])


def tally(cnt, n=4, name=ko):
    return " · ".join(f"{name(c)} {v}회" for c, v in cnt.most_common(n))


_twice = f"({'·'.join(map(str, mu_twice))}년 박탈·승계 두 사람 모두 포함)" if mu_twice else ""
KOR_OPTS = ["한 번도 없다", "1번", "2번", "3번 이상"]

vs("miss-universe", "👑 미스 유니버스", f"{mu_y0}~{mu_y1}년 우승을 더 많이 한 나라는?",
   *[(ko(c), mu_cnt[c], f"{mu_cnt[c]}회") for c in ("United States", "Venezuela")])
vs("miss-universe", "👑 미스 유니버스", f"{mu_y0}~{mu_y1}년 우승을 더 많이 한 대륙은?",
   *[(CONT_KO[c], cont_cnt[c], f"{cont_cnt[c]}회") for c in ("Asia", "Europe")])

topic(
    "miss-universe", "👑", "미녀가 가장 많은 나라는 어디일까?",
    f"{mu_y0}년부터 {mu_y1}년까지, 미스 유니버스 우승자와 출전국",
    "Wikipedia (List of Miss Universe titleholders)",
    "https://en.wikipedia.org/wiki/List_of_Miss_Universe_titleholders",
    [
        choice("m1", "미스유니버스에서 가장 많이 우승한 나라는 어디일까요?",
               top_opts(mu_cnt, mu_win), [ko(c) for c in mu_win],
               f"{mu_y0}~{mu_y1}년 우승자 {len(MU)}명{_twice}의 국적을 나라별로 세면 "
               f"{tally(mu_cnt)}",
               "미인대회 하면 떠오르는 나라와 실제 우승 횟수 1위는 다를 수 있습니다. "
               "우승은 오래, 꾸준히 출전한 나라에 쌓입니다."),
        choice("m2", "미스유니버스에서 가장 많이 우승한 대륙은 어디일까요?",
               [CONT_KO[k] for k in CONT_KO], [CONT_KO[c] for c in cont_win],
               f"{mu_y0}~{mu_y1}년 우승자 {len(MU)}명의 국적을 Our World in Data "
               f"대륙 구분(북·남아메리카를 나눔)으로 묶어 세면 "
               f"{tally(cont_cnt, 6, CONT_KO.get)}",
               "대륙을 어떻게 나누느냐에 따라 1위가 바뀝니다. 아메리카를 하나로 볼지 "
               "남북으로 나눌지부터 정해야 셀 수 있습니다."),
        choice("m3", f"{R0}~{R1}년, 가장 많이 우승한 나라는 어디일까요?",
               top_opts(rec_cnt, rec_win), [ko(c) for c in rec_win],
               f"{R0}~{R1}년 우승자 {len(recent)}명의 국적을 나라별로 세면 "
               f"{tally(rec_cnt, max(4, len(rec_win)))}"
               + (" (공동 1위는 모두 정답)" if len(rec_win) > 1 else ""),
               "전체 기간의 강자가 최근에도 강자인지는 기간을 잘라 세어 봐야 압니다."),
        slider("m4", "미스유니버스에는 지금까지 몇 개 나라·지역이 출전했을까요?",
               len(entrants), "곳", 50, 250, 5,
               f"위키백과 출전국 안내 상자의 대륙별 목록(지금은 빠진 나라 포함, "
               f"옛 나라·옛 자치령과 나라가 아닌 'Latina' 제외)에 오른 나라·지역 "
               f"{len(entrants)}곳",
               "'몇 개국'이라고 물어도 실제 목록에는 자치령과 지역이 섞여 있습니다. "
               "무엇을 나라로 칠지 정해야 숫자가 나옵니다."),
        choice("m5", "대한민국은 몇 번이나 우승했을까요?",
               KOR_OPTS, KOR_OPTS[min(kor_wins, 3)],
               f"{mu_y0}~{mu_y1}년 우승자 {len(MU)}명 중 대한민국 국적은 {kor_wins}명"
               + (" (출전국 목록에는 올라 있음)" if kor_in else ""),
               "꾸준히 출전한다고 우승이 따라오지는 않습니다. 출전 기록과 우승 "
               "기록은 다른 표입니다."),
    ],
    caveat="위키백과 문서는 누구나 고칠 수 있어, 받은 시점에 따라 내용이 달라질 수 "
           "있습니다. 우승 횟수는 '미인이 많은 나라'가 아니라 오래, 꾸준히 출전한 "
           "나라를 보여 줍니다. 대륙은 Our World in Data의 구분을 따릅니다. "
           # 다른 자료와 숫자가 갈리는 지점 — 모두 앞의 점검에서 실제로 갈린 것이다
           f"{'·'.join(map(str, mu_twice))}년 박탈된 우승자와 승계자를 둘 다 세어 "
           f"대회는 {len(set(x['y'] for x in MU))}회인데 우승자는 {len(MU)}명이고, "
           "대륙은 아메리카를 북·남으로 나눴으며, 출전 나라·지역 "
           f"{len(entrants)}곳은 위키백과 목록 {len(nav_listed)}곳에서 옛 나라·옛 자치령 "
           f"{nav_old}곳과 나라가 아닌 'Latina'를 뺀 값이라, 한 명만 세거나 아메리카를 "
           "하나로 묶거나 목록을 모두 센 자료와는 숫자가 다릅니다.")

# ── 12. 돈 ───────────────────────────────────────────────────────
# PIP 는 '하루 X달러(2021년 PPP) 아래에 사는 사람의 비율'을 준다. 소득선 62개에서
# 받은 비율을 이으면 세계 소득 분포 곡선이 되고, 그 곡선 위에서
# '300만 원은 아래에서 몇 %' 와 '50% 가 되는 소득선 = 중위값' 을 함께 읽는다.
PIP = [r for r in load_json("pip_world.json") if r["estimate_type"] == "actual"]
pip_y = max(r["reporting_year"] for r in PIP)
CURVE = sorted((r["poverty_line"], r["headcount"] * 100) for r in PIP
               if r["reporting_year"] == pip_y)


def _pairs():
    """곡선의 이웃한 두 점. 소득선은 12%씩 벌어지므로 로그 눈금 위에서 잇는다."""
    for (x0, y0), (x1, y1) in zip(CURVE, CURVE[1:]):
        yield log(x0), y0, log(x1), y1


def below_pct(usd_day):
    """하루 usd_day 달러보다 적게 버는 사람의 비율(%)."""
    for lx0, y0, lx1, y1 in _pairs():
        if lx0 <= log(usd_day) <= lx1:
            return y0 + (log(usd_day) - lx0) / (lx1 - lx0) * (y1 - y0)
    raise ValueError(f"하루 {usd_day:.2f}달러가 받은 곡선 범위 밖입니다 — 소득선을 넓히세요")


def line_at(pct):
    """사람의 pct% 가 그보다 적게 버는 소득선(하루 달러)."""
    for lx0, y0, lx1, y1 in _pairs():
        if y0 <= pct <= y1:
            return exp(lx0 + (pct - y0) / (y1 - y0) * (lx1 - lx0))
    raise ValueError(f"{pct}% 가 받은 곡선 범위 밖입니다")


_mk = load_json("wb_money_kor.json")[1]


def kor_series(ind):
    return {int(r["date"]): r["value"] for r in _mk
            if r["indicator"]["id"] == ind and r["value"] is not None}


ppp = kor_series("PA.NUS.PRVT.PP")      # 2021년 소비 PPP — PIP 의 달러와 같은 기준
cpi = kor_series("FP.CPI.TOTL")

# 원 ↔ PIP 달러. 그 해 원화를 2021년 물가로 되돌린 뒤 2021년 PPP 로 나눈다
def won_to_pip(won, year):
    return won * cpi[2021] / cpi[year] / ppp[2021]


def pip_to_won(usd, year):
    return usd * ppp[2021] * cpi[year] / cpi[2021]


SALARY = 3_000_000
sal_day = won_to_pip(SALARY * 12 / 365, pip_y)
sal_pct = below_pct(sal_day)
med_day = line_at(50)
med_month = pip_to_won(med_day * 365 / 12, pip_y) / 10_000          # 만 원

# '내 월급 계산기'에 싣는다 → extras/salary.json. 계산은 폰에서 하고 월급은 어디로도 보내지 않는다.
# 입력은 '올해' 월급이라 가장 최근 물가로 2021년 값으로 되돌린다 (문항 1번은 PIP 해의 물가)
cpi_y = max(cpi)
CALC = {"year": pip_y, "cpi_year": cpi_y,
        "k": 12 / 365 * cpi[2021] / cpi[cpi_y] / ppp[2021],   # 원/월 × k = PIP 달러/일
        "curve": [[x, round(y, 3)] for x, y in CURVE],          # [하루 달러, 그 아래 사람 %]
        "median_day": round(med_day, 3),
        "median_month_won": round(pip_to_won(med_day * 365 / 12, cpi_y))}


# 자영업자 소득은 KOSIS(인증키 필요)에서만 받을 수 있다. 키가 없어 원본이 없으면
# 지난번에 계산해 둔 문항을 그대로 쓴다 — 키 없는 사람도 나머지는 다시 계산할 수 있게
if (RAW / "kosis_household_income.json").exists():
    HH = load_json("kosis_household_income.json")
    hh_y = max(int(r["PRD_DE"]) for r in HH)      # 조사 연도. 소득은 그 전해 것이다

    def hh_month(status, item):
        """가구주 종사상지위별 경상소득(연, 만 원) → 한 달."""
        return next(float(r["DT"]) for r in HH if int(r["PRD_DE"]) == hh_y
                    and r["C2_NM"] == status and r["ITM_ID"] == item) / 12

    se_avg = hh_month("자영업자", "T01")
    se_med = hh_month("자영업자", "T03")
    wage_avg = hh_month("상용근로자", "T01")
    q_selfemp = slider(
        "n2", f"{hh_y - 1}년 한국 자영업자 가구의 한 달 평균 소득은? (가구원 소득 모두 포함)",
        se_avg, "만 원", 100, 1500, 10,
        f"{hh_y}년 가계금융복지조사: 가구주가 자영업자인 가구의 {hh_y - 1}년 경상소득 "
        f"평균 연 {se_avg * 12:,.0f}만 원 ÷ 12 = {se_avg:,.0f}만 원 "
        f"(중앙값 {se_med:,.0f}만 원 · 상용근로자 가구 평균 {wage_avg:,.0f}만 원)",
        "'자영업은 힘들다'는 말만 들으면 낮게 짐작하기 쉽습니다. 다만 가구원 모두의 "
        "소득을 합친 값이고, 잘 버는 일부가 평균을 끌어올려 중앙값은 그보다 낮습니다.")
else:
    print("  ※ KOSIS 원본이 없어 '돈' 2번 문항은 지난번 계산값을 그대로 씁니다")
    q_selfemp = next(q for q in json.load(io.open(OUT / "money.json", encoding="utf-8"))
                     ["questions"] if q["id"] == "n2")

# 인구 5천만 이상 나라의 1인당 국민소득 (Atlas). 한국이 들어 있는 해를 쓴다
_gni = load_json("wb_gni.json")[1]
_pop = load_json("wb_pop_all.json")[1]
big_y = max(int(r["date"]) for r in _gni
            if r["countryiso3code"] == "KOR" and r["value"] is not None)
_p = {r["countryiso3code"]: r["value"] for r in _pop
      if int(r["date"]) == big_y and r["countryiso3code"] in REAL and r["value"]}
BIG = {r["countryiso3code"]: r["value"] for r in _gni
       if int(r["date"]) == big_y and r["countryiso3code"] in REAL and r["value"]
       and _p.get(r["countryiso3code"], 0) >= 50_000_000}
big_rank = sorted(BIG, key=BIG.get, reverse=True)
BIG_KO = {"USA": "미국", "DEU": "독일", "GBR": "영국", "FRA": "프랑스", "JPN": "일본",
          "KOR": "한국", "ITA": "이탈리아", "RUS": "러시아", "CHN": "중국", "TUR": "튀르키예",
          "MEX": "멕시코", "BRA": "브라질", "THA": "태국", "ZAF": "남아프리카공화국",
          "IRN": "이란", "COL": "콜롬비아", "COD": "콩고민주공화국", "SDN": "수단",
          "ETH": "에티오피아", "UGA": "우간다", "TZA": "탄자니아", "MMR": "미얀마",
          "NGA": "나이지리아"}
# 상위 3개국에 일본을 더한다(이미 들어 있으면 4위까지). 순서로 정답이 드러나지 않게 가나다순
big_opts = big_rank[:3] + (["JPN"] if "JPN" not in big_rank[:3] else big_rank[3:4])
big_hi, big_lo = big_rank[0], big_rank[-1]
for c in big_opts + [big_lo]:
    assert c in BIG_KO, f"{c}: BIG_KO 에 한국어 이름을 넣으세요"
big_ratio = BIG[big_hi] / BIG[big_lo]
big_top5 = " · ".join(f"{BIG_KO.get(c, c)} {BIG[c]:,.0f}달러" for c in big_rank[:5])
jpn_big = big_rank.index("JPN") + 1
kor_big = big_rank.index("KOR") + 1


def usd(v):
    return f"{v:,.0f}달러"


vs("money", "💰 돈", f"{big_y}년 1인당 국민소득이 더 높은 나라는?",
   ("한국", BIG["KOR"], usd(BIG["KOR"])), ("일본", BIG["JPN"], usd(BIG["JPN"])))
vs("money", "💰 돈", f"{big_y}년 1인당 국민소득, 더 큰 쪽은?",
   ("미국 1명", BIG["USA"], usd(BIG["USA"])),
   ("독일 1명 + 영국 1명", BIG["DEU"] + BIG["GBR"], usd(BIG["DEU"] + BIG["GBR"])))

topic(
    "money", "💰", "돈, 다른 사람들은 얼마나 벌까",
    "내 월급의 세계 순위부터 나라별 국민소득까지",
    "World Bank (PIP · Open Data) · 통계청 가계금융복지조사", "https://pip.worldbank.org/",
    [
        slider("n1", "월급 300만 원. 전 세계 사람 중 상위 몇 %에 해당할까요? "
                     "(나보다 많이 버는 사람의 비율)",
               100 - sal_pct, "%", 0, 50, 1,
               f"{pip_y}년 300만 원은 2021년 물가·구매력으로 하루 {sal_day:.1f}달러. "
               f"PIP 세계 분포에서 이보다 많이 버는 사람은 {100 - sal_pct:.1f}%, "
               f"적게 버는 사람은 {sal_pct:.1f}%",
               "한국 안에서 보는 월급과 세계 속의 월급은 전혀 다른 자리에 있습니다. "
               "비교 대상을 바꾸면 같은 돈의 순위가 바뀝니다."),
        q_selfemp,
        slider("n3", "인구 5천만 명이 넘는 나라 중 1인당 국민소득이 가장 높은 나라는 "
                     "가장 낮은 나라의 몇 배일까요?",
               big_ratio, "배", 5, 200, 1,
               f"{big_y}년 1인당 GNI(Atlas) {BIG_KO[big_hi]} {BIG[big_hi]:,.0f}달러 ÷ "
               f"{BIG_KO[big_lo]} {BIG[big_lo]:,.0f}달러 = {big_ratio:.1f}배 "
               f"(인구 5천만 이상 {len(BIG)}개국 중)",
               "'몇 배'는 감이 잘 안 와서 10배, 20배쯤으로 줄여 답하기 쉽습니다. "
               "달러로 바꾼 값이라 물가 차이는 빠져 있어 체감 격차는 이보다 작습니다."),
        slider("n4", f"{pip_y}년 전 세계 사람을 소득순으로 세우면, 한가운데 사람의 "
                     "한 달 소득은 한국 물가로 얼마일까요?",
               med_month, "만 원", 0, 300, 5,
               f"세계 중위값 하루 {med_day:.2f}달러(2021년 PPP) → 한국 물가로 "
               f"한 달 약 {med_month:,.0f}만 원",
               "'평균'은 부자가 끌어올리지만 '한가운데'는 그렇지 않습니다. "
               "세계의 한가운데는 생각보다 훨씬 아래에 있습니다."),
        choice("n5", "인구 5천만 명이 넘는 나라 중 1인당 국민소득이 가장 높은 나라는?",
               sorted(BIG_KO[c] for c in big_opts), BIG_KO[big_rank[0]],
               f"{big_y}년 인구 5천만 이상 {len(BIG)}개국의 1인당 GNI(Atlas): "
               f"{big_top5} … 일본 {jpn_big}위 · 한국 {kor_big}위",
               "룩셈부르크·스위스 같은 작은 부자 나라를 빼면 순위가 달라집니다. "
               "사람이 많으면서 부유한 나라는 드뭅니다."),
    ],
    caveat="PIP는 가구의 1인당 소득 또는 소비(나라마다 다름)를 2021년 물가·구매력(PPP)으로 "
           "맞춘 값이라, 세전 월급과 바로 같지 않습니다. 300만 원은 혼자 쓰는 한 달 "
           "소득으로 보고 한국 소비자물가와 PPP로 바꿨습니다. 자영업자 소득은 가구주가 "
           "자영업자인 가구의 근로·사업·재산·이전 소득을 모두 합친 값이라, 자영업자 한 사람이 "
           "버는 돈과 다릅니다. 나라별 1인당 국민소득은 달러로 바꾼 값이라 물가 차이가 빠져 "
           "있습니다.")


# ── 원본이 없을 수 있는 주제 공통 ────────────────────────────────
# KOSIS 원본은 인증키가 있어야 받아지고, 로또는 비공식 경로라 언제든 막힐 수 있다.
# 원본이 없으면 지난번에 계산해 둔 주제를 그대로 다시 싣는다 — 그래도 나머지 주제는
# 다시 계산할 수 있게
def topic_or_kept(tid, files, build):
    if all((RAW / f).exists() for f in files):
        build()
        return
    print(f"  ※ 원본({', '.join(files)})이 없어 '{tid}' 주제는 지난번 계산값을 그대로 씁니다")
    TOPICS.append(json.load(io.open(OUT / f"{tid}.json", encoding="utf-8")))
    KEPT.add(tid)                 # 이 주제의 '더 많을까' 문제도 지난번 것을 쓴다


def kosis_series(name, **match):
    """KOSIS 응답에서 조건(C2="K01119" 처럼)에 맞는 줄만 골라 {연도: 값}."""
    return {int(r["PRD_DE"]): float(r["DT"]) for r in load_json(name)
            if all(r.get(k) == v for k, v in match.items()) and r["DT"] not in ("-", "")}


# ── 13. 물가 ─────────────────────────────────────────────────────
# 물가지수는 같은 물건 값이 어떻게 변했는지를 2020년=100 으로 나타낸다. 두 해의 지수를
# 나누면 그사이 값이 몇 배가 됐는지가 된다. 실제 가격(원)은 이 표에 없다
CPI_CODE = {"총지수": "0", "짜장면": "K01119", "라면": "A01110", "커피(외식)": "K01133",
            "시내버스 요금": "G03201", "택시 요금": "G03203", "TV": "I01101",
            "소주": "B01101", "쌀": "A01101"}


def build_prices():
    P = {k: kosis_series("kosis_cpi.json", C2=c) for k, c in CPI_CODE.items()}
    y1 = max(P["총지수"])
    y0 = y1 - 30

    def times(k):
        return P[k][y1] / P[k][y0]

    def up(k, years):                        # years 년 전보다 몇 % 올랐나
        return (P[k][y1] / P[k][y1 - years] - 1) * 100

    y10 = y1 - 10

    def hint10(*ks):
        return f"힌트: {y10}년보다 " + " · ".join(f"{k} {up(k, 10):+.0f}%" for k in ks)

    four = ["짜장면", "라면", "커피(외식)", "시내버스 요금"]
    top = max(four, key=times)
    fall_opts = ["TV", "쌀", "소주", "택시 요금"]
    fell = [k for k in fall_opts if times(k) < 1]
    assert fell, "30년 전보다 값이 내린 보기가 하나도 없습니다 — 보기를 바꾸세요"
    rice = (P["쌀"][y1] / P["쌀"][y1 - 1] - 1) * 100
    total1 = (P["총지수"][y1] / P["총지수"][y1 - 1] - 1) * 100

    for a, b in (("짜장면", "시내버스 요금"), ("라면", "커피(외식)"), ("짜장면", "총지수"),
                 ("쌀", "소주"), ("택시 요금", "라면")):
        vs("prices", "🍜 물가", f"{y0}→{y1}년, 값이 더 많이 오른 것은?",
           *[(k.replace("총지수", "물가 전체"), up(k, 30), f"{up(k, 30):+.0f}%") for k in (a, b)])

    topic(
        "prices", "🍜", "짜장면 값은 얼마나 올랐나",
        f"{y0}년부터 {y1}년까지, 30년 동안 오른 것과 내린 것",
        "통계청 소비자물가조사 (KOSIS)",
        "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1J22001",
        [
            slider("pr1", f"{y1}년 짜장면 값은 {y0}년보다 몇 % 올랐을까요?",
                   up("짜장면", 30), "%", 0, 1000, 10,
                   f"짜장면 물가지수 {y0}년 {P['짜장면'][y0]:.1f} → {y1}년 "
                   f"{P['짜장면'][y1]:.1f} (2020년=100) = {up('짜장면', 30):+.0f}% "
                   f"({times('짜장면'):.1f}배)",
                   "해마다 조금씩 오르면 체감은 덜하지만, 30년을 이어 붙이면 "
                   "크게 달라집니다.",
                   hint=hint10("짜장면")),
            choice("pr2", "짜장면·라면·커피(외식)·시내버스 요금 중 30년 동안 값이 가장 "
                          "많이 오른 것은?",
                   four, top,
                   f"{y0}→{y1}년 " + " · ".join(f"{k} {up(k, 30):+.0f}%" for k in four),
                   "자주 사 먹는 것의 값은 기억에 남지만, 매일 내는 교통비는 오른 줄 "
                   "모르고 지나가기 쉽습니다.",
                   hint=hint10(*four)),
            slider("pr3", "같은 30년 동안 물가 전체(소비자물가지수)는 몇 % 올랐을까요?",
                   up("총지수", 30), "%", 0, 500, 5,
                   f"총지수 {y0}년 {P['총지수'][y0]:.1f} → {y1}년 {P['총지수'][y1]:.1f} "
                   f"= {up('총지수', 30):+.0f}% (짜장면 {up('짜장면', 30):+.0f}%)",
                   "눈에 띄는 품목은 많이 오른 것처럼 느껴지지만, 전체 물가에는 덜 오르거나 "
                   "내린 것도 섞여 있습니다.",
                   hint=hint10("총지수").replace("총지수", "물가 전체")),
            choice("pr4", "30년 전보다 오히려 값이 내린 것은?",
                   fall_opts, fell,
                   f"{y0}→{y1}년 " + " · ".join(f"{k} {times(k):.2f}배" for k in fall_opts),
                   "같은 값에 화면이 커지고 성능이 좋아진 몫이 반영돼 있습니다. 물가지수는 "
                   "품질이 좋아진 만큼을 값이 내린 것으로 셉니다."),
            slider("pr5", f"{y1}년 쌀값은 1년 전보다 몇 % 올랐을까요?",
                   rice, "%", -10, 30, 1,
                   f"쌀 물가지수 {y1 - 1}년 → {y1}년 {rice:+.1f}% "
                   f"(같은 해 물가 전체 {total1:+.1f}%)",
                   "쌀값은 그해 작황에 따라 크게 출렁입니다. 한 해 값만 보고 추세를 "
                   "말하기는 어렵습니다.",
                   hint=hint10("쌀")),
        ],
        caveat="물가지수는 가격(원)이 아니라 같은 품목 값의 변화를 2020년=100으로 나타낸 "
               "값입니다. 품질이 좋아진 만큼은 값이 내린 것으로 보정하므로, TV처럼 성능이 "
               "빠르게 좋아지는 물건은 매장 가격과 다르게 움직입니다. 전국 기준입니다.")


topic_or_kept("prices", ["kosis_cpi.json"], build_prices)


# ── 14. 결혼과 이혼 ──────────────────────────────────────────────
def build_marriage():
    wed = kosis_series("kosis_vital.json", C2="20")
    div = kosis_series("kosis_vital.json", C2="30")
    crude = kosis_series("kosis_vital.json", C2="31")      # 조이혼율: 인구 1천 명당 이혼
    groom = kosis_series("kosis_marriage_age.json", ITM_ID="T1")
    bride = kosis_series("kosis_marriage_age.json", ITM_ID="T2")
    y = max(wed)
    peak_y = max(wed, key=wed.get)
    div_peak = max(div, key=div.get)
    decades = ["1990년대", "2000년대", "2010년대", "2020년대"]
    div_dec = f"{div_peak // 10 * 10}년대"
    assert div_dec in decades, f"이혼 정점 {div_peak}년이 보기에 없습니다"
    g0 = min(groom)

    # 동거기간별 이혼. '20년 이상'은 그 뒤를 모두 합친 칸이라 다른 칸과 크기를 겨루지 않고
    # 전체에서 차지하는 비율로만 묻는다. 코드 10 = 1년 미만, 40 = 20년 이상
    def dur_share(year, code):
        d = kosis_series("kosis_divorce_duration.json", C2=code)
        return d[year] / kosis_series("kosis_divorce_duration.json", C2="00")[year] * 100

    d_y = max(kosis_series("kosis_divorce_duration.json", C2="00"))
    d_old = d_y - 30
    new_now, new_old = dur_share(d_y, "10"), dur_share(d_old, "10")
    late_now, late_old = dur_share(d_y, "40"), dur_share(d_old, "40")
    per100 = div[y] / wed[y] * 100

    vs("marriage", "💍 결혼", "평균 초혼 나이가 더 많은 쪽은?",
       (f"{y}년 여자", bride[y], f"{bride[y]:.1f}세"), (f"{g0}년 남자", groom[g0], f"{groom[g0]:.1f}세"))
    vs("marriage", "💍 이혼", "이혼이 더 많았던 해는?",
       (f"{d_old}년", div[d_old], f"{div[d_old]:,.0f}건"), (f"{y}년", div[y], f"{div[y]:,.0f}건"))

    topic(
        "marriage", "💍", "결혼과 이혼",
        "언제 결혼하고, 얼마나 결혼하고, 언제 헤어지나",
        "통계청 인구동향조사 (KOSIS)",
        "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1B8000G",
        [
            slider("mr1", f"{y}년 한국 남자의 평균 초혼 나이는?",
                   groom[y], "세", 25, 40, 1,
                   f"{y}년 남자 {groom[y]:.1f}세 · 여자 {bride[y]:.1f}세 "
                   f"({g0}년 남자 {groom[g0]:.1f}세 · 여자 {bride[g0]:.1f}세)",
                   f"주변 결혼식만 떠올리면 젊게 짐작하기 쉽습니다. {y - g0}년 사이 "
                   f"{groom[y] - groom[g0]:.1f}살 늦어졌습니다."),
            # '이혼율'은 흔히 그해 결혼 대비 이혼으로 말한다. 통계청 공식 지표(조이혼율)는
            # 인구 1천 명당 이혼이라 정수로 고를 수 없어 근거에 함께 적는다
            slider("mr2", f"{y}년 이혼율은? (그해 결혼 100건당 이혼 건수)",
                   per100, "%", 0, 100, 1,
                   f"{y}년 결혼 {wed[y]:,.0f}건 · 이혼 {div[y]:,.0f}건 → 100건당 "
                   f"{per100:.1f}건. 통계청 공식 이혼율(조이혼율)은 인구 1천 명당 "
                   f"{crude[y]:.1f}건",
                   "'세 쌍 중 한 쌍이 이혼한다'는 말이 이 숫자에서 나옵니다. 하지만 그해 "
                   "이혼한 부부는 대부분 다른 해에 결혼한 부부라, 결혼한 부부가 이혼할 "
                   "확률과는 다릅니다."),
            choice("mr3", f"{min(div)}년 이후 이혼 건수가 가장 많았던 때는?",
                   decades, div_dec,
                   f"가장 많았던 해 {div_peak}년 {div[div_peak]:,.0f}건 · "
                   f"{y}년 {div[y]:,.0f}건",
                   "'요즘 이혼이 많다'는 인상과 실제 정점은 다를 수 있습니다. 이혼은 "
                   "결혼 건수를 따라 움직입니다."),
            slider("mr4", "이혼 부부 중 1년 이내 이혼하는 비율은?",
                   new_now, "%", 0, 30, 1,
                   f"{d_y}년 이혼 {kosis_series('kosis_divorce_duration.json', C2='00')[d_y]:,.0f}건 "
                   f"중 함께 산 지 1년 미만 {new_now:.1f}% ({d_old}년 {new_old:.1f}%)",
                   "이혼이라면 신혼부부를 먼저 떠올리기 쉽습니다. 실제 비율은 함께 산 "
                   "기간별로 세어 봐야 압니다."),
            slider("mr5", f"{d_y}년 이혼한 부부 중 함께 산 지 20년이 넘은 부부의 비율은?",
                   late_now, "%", 0, 60, 1,
                   f"{d_y}년 {late_now:.1f}% ({d_old}년 {late_old:.1f}%)",
                   "흔히 '황혼이혼'이라 부르는 경우입니다. 30년 사이 비율이 크게 "
                   "달라졌습니다." if abs(late_now - late_old) >= 10 else
                   "흔히 '황혼이혼'이라 부르는 경우입니다."),
        ],
        caveat="이혼은 신고한 해를 기준으로 셉니다. 동거기간은 결혼 신고일이 아니라 실제로 "
               "함께 살기 시작한 때부터 잽니다. 초혼 나이는 그해 처음 결혼한 사람의 "
               "평균입니다.")


topic_or_kept("marriage", ["kosis_vital.json", "kosis_marriage_age.json",
                         "kosis_divorce_duration.json"], build_marriage)


# ── 15. 한국인의 하루 ────────────────────────────────────────────
# 생활시간조사는 5년마다 10세 이상이 시간 일지를 쓴 것을 모은다. 값은 "8:04" 같은 시:분
def build_daily():
    def tu(kind, day, sex, act):
        out = {}
        for r in load_json(f"kosis_timeuse_{kind}.json"):
            if (r["C1"], r["C2"], r["C4"]) == (day, sex, act) and ":" in r["DT"]:
                h, m = r["DT"].split(":")
                out[int(r["PRD_DE"])] = int(h) * 60 + int(m)
        return out

    def hm(m):
        return f"{m // 60}시간 {m % 60}분"

    def avg(sex, act):                     # 10세 이상 모두의 요일 평균
        return tu("all", "A0", sex, act)

    commute = tu("doer", "A1", "S0", "S921")
    back = tu("doer", "A1", "S0", "S922")
    y = max(commute)
    sleep = avg("S0", "S11")
    y0 = min(sleep)
    w, m = avg("S2", "S4"), avg("S1", "S4")
    gap, gap0 = w[y] - m[y], w[y0] - m[y0]
    acts = {"수면": "S11", "일": "S2", "가사(가정관리)": "S4", "문화·여가": "S8"}
    change = {k: avg("S0", c)[y] - avg("S0", c)[y0] for k, c in acts.items()}
    media = avg("S0", "S82")

    vs("daily", "🕐 하루", f"{y}년 하루에 더 오래 하는 것은?",
       ("미디어로 쉬는 시간", media[y], f"{media[y]}분"),
       ("여자의 집안일 시간", w[y], f"{w[y]}분"))
    vs("daily", "🕐 하루", f"{y}년 하루에 더 오래 걸리는 것은?",
       ("평일 출근길 (편도)", commute[y], f"{commute[y]}분"),
       ("남자의 집안일 시간", m[y], f"{m[y]}분"))
    vs("daily", "🕐 하루", f"{y0}→{y}년, 하루에 더 많이 늘어난 시간은?",
       ("수면", change["수면"], f"{change['수면']:+d}분"),
       ("문화·여가", change["문화·여가"], f"{change['문화·여가']:+d}분"))

    topic(
        "daily", "🕐", "한국인의 하루는 어떻게 흘러가나",
        f"출근·잠·집안일·여가, {y0}년부터 {y}년까지의 하루",
        "통계청 생활시간조사 (KOSIS)",
        "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1TS202003",
        [
            slider("dl1", f"{y}년 평일, 출근하는 사람은 출근길에 평균 몇 분을 쓸까요?",
                   commute[y], "분", 10, 90, 1,
                   f"{y}년 평일 출근 {commute[y]}분 · 퇴근 {back[y]}분 "
                   "(그날 출근한 사람만의 평균)",
                   "'출근에 한 시간'은 수도권의 이야기일 수 있습니다. 전국 평균은 모든 "
                   "지역을 섞은 값입니다."),
            slider("dl2", f"{y}년 한국인(10세 이상)의 하루 평균 수면 시간은 몇 시간일까요?",
                   sleep[y] / 60, "시간", 4, 12, 1,
                   f"{y}년 {hm(sleep[y])} ({y0}년 {hm(sleep[y0])})",
                   "수면 시간에는 밤잠과 낮잠이 모두 들어갑니다. 평일과 주말도 섞여 있어 "
                   "체감보다 길게 나옵니다."),
            slider("dl3", f"{y}년 여자는 남자보다 집안일(가정관리)에 하루 몇 분을 더 쓸까요?",
                   gap, "분", 0, 200, 5,
                   f"{y}년 여자 {w[y]}분 · 남자 {m[y]}분 = 차이 {gap}분 "
                   f"({y0}년 여자 {w[y0]}분 · 남자 {m[y0]}분, 차이 {gap0}분)",
                   ("차이는 줄고 있지만 여전히 큽니다." if gap < gap0 else
                    "차이는 줄지 않았습니다.") + " 10세 이상 모두의 평균이라 집안일을 "
                   "하지 않은 사람도 0분으로 들어 있습니다."),
            choice("dl4", f"{y0}년부터 {y}년까지 하루 중 가장 많이 늘어난 시간은?",
                   list(acts), max(change, key=change.get),
                   f"{y0}→{y}년 " + " · ".join(f"{k} {v:+d}분" for k, v in change.items()),
                   f"{y - y0}년 사이 하루 24시간의 배분이 바뀌었습니다. 무엇이 줄고 무엇이 "
                   "늘었는지는 세어 봐야 압니다."),
            slider("dl5", f"{y}년 한국인(10세 이상)이 미디어(방송·동영상·책·인터넷)로 "
                          "쉬는 시간은 하루 평균 몇 분일까요?",
                   media[y], "분", 0, 360, 5,
                   f"{y}년 {media[y]}분 ({hm(media[y])}) · {y0}년 {media[y0]}분",
                   "스마트폰을 보는 시간은 길게 느껴지지만, 이 숫자는 '쉬려고' 미디어를 "
                   "쓴 시간만 셉니다. 일하거나 이동하며 본 화면은 빠집니다."),
        ],
        caveat="생활시간조사는 5년마다 10세 이상이 이틀 동안 쓴 시간 일지를 모은 것입니다. "
               "하루 평균은 평일과 주말을 섞은 값이고, 출근 시간은 그날 출근한 사람만의 "
               "평균입니다. 한 번에 한 가지 '주된 행동'만 셉니다.")


topic_or_kept("daily", ["kosis_timeuse_all.json", "kosis_timeuse_doer.json"], build_daily)


# ── 16. 행복 ─────────────────────────────────────────────────────
# 점수는 '가장 좋은 삶을 10, 가장 나쁜 삶을 0 이라 할 때 지금 몇 점인가'에 스스로 답한 값
hp = [r for r in load_csv("happiness.csv") if r["cantril_ladder_score"]]
h_y = max(int(r["year"]) for r in hp if r["code"] == "KOR")
H = owid_countries(hp, h_y, "cantril_ladder_score")
h_rank = sorted(H, key=H.get, reverse=True)
kor_h = H["South Korea"]
kor_hr = rank_of(H, "South Korea")
wh = h_y + 1          # OWID 의 해는 3년 조사의 마지막 해, 보고서는 다음 해에 나온다
gd = [r for r in load_csv("gdp_pcap_ppp.csv") if r["ny_gdp_pcap_pp_kd"]]
gd_y = max(int(r["year"]) for r in gd if r["code"] == "KOR" and int(r["year"]) <= h_y)
GD = owid_countries(gd, gd_y, "ny_gdp_pcap_pp_kd")
both = [c for c in H if c in GD]
rich_rank = sorted(both, key=GD.get, reverse=True)
richest = rich_rank[0]
asia4 = ["South Korea", "Japan", "United States", "Taiwan"]
HAPPY_KO = {"Finland": "핀란드", "Iceland": "아이슬란드", "Denmark": "덴마크",
            "Costa Rica": "코스타리카", "Sweden": "스웨덴", "Norway": "노르웨이",
            "Netherlands": "네덜란드", "Israel": "이스라엘", "Mexico": "멕시코",
            "Switzerland": "스위스", "Luxembourg": "룩셈부르크", "Australia": "호주",
            "New Zealand": "뉴질랜드", "South Korea": "한국", "Japan": "일본",
            "United States": "미국", "Taiwan": "대만", "Afghanistan": "아프가니스탄",
            "Sierra Leone": "시에라리온", "Lebanon": "레바논", "Malawi": "말라위",
            "Singapore": "싱가포르", "Ireland": "아일랜드", "Qatar": "카타르",
            "United Arab Emirates": "아랍에미리트", "Macao": "마카오", "Bahrain": "바레인",
            "Kuwait": "쿠웨이트", "Saudi Arabia": "사우디아라비아", "Belgium": "벨기에",
            "Austria": "오스트리아", "Germany": "독일", "Hong Kong": "홍콩"}


def hko(c):
    return HAPPY_KO.get(c, c)


h_top4 = h_rank[:4]
for c in h_top4 + asia4 + rich_rank[:3]:
    assert c in HAPPY_KO, f"{c}: HAPPY_KO 에 한국어 이름을 넣으세요"

for a, b in (("South Korea", "Japan"), ("Taiwan", "United States"),
             ("Costa Rica", "Sweden"), ("Singapore", "Ireland")):
    vs("happiness", "😊 행복", "삶 만족도가 더 높은 나라는?",
       *[(hko(c), H[c], f"{H[c]:.2f}점 ({rank_of(H, c)}위)") for c in (a, b)])

topic(
    "happiness", "😊", "한국인은 행복할까",
    "나라별 삶 만족도와 소득",
    "World Happiness Report (Our World in Data)",
    "https://ourworldindata.org/grapher/happiness-cantril-ladder",
    [
        slider("hp1", f"세계행복보고서 {wh}에서 한국인의 삶 만족도는 0~10점 중 몇 점일까요?",
               kor_h, "점", 0, 10, 1,
               f"세계행복보고서 {wh}({h_y - 2}~{h_y}년 조사) 한국 {kor_h:.2f}점 · 1위 {hko(h_rank[0])} {H[h_rank[0]]:.2f}점 · "
               f"꼴찌 {hko(h_rank[-1])} {H[h_rank[-1]]:.2f}점",
               "이 점수는 '가장 좋은 삶을 10점이라 할 때 지금 몇 점인가'에 스스로 답한 "
               "값입니다. 행복을 재는 방법에 따라 숫자가 달라집니다."),
        slider("hp2", f"한국의 삶 만족도는 {len(H)}개국 중 몇 위일까요?",
               kor_hr, "위", 1, len(H), 1,
               f"세계행복보고서 {wh} {len(H)}개국 중 {kor_hr}위 (같은 점수는 같은 순위)",
               "경제 순위만큼 높을 거라고 짐작하기 쉽습니다."),
        choice("hp3", "삶 만족도가 가장 높은 나라는?",
               sorted(hko(c) for c in h_top4), hko(h_rank[0]),
               f"세계행복보고서 {wh} " + " · ".join(f"{rank_of(H, c)}위 {hko(c)} {H[c]:.2f}"
                                        for i, c in enumerate(h_rank[:5])),
               "부유한 나라 순서와 행복한 나라 순서는 같지 않습니다."),
        choice("hp4", "한국·일본·미국·대만 중 삶 만족도가 가장 높은 나라는?",
               [hko(c) for c in asia4], hko(max(asia4, key=H.get)),
               " · ".join(f"{hko(c)} {H[c]:.2f}점 ({rank_of(H, c)}위)" for c in asia4),
               "이웃 나라끼리도 차이가 큽니다. 소득이 비슷해도 만족도는 다릅니다."),
        slider("hp5", f"1인당 소득이 가장 높은 나라({hko(richest)})는 삶 만족도 "
                      f"{len(H)}개국 중 몇 위일까요?",
               rank_of(H, richest), "위", 1, len(H), 1,
               f"{gd_y}년 1인당 GDP(구매력 기준) 1~3위 "
               + " · ".join(f"{hko(c)} {GD[c]:,.0f}달러(만족도 {rank_of(H, c)}위)"
                            for c in rich_rank[:3]),
               "돈은 행복을 늘리지만, 가장 부유한 나라가 가장 행복한 나라는 아닙니다."
               if h_rank[0] != richest else "이번에는 가장 부유한 나라가 가장 행복한 "
               "나라이기도 합니다."),
    ],
    caveat="나라마다 해마다 약 1천 명에게 물은 갤럽 세계 조사를 3년치 묶은 평균이라, "
           "한 해의 사건보다 흐름을 보여 줍니다. 1인당 GDP는 물가 차이를 맞춘 구매력(PPP) "
           "기준입니다. 순위는 이 자료에 점수가 있는 나라끼리만 매긴 것입니다.")


# ── 17. 술 ───────────────────────────────────────────────────────
# WHO 추정치: 15세 이상 1명이 1년에 마시는 순수 알코올(L). 술 종류와 도수를 모두 맞춘 값
al = [r for r in load_csv("alcohol.csv") if r["sh_alc_pcap_li"]]
al_y = max(int(r["year"]) for r in al if r["code"] == "KOR")
AL = owid_countries(al, al_y, "sh_alc_pcap_li")
al_rank = sorted(AL, key=AL.get, reverse=True)
kor_al = AL["South Korea"]
_kal = {int(r["year"]): float(r["sh_alc_pcap_li"]) for r in al if r["code"] == "KOR"}
al_y0 = al_y - 20
al_drop = (1 - kor_al / _kal[al_y0]) * 100
# 1위와 함께 '술 하면 떠오르는' 나라를 보기로 둔다. 1위가 그중 하나면 다음 순위로 채운다
al_opts = [al_rank[0]] + [c for c in ("Russia", "Ireland", "South Korea") if c != al_rank[0]]
al_opts += [c for c in al_rank[1:] if c not in al_opts][:4 - len(al_opts)]
al4 = ["South Korea", "Japan", "China", "United States"]
ALC_KO = {"Romania": "루마니아", "Georgia": "조지아", "Latvia": "라트비아",
          "Lithuania": "리투아니아", "Czechia": "체코", "Germany": "독일", "Russia": "러시아",
          "Ireland": "아일랜드", "South Korea": "한국", "Japan": "일본", "China": "중국",
          "United States": "미국", "France": "프랑스", "Poland": "폴란드"}
for c in al_opts + al4 + al_rank[:3]:
    assert c in ALC_KO, f"{c}: ALC_KO 에 한국어 이름을 넣으세요"

for a, b in (("South Korea", "United States"), ("Russia", "Ireland"),
             ("Japan", "China"), ("Germany", "France")):
    vs("alcohol", "🍺 술", f"{al_y}년 1인당 술을 더 많이 마시는 나라는?",
       *[(ALC_KO[c], AL[c], f"{AL[c]:.1f}L") for c in (a, b)])

topic(
    "alcohol", "🍺", "누가 제일 많이 마시나",
    "나라별 1인당 알코올 소비량",
    "WHO (Our World in Data)",
    "https://ourworldindata.org/grapher/total-alcohol-consumption-per-capita-litres-of-pure-alcohol",
    [
        slider("al1", f"{al_y}년 한국의 15세 이상 1인당 연간 순수 알코올 소비량은 몇 L일까요?",
               kor_al, "L", 0, 20, 1,
               f"{al_y}년 한국 {kor_al:.2f}L · 세계 1위 {ALC_KO[al_rank[0]]} "
               f"{AL[al_rank[0]]:.1f}L",
               "소주·맥주가 아니라 그 안에 든 '순수 알코올'만 셉니다. 소주 한 병"
               f"(360mL·16도)에는 {360 * 0.16:.0f}mL가 들어 있어, {kor_al:.1f}L는 소주 약 "
               f"{kor_al * 1000 / (360 * 0.16):.0f}병입니다."),
        slider("al2", f"한국은 {len(AL)}개국 중 술을 몇 번째로 많이 마실까요?",
               rank_of(AL, "South Korea"), "위", 1, len(AL), 1,
               f"{al_y}년 {len(AL)}개국 중 {rank_of(AL, 'South Korea')}위",
               "'한국은 술을 많이 마신다'는 인상과 실제 순위는 다를 수 있습니다. "
               "회식 문화가 눈에 띄는 것과 총량은 다른 이야기입니다."),
        choice("al3", "1인당 알코올 소비량이 가장 많은 나라는?",
               sorted(ALC_KO[c] for c in al_opts), ALC_KO[al_rank[0]],
               f"{al_y}년 " + " · ".join(f"{i + 1}위 {ALC_KO.get(c, c)} {AL[c]:.1f}L"
                                        for i, c in enumerate(al_rank[:3]))
               + " … " + " · ".join(f"{ALC_KO[c]} {rank_of(AL, c)}위"
                                    for c in al_opts if c not in al_rank[:3]),
               "'술 하면 떠오르는 나라'와 실제 1위는 다릅니다. 상위권은 대개 "
               "동유럽 나라들입니다."),
        choice("al4", "한국·일본·중국·미국 중 1인당 알코올 소비량이 가장 많은 나라는?",
               [ALC_KO[c] for c in al4], ALC_KO[max(al4, key=AL.get)],
               " · ".join(f"{ALC_KO[c]} {AL[c]:.1f}L ({rank_of(AL, c)}위)" for c in al4),
               "독한 술을 마시는 나라가 더 많이 마실 것 같지만, 순수 알코올로 바꾸면 "
               "순서가 달라집니다."),
        slider("al5", f"{al_y}년 한국의 1인당 알코올 소비량은 {al_y0}년보다 몇 % 줄었을까요?",
               al_drop, "%", 0, 80, 1,
               f"{al_y0}년 {_kal[al_y0]:.2f}L → {al_y}년 {kor_al:.2f}L ({-al_drop:+.0f}%)",
               "회식이 줄고 술을 덜 권하는 분위기가 숫자에도 나타납니다. 다만 "
               f"{al_y}년은 코로나19로 술집 영업이 줄어든 해이기도 합니다."
               if al_y == 2020 else
               "회식이 줄고 술을 덜 권하는 분위기가 숫자에도 나타납니다."),
    ],
    caveat="WHO 추정치로, 판매 기록에 밀주·여행객 몫 같은 기록되지 않은 소비를 더한 "
           "값입니다. 15세 이상 인구로 나눈 평균이라 마시지 않는 사람도 들어 있습니다. "
           "나라마다 집계 방식이 달라 작은 차이로 순위를 가르기는 어렵습니다.")


# ── 18. 올림픽 ───────────────────────────────────────────────────
# 위키백과 문서에는 표가 여러 개다. 표마다 바로 위의 제목(h2·h3)을 같이 모아 이름으로 찾는다
class _Tables(HTMLParser):
    """문서의 최상위 표를 모두 모은다. {제목: [[칸 글자, ...], ...]} — 각주(<sup>)는 뺀다."""

    def __init__(self):
        super().__init__()
        self.depth, self.skip, self.head, self.inh = 0, 0, "", None
        self.tables, self.cell = [], None

    def handle_starttag(self, tag, attrs):
        if tag in ("h2", "h3", "h4") and self.depth == 0:
            self.inh = ""
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.tables.append((self.head, []))
        if self.depth != 1:
            return
        if tag == "tr":
            self.tables[-1][1].append([])
        elif tag in ("td", "th"):
            self.cell = ""
        elif tag in ("sup", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("h2", "h3", "h4") and self.inh is not None:
            self.head, self.inh = " ".join(self.inh.split()), None
        if tag == "table":
            self.depth -= 1
        elif self.depth == 1:
            if tag in ("td", "th") and self.cell is not None and self.tables[-1][1]:
                self.tables[-1][1][-1].append(" ".join(self.cell.split()))
                self.cell = None
            elif tag in ("sup", "style"):
                self.skip -= 1

    def handle_data(self, s):
        if self.inh is not None:
            self.inh += s
        if self.cell is not None and not self.skip:
            self.cell += s


def wiki_table(name, head):
    """제목이 head 로 시작하는 첫 표."""
    t = _Tables()
    t.feed(load_json(name)["parse"]["text"]["*"])
    for h, rows in t.tables:
        if h.startswith(head):
            return rows
    raise ValueError(f"{name}: '{head}' 표를 찾지 못했습니다 — 문서 모양이 바뀌었습니다")


def num(s):
    return int(s.replace(",", "") or 0)


SPORT_KO = {"Archery": "양궁", "Taekwondo": "태권도", "Judo": "유도", "Shooting": "사격",
            "Wrestling": "레슬링", "Fencing": "펜싱", "Badminton": "배드민턴",
            "Weightlifting": "역도", "Boxing": "복싱", "Table tennis": "탁구",
            "Short track speed skating": "쇼트트랙"}

_ss = wiki_table("olympics_korea.json", "Medals by summer sport")
_ws = wiki_table("olympics_korea.json", "Medals by winter sport")
assert _ss[0][:2] == ["Sport", "Gold"], "종목별 메달 표 모양이 바뀌었습니다"


def by_sport(rows):
    """종목 → 금메달. 합계 줄은 뺀다."""
    return {r[0]: num(r[1]) for r in rows[1:]
            if len(r) >= 5 and r[1].isdigit() and not r[0].startswith("Total")}


SUM_SPORT = by_sport(_ss)
WIN_SPORT = by_sport(_ws)
sum_gold = sum(SUM_SPORT.values())
win_gold = sum(WIN_SPORT.values())
ol_top = sorted(SUM_SPORT, key=SUM_SPORT.get, reverse=True)
for c in ol_top[:4]:
    assert c in SPORT_KO, f"{c}: SPORT_KO 에 한국어 이름을 넣으세요"
st = "Short track speed skating"
st_share = WIN_SPORT[st] / win_gold * 100

# 대회별 순위. '1912–1936 as part of Japan' 같은 줄과 합계 줄은 숫자 순위가 없어 빠진다
_sg = wiki_table("olympics_korea.json", "Medals by Summer Games")
GAMES = {r[0]: num(r[6]) for r in _sg[1:] if len(r) >= 7 and r[6].isdigit()}
best = min(GAMES, key=GAMES.get)
# 가장 최근 하계 대회 — 아직 열리지 않은 대회('future event')는 숫자 순위가 없어 빠진다
_last = [r for r in _sg[1:] if len(r) >= 7 and r[6].isdigit() and r[0][:4].isdigit()][-1]
last_name, last_gold = _last[0], num(_last[2])
CITY_KO = {"Paris": "파리", "Tokyo": "도쿄", "Los Angeles": "로스앤젤레스", "Brisbane": "브리즈번",
           "Rio de Janeiro": "리우데자네이루", "Seoul": "서울"}
last_city = last_name[5:]
assert last_city in CITY_KO, f"{last_city}: CITY_KO 에 한국어 이름을 넣으세요"
last_ko = f"{last_name[:4]} {CITY_KO[last_city]}"

# 역대 하계 메달표(금메달 순). 소련처럼 없어진 나라도 순위에 들어 있다
_at = wiki_table("olympics_alltime.json", "Summer Olympics (")
assert _at[0][:3] == ["Rank", "NOC", "Gold"], "역대 메달표 모양이 바뀌었습니다"
AT = {r[1].rstrip("*"): (r[0], num(r[2])) for r in _at[1:] if len(r) >= 6}
kor_at_rank, kor_at_gold = AT["South Korea"]
assert kor_at_gold == sum_gold, (f"두 문서의 한국 하계 금메달 수가 다릅니다: "
                                 f"종목별 {sum_gold} · 역대표 {kor_at_gold}")

_judo_tkd = SUM_SPORT["Taekwondo"] + SUM_SPORT["Judo"]
vs("olympics", "🏅 올림픽", "한국 하계올림픽 금메달, 더 많은 쪽은?",
   ("양궁", SUM_SPORT["Archery"], f"{SUM_SPORT['Archery']}개"),
   ("태권도 + 유도", _judo_tkd, f"{_judo_tkd}개"))
vs("olympics", "🏅 올림픽", "한국 올림픽 금메달, 더 많은 쪽은?",
   ("동계 쇼트트랙", WIN_SPORT[st], f"{WIN_SPORT[st]}개"),
   ("하계 태권도", SUM_SPORT["Taekwondo"], f"{SUM_SPORT['Taekwondo']}개"))
vs("olympics", "🏅 올림픽", "역대 하계올림픽 금메달이 더 많은 나라는?",
   ("한국", kor_at_gold, f"{kor_at_gold}개"), ("일본", AT["Japan"][1], f"{AT['Japan'][1]}개"))

topic(
    "olympics", "🏅", "올림픽 메달",
    "한국은 무엇으로, 얼마나 땄나",
    "Wikipedia (South Korea at the Olympics · All-time Olympic Games medal table)",
    "https://en.wikipedia.org/wiki/South_Korea_at_the_Olympics",
    [
        choice("ol1", "한국이 하계올림픽에서 금메달을 가장 많이 딴 종목은?",
               sorted(SPORT_KO[c] for c in ol_top[:4]), SPORT_KO[ol_top[0]],
               " · ".join(f"{SPORT_KO[c]} {SUM_SPORT[c]}개" for c in ol_top[:4]),
               "가장 먼저 떠오르는 종목이 맞을 때도 있습니다. 2위와의 차이까지 "
               "짐작했는지가 감입니다."),
        slider("ol2", f"가장 최근 하계올림픽({last_ko})에서 한국이 딴 금메달은 몇 개일까요?",
               last_gold, "개", 0, 40, 1,
               f"{last_ko} 금 {last_gold} · 은 {_last[3]} · 동 {_last[4]}개 "
               f"(금메달 순 {_last[6]}위) · 역대 하계 금메달은 모두 {sum_gold}개",
               "메달 소식은 크게 남지만, 금메달이 정확히 몇 개였는지는 기억하기 "
               "어렵습니다."),
        slider("ol3", "한국의 동계올림픽 금메달 중 쇼트트랙이 차지하는 비율은?",
               st_share, "%", 0, 100, 1,
               f"동계 금메달 {win_gold}개 중 쇼트트랙 {WIN_SPORT[st]}개 = {st_share:.0f}%",
               "'동계는 쇼트트랙'이라는 말은 맞지만, 얼마나 쏠려 있는지는 세어 봐야 "
               "압니다."),
        slider("ol4", "한국의 하계올림픽 역대 최고 순위는 몇 위일까요?",
               GAMES[best], "위", 1, 30, 1,
               f"{best.replace('Seoul', '서울')} 대회 {GAMES[best]}위 · 대회별 순위는 "
               "금메달 수 기준",
               "안방에서 열린 대회의 성적이 가장 좋은 경우가 많습니다."
               if "Seoul" in best else "최고 성적은 기억에 남는 대회와 다를 수 있습니다."),
        slider("ol5", "역대 하계올림픽 금메달 순위에서 한국은 몇 위일까요?",
               int(kor_at_rank), "위", 1, 50, 1,
               f"역대 하계 금메달 순 {kor_at_rank}위 (금 {kor_at_gold}개). 소련처럼 "
               f"지금은 없는 나라도 순위에 들어 있습니다",
               "한 대회 순위는 높아도 역대 순위는 오래 출전한 나라가 앞섭니다."),
    ],
    caveat="위키백과 문서는 누구나 고칠 수 있고, 금지약물로 박탈된 메달이 나중에 "
           "반영되기도 해 받은 시점에 따라 숫자가 달라질 수 있습니다. 순위는 금메달 수 "
           "기준입니다. 종목별 금메달 합계와 역대 메달표의 한국 금메달 수가 같은지 "
           "확인한 뒤에 씁니다.")


# ── 19. 로또 ─────────────────────────────────────────────────────
# 회차마다 본번호 6개 + 보너스 1개. 출현 횟수는 본번호만 센다
def ymd(s):
    return f"{s[:4]}.{int(s[4:6])}.{int(s[6:])}"


def build_lotto():
    LT = load_json("lotto.json")
    lt_n = len(LT)
    lt_d0, lt_d1 = LT[0]["ltRflYmd"], LT[-1]["ltRflYmd"]
    balls = Counter(r[f"tm{i}WnNo"] for r in LT for i in range(1, 7))
    lt_top = leaders(balls)
    lt_min = min(balls.values())
    lt_expect = lt_n * 6 / 45
    # 완전히 무작위일 때 한 번호의 출현 횟수가 흔들리는 폭(표준편차)
    lt_sd = (lt_n * 6 / 45 * (1 - 6 / 45)) ** 0.5
    won1 = [r for r in LT if r["rnk1WnNope"] > 0]
    lt_avg = mean(r["rnk1WnAmt"] for r in won1) / 1e8                     # 억 원
    lt_most = max(LT, key=lambda r: r["rnk1WnNope"])
    lt_carry = sum(1 for r in LT if r["rnk1WnNope"] == 0)
    lt_crowd = sum(1 for r in LT if r["rnk1WnNope"] >= 20)

    vs("lotto", "🎰 로또", f"{lt_n:,}회 동안 더 많이 나온 번호는?",
       ("1번", balls[1], f"{balls[1]}회"), ("45번", balls[45], f"{balls[45]}회"))
    vs("lotto", "🎰 로또", f"{lt_n:,}회 중 더 많았던 회차는?",
       ("1등이 한 명도 없던 회차", lt_carry, f"{lt_carry}회"),
       ("1등이 20명 넘게 나온 회차", lt_crowd, f"{lt_crowd}회"))

    topic(
        "lotto", "🎰", "로또, 정말 랜덤일까",
        f"{ymd(lt_d0)}부터 {ymd(lt_d1)}까지 {lt_n:,}회의 당첨번호",
        "동행복권 로또 6/45 당첨결과",
        "https://www.dhlottery.co.kr/lt645/result",
        [
            # '몇 배'는 1과 2 사이라 정수 눈금으로는 고를 수 없다 — 이 문항만 0.1 눈금
            slider("lt1", "가장 많이 나온 숫자와 가장 적게 나온 숫자는 몇 배 차이 날까요?",
                   balls[lt_top[0]] / lt_min, "배", 1, 3, 0.1,
                   f"{lt_n:,}회 동안 가장 많이 나온 {', '.join(f'{x}번' for x in lt_top)} "
                   f"{balls[lt_top[0]]}회 ÷ 가장 적게 나온 "
                   f"{', '.join(f'{x}번' for x, c in sorted(balls.items()) if c == lt_min)} "
                   f"{lt_min}회 = {balls[lt_top[0]] / lt_min:.2f}배",
                   "'잘 나오는 번호'가 따로 있는 것처럼 보이지만, 완전히 무작위로 뽑아도 "
                   "번호마다 출현 횟수는 꽤 벌어집니다.",
                   hint=f"힌트: 번호 하나는 평균 {lt_expect:.0f}번 나왔습니다"),
            slider("lt2", "1등 당첨금은 1명당 평균 몇 억 원일까요?",
                   lt_avg, "억 원", 0, 100, 1,
                   f"1등이 나온 {len(won1):,}회의 1인당 1등 당첨금 평균 {lt_avg:.1f}억 원 "
                   "(세금 떼기 전)",
                   "'로또 1등 = 수십억'이라는 인상은 초기 회차의 큰 당첨금에서 왔습니다. "
                   "당첨자가 여럿이면 나눠 갖습니다."),
            slider("lt3", "한 회차에 1등이 가장 많이 나온 때는 몇 명이었을까요?",
                   lt_most["rnk1WnNope"], "명", 0, max(100, lt_most["rnk1WnNope"] + 20), 1,
                   f"{lt_most['ltEpsd']}회({ymd(lt_most['ltRflYmd'])}) 1등 "
                   f"{lt_most['rnk1WnNope']}명, 1인당 {lt_most['rnk1WnAmt'] / 1e8:.1f}억 원",
                   "많은 사람이 같은 번호를 고르면 1등이 한꺼번에 쏟아집니다. 번호는 무작위여도 "
                   "사람이 고르는 번호는 무작위가 아닙니다."),
            slider("lt4", f"{lt_n:,}회 중 1등이 한 명도 없어 당첨금이 다음 회로 넘어간 회차는?",
                   lt_carry, "회", 0, 50, 1,
                   f"1등 당첨자 0명인 회차 {lt_carry}회",
                   "1등 확률은 낮지만 사는 사람이 많아 대부분의 회차에서 누군가는 당첨됩니다."),
            slider("lt5", "가장 많이 나온 번호는 가장 적게 나온 번호보다 몇 번 더 나왔을까요?",
                   balls[lt_top[0]] - lt_min, "번", 0, 150, 1,
                   f"차이 {balls[lt_top[0]] - lt_min}회: 가장 많이 {balls[lt_top[0]]}회 "
                   f"({', '.join(f'{x}번' for x in lt_top)}) · "
                   f"가장 적게 {lt_min}회 "
                   f"({', '.join(f'{x}번' for x, c in sorted(balls.items()) if c == lt_min)})",
                   f"완전히 무작위여도 번호마다 평균 {lt_expect:.0f}회에서 ±{lt_sd:.0f}회쯤 "
                   "흔들리고, 45개 중 가장 많은 것과 가장 적은 것은 그보다 훨씬 벌어집니다. "
                   "차이가 있다고 '잘 나오는 번호'가 있는 것은 아닙니다."),
        ],
        caveat="동행복권 공식 내려받기가 없어져, 결과 화면이 쓰는 내부 주소에서 받은 "
               "값입니다. 당첨금은 세금을 떼기 전 금액이고 물가를 반영하지 않았습니다. "
               "출현 횟수는 보너스 번호를 빼고 셉니다.")


topic_or_kept("lotto", ["lotto.json"], build_lotto)


# ── 20. 출근길 지하철 ────────────────────────────────────────────
# 한 달치 교통카드 승하차를 역×노선×시간대로 준다. HR_8_GET_ON_NOPE = 8시~9시 승차.
# 환승역은 개찰구를 같이 쓰는 노선 하나에 몰아 잡히기도 한다(충무로 3호선은 거의 0,
# 4호선에 다 잡힌다). 그래서 노선을 가리지 않고 역 이름으로 합친다
def build_subway():
    import calendar

    rows = load_json("seoul_subway_hourly.json")["CardSubwayTime"]["row"]
    ym = rows[0]["USE_MM"]
    days = calendar.monthrange(int(ym[:4]), int(ym[4:]))[1]

    def name(s):                                    # 잠실(송파구청) → 잠실
        return re.sub(r"\(.*\)", "", s).strip()

    def per_day(hours, kinds):
        """역 이름별 하루 평균 인원. hours 시간대, kinds 는 ON·OFF 중 무엇을 셀지."""
        out = Counter()
        for r in rows:
            out[name(r["STTN"])] += sum(r[f"HR_{h}_GET_{k}_NOPE"] for h in hours for k in kinds)
        return {k: v / days for k, v in out.items()}

    def top(d, n=4):
        return sorted(d, key=d.get, reverse=True)[:n]

    def ppl(v):
        return f"{v / 10_000:.1f}만 명"

    busy = per_day(range(24), ("ON", "OFF"))
    rides = sum(per_day(range(24), ("ON",)).values())
    by_hour = {h: sum(per_day([h], ("ON",)).values()) for h in range(24)}
    peak_h = max(by_hour, key=by_hour.get)
    hour_opts = [7, 8, 17, 18]
    assert peak_h in hour_opts, f"승차가 가장 많은 시간 {peak_h}시가 보기에 없습니다"
    am_off = per_day([7, 8], ("OFF",))
    am_on = per_day([7, 8], ("ON",))
    # 가장 붐비는 역 보기: 상위 3곳 + '강남'(흔히 1위로 짐작하는 역)
    busy_opts = top(busy, 3) + (["강남"] if "강남" not in top(busy, 3) else top(busy)[3:])

    # 2호선·경부선이 만나는 큰 환승역이라 흔히 1위로 짐작하는 역. 보기에 늘 넣는다
    EXTRA_STN = "신도림"

    def with_extra(opts):
        return sorted(set(opts) | {EXTRA_STN})

    def also(d, fmt):
        """근거에 신도림이 상위 5위 밖이면 따로 붙인다."""
        if EXTRA_STN in top(d, 5):
            return ""
        return f" … {EXTRA_STN} {fmt(d[EXTRA_STN])} ({rank_of(d, EXTRA_STN)}위)"

    def nmen(v):
        return f"{v:,.0f}명"

    tag = "🚇 지하철"
    vs("subway", tag, "하루 승하차가 더 많은 역은?",
       *[(s, busy[s], ppl(busy[s])) for s in ("홍대입구", "강남")])
    vs("subway", tag, "출근 시간(7~9시)에 내리는 사람이 더 많은 역은?",
       *[(s, am_off[s], f"{am_off[s]:,.0f}명") for s in ("여의도", "강남")])
    vs("subway", tag, "출근 시간(7~9시)에 타는 사람이 더 많은 역은?",
       *[(s, am_on[s], f"{am_on[s]:,.0f}명") for s in ("신림", "잠실")])
    vs("subway", tag, "지하철 승차가 더 많은 시간대는?",
       *[(f"{h}~{h + 1}시", by_hour[h], ppl(by_hour[h])) for h in (8, 18)])

    def hr(h):
        return f"{h}~{h + 1}시"

    topic(
        "subway", "🚇", "출근길 지하철",
        f"{ym[:4]}년 {int(ym[4:])}월 서울 지하철 역별·시간대별 승하차",
        "서울 열린데이터광장 (지하철 시간대별 승하차 인원)",
        "https://data.seoul.go.kr/",
        [
            choice("sb1", "하루 승하차 인원이 가장 많은 역은?",
                   with_extra(busy_opts), top(busy, 1)[0],
                   f"{ym[:4]}년 {int(ym[4:])}월 하루 평균 승하차 " + " · ".join(
                       f"{s} {ppl(busy[s])}" for s in top(busy, 5)) + also(busy, ppl),
                   "가장 붐빈다고 느끼는 역과 실제로 개찰구를 드나드는 사람이 많은 역은 "
                   "다릅니다. 역 안에서 갈아타기만 하는 사람은 세지 않아, 신도림처럼 "
                   "환승객으로 붐비는 역은 생각보다 아래에 있습니다."),
            slider("sb2", "서울 지하철을 하루에 타는 사람은 평균 몇 만 명일까요?",
                   rides / 10_000, "만 명", 100, 1500, 10,
                   f"{ym[:4]}년 {int(ym[4:])}월 승차 인원 ÷ {days}일 = 하루 {ppl(rides)} "
                   "(개찰구를 지날 때마다 1번, 환승은 세지 않음)",
                   "서울 인구와 견주어 보면 크기가 와닿습니다. 다만 한 사람이 하루 두세 번 "
                   "타면 그만큼 겹쳐 셉니다."),
            choice("sb3", "하루 중 지하철 승차가 가장 많은 시간대는?",
                   [hr(h) for h in hour_opts], hr(peak_h),
                   " · ".join(f"{hr(h)} {ppl(by_hour[h])}" for h in hour_opts),
                   "출근길이 더 붐빈다고 느끼지만, 저녁에는 퇴근에 학교·약속·장보기 이동까지 "
                   "겹칩니다." if peak_h >= 17 else
                   "출근 시간은 정해져 있어 한꺼번에 몰리고, 퇴근 시간은 사람마다 흩어집니다."),
            choice("sb4", "출근 시간(7~9시)에 내리는 사람이 가장 많은 역은?",
                   with_extra(top(am_off)), top(am_off, 1)[0],
                   "7~9시 하루 평균 하차 " + " · ".join(
                       f"{s} {am_off[s]:,.0f}명" for s in top(am_off, 5)) + also(am_off, nmen),
                   "출근길 하차가 많은 역은 일터가 몰린 곳입니다. 강남 말고도 큰 업무 "
                   "지구가 여럿 있습니다."),
            choice("sb5", "출근 시간(7~9시)에 타는 사람이 가장 많은 역은?",
                   with_extra(top(am_on)), top(am_on, 1)[0],
                   "7~9시 하루 평균 승차 " + " · ".join(
                       f"{s} {am_on[s]:,.0f}명" for s in top(am_on, 5)) + also(am_on, nmen),
                   "출근길 승차가 많은 역은 사람이 많이 사는 곳, 또는 다른 교통에서 "
                   "갈아타는 곳입니다."),
        ],
        caveat="교통카드로 개찰구를 지난 기록입니다. 지하철 안에서 갈아탄 것은 세지 않고, "
               "환승역은 개찰구를 함께 쓰는 노선 하나에 몰아 잡히기도 해 역 이름으로 "
               "합쳐 셉니다. 서울 시내 역만 들어 있으며 주말을 포함한 한 달 평균입니다.")


topic_or_kept("subway", ["seoul_subway_hourly.json"], build_subway)


# ════════════════════════════════════════════════════════════════
def shown(q):
    """정답지·화면 출력용 정답 글자. 공동 정답은 · 로 잇는다."""
    if q["type"] == "slider":
        return f"{q['answer']}{q.get('unit', '')}"
    return " · ".join(q["answer"]) if isinstance(q["answer"], list) else q["answer"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for t in TOPICS:
        assert len(t["questions"]) == 5, f"{t['id']}: 문항이 5개가 아닙니다"
        pack = {k: t[k] for k in ("id", "emoji", "title", "blurb",
                                  "source", "source_url", "caveat")}
        pack["questions"] = t["questions"]
        io.open(OUT / f"{t['id']}.json", "w", encoding="utf-8").write(
            json.dumps(pack, ensure_ascii=False, indent=2))
        if t["id"] in SHOWN:
            index.append({k: t[k] for k in ("id", "emoji", "title", "blurb",
                                            "source")})
    missing = set(SHOWN) - {t["id"] for t in TOPICS}
    assert not missing, f"SHOWN 에 없는 주제가 있습니다: {missing}"
    index.sort(key=lambda t: SHOWN.index(t["id"]))       # 계산한 순서가 아니라 SHOWN 순서로

    # 홈 화면의 두 놀이: 내 월급 계산기 · 더 많을까 적을까. 보이는 주제의 문제만 싣는다.
    # 원본이 없어 지난번 값을 쓴 주제는 '더 많을까' 문제도 지난번 것을 그대로 가져온다
    EXTRA = APP / "extras"
    EXTRA.mkdir(exist_ok=True)
    pairs = [p for p in VERSUS if p["tid"] in SHOWN]
    if KEPT and (EXTRA / "versus.json").exists():
        pairs += [p for p in json.load(io.open(EXTRA / "versus.json", encoding="utf-8"))
                  if p["tid"] in KEPT and p["tid"] in SHOWN]
    pairs.sort(key=lambda p: SHOWN.index(p["tid"]))
    io.open(EXTRA / "versus.json", "w", encoding="utf-8").write(
        json.dumps(pairs, ensure_ascii=False, indent=1))
    io.open(EXTRA / "salary.json", "w", encoding="utf-8").write(
        json.dumps(CALC, ensure_ascii=False))

    # 제목·부제는 화면을 바꾸며 고친 값을 지킨다 (2장에서 내 제목으로 바꾼다).
    # 다시 계산해도 내 제목이 «감 테스트»로 돌아가지 않게, 있던 값을 읽어 쓴다
    old = {}
    if (APP / "topics.json").exists():
        try:
            old = json.load(io.open(APP / "topics.json", encoding="utf-8"))
        except ValueError:
            old = {}
    io.open(APP / "topics.json", "w", encoding="utf-8").write(json.dumps({
        "title": old.get("title", "감 테스트"),
        "subtitle": old.get("subtitle", "숫자에 대한 당신의 감은 몇 점입니까"),
        # 등급·문구는 여기서 고칩니다. 코드를 건드릴 일이 아닙니다
        "grades": [
            {"min": 90, "name": "촉이 데이터급", "emoji": "🎯"},
            {"min": 70, "name": "감이 좋은 편", "emoji": "📊"},
            {"min": 50, "name": "보통의 감각", "emoji": "🙂"},
            {"min": 30, "name": "느낌대로 삽니다", "emoji": "🎲"},
            {"min": 0, "name": "감은 접어두시죠", "emoji": "🙈"},
        ],
        # 문구는 여러 개 중 하나를 무작위로 고른다. 슬라이더는 오차가 클수록 아래 칸으로,
        # 높게(high)·낮게(low) 빗나갔는지에 따라 다른 문구 — 놀리되 상처 주지 않게
        "reactions_choice": {
            "hit": ["맞히셨습니다", "촉이 살아 있네요", "데이터랑 통했습니다",
                    "정답! 혹시 통계청 다니세요?", "이건 감이 아니라 실력입니다"],
            "miss": ["다들 그렇게 찍습니다", "직감이 잠깐 출장 중이네요", "상식이 배신한 순간",
                     "괜찮아요, 그래서 퀴즈입니다", "그럴 줄 알고 낸 문제입니다",
                     "아깝… 다음 문제는 맞힐 겁니다"],
        },
        "reactions": [
            {"max": 5, "any": ["소름… 거의 정확합니다", "촉이 좋으시네요",
                               "숫자 감각 인정합니다", "혹시 답을 미리 보셨나요?"]},
            {"max": 20, "high": ["살짝 높게 보셨어요", "조금 후하게 잡으셨네요",
                                 "아깝게 위로 빗나갔어요"],
                        "low": ["살짝 낮게 보셨어요", "조금 짜게 잡으셨네요",
                                "아깝게 아래로 빗나갔어요"]},
            {"max": 60, "high": ["생각보다 꽤 적습니다", "부풀려진 인상에 속으셨네요",
                                 "실제는 그보다 한참 아래"],
                        "low": ["생각보다 꽤 많습니다", "실제는 그보다 한참 위",
                                "현실이 상상을 넘었네요"]},
            {"max": 999, "high": ["상상력이 풍부하시네요", "꽤 멀리, 위로 가셨습니다",
                                  "숫자를 너무 크게 보셨어요"],
                         "low": ["현실은 훨씬 컸습니다", "꽤 멀리, 아래로 가셨습니다",
                                 "이건 다들 놀라는 숫자입니다"]},
        ],
        "topics": index,
    }, ensure_ascii=False, indent=2))

    # 사람이 읽는 정답지
    n_q = sum(len(t["questions"]) for t in TOPICS)
    md = [f"# 문항 정답지 — {len(TOPICS)}주제 {n_q}문항", "",
          "> 모든 값은 `data/raw/` 의 원본에서 `tools/build_topics.py` 가 계산합니다.", ""]
    for t in TOPICS:
        md += [f"## {t['emoji']} {t['title']}", "",
               f"- 출처: {t['source']} · {t['source_url']}"]
        if t["caveat"]:
            md += [f"- **한계** {t['caveat']}"]
        md += [""]
        for q in t["questions"]:
            a = shown(q)
            md += [f"**{q['text']}**", "",
                   f"- 정답 **{a}**", f"- 근거 {q['basis']}",
                   f"- 왜 틀리나 {q['why']}", ""]
    io.open(HERE / "정답지.md", "w", encoding="utf-8").write("\n".join(md))

    print(f"주제 {len(TOPICS)}개 · 문항 {sum(len(t['questions']) for t in TOPICS)}개")
    print(f"화면에 보이는 주제 {len(index)}개 · 숨긴 주제 {len(TOPICS) - len(index)}개 (SHOWN)")
    for t in TOPICS:
        hide = "" if t["id"] in SHOWN else "  [숨김]"
        print(f"\n{t['emoji']} {t['title']}  ({t['source']}){hide}")
        for q in t["questions"]:
            a = shown(q)
            print(f"    [{q['type'][:6]:<6}] {q['text'][:44]:<44} → {a}")


if __name__ == "__main__":
    main()
