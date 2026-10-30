# 감 테스트 — 공개 데이터로 만든 추측 게임

주제 10개, 주제당 5문항. **모든 숫자는 공개 데이터에서 직접 계산한 값**입니다.
토요일 실습의 골격 저장소이고, 이대로 Vercel에 올리면 바로 돌아갑니다.

**10개가 모두 채워져 있습니다.** 실습에서는 자기가 맡은 한 주제를 지우고
`tools/build_topics.py` 안에서 다시 계산해 넣습니다. 이미 들어 있는 값은
자기 계산과 대조할 때 씁니다.

## 돌려보기

```
python -m http.server 8000      # index.html 을 더블클릭하면 안 됩니다
```

## Claude Code 설정

`.claude/settings.json` 은 이 폴더에서 켠 Claude Code가 **수동 모드**(고치기·실행마다 묻는다)로 시작하게 합니다 — 교재 1-4. 배포에서는 빠집니다(`.vercelignore`).

## 올리기

```
1. https://github.com/kym20b/gam-test → Use this template → 내 GitHub 으로
2. vercel.com → Add New → Project → Import
3. Framework Preset: Other → Deploy
```

빌드가 없어서 20~40초면 끝나고, 이후에는 `git push` 할 때마다 자동으로 다시 올라갑니다.

## 데이터가 오는 길

```
   [내 노트북]                                   [인터넷]

   tools/fetch_all.py      공개 데이터 6곳에서 받기 (키·로그인 없음)
        ↓  data/raw/  약 19MB   ← git 에도 배포에도 올라가지 않습니다
   tools/build_topics.py   원본에서 문항 계산
        ↓
   topics/*.json  약 40KB  ← 이것만 올라갑니다
```

원본을 브라우저로 보내서 거기서 평균을 내는 게 아닙니다. **평균은 내 노트북에서 미리
내고, 그 결과만 싣습니다.** 그래서 폰에서 즉시 뜹니다.

## 파일

| 파일 | 하는 일 |
|---|---|
| `index.html` | 화면 한 장 (주제 고르기 · 문항 · 결과) |
| `style.css` | 폰 세로 기준. 색과 간격은 여기서만 정합니다 |
| `app.js` | 진행 · 채점 · 결과 · 공유. **계산은 전부 여기서 끝납니다** |
| `topics.json` | 주제 목록 · 등급 · 반응 문구 |
| `topics/*.json` | ★ 주제별 5문항. 정답 · 근거 · 해설 · 한계 |
| `api/comment.py` | Gemini 촌평. 서버가 필요한 유일한 자리 |
| `tools/fetch_all.py` | 공개 데이터 내려받기 (배포 제외) |
| `tools/build_topics.py` | 원본 → 문항 계산 (배포 제외) |

## 주제 10개

| | 주제 | 출처 |
|---|---|---|
| 🌡️ | 서울은 얼마나 더워졌나 | Open-Meteo Archive (ERA5) |
| ☔ | 비는 언제, 얼마나 | Open-Meteo Archive (ERA5) |
| 😷 | 미세먼지, 언제 나쁜가 | Open-Meteo Air Quality (CAMS) |
| ⏰ | 우리는 얼마나 일하나 | Our World in Data |
| 👶 | 인구와 출산 | World Bank |
| 🌐 | 인터넷과 연결 | World Bank |
| 💵 | 원달러 환율 26년 | Frankfurter (ECB) |
| 🏙️ | 서울·도쿄·런던·싱가포르 | Open-Meteo Archive (ERA5) |
| 🌎 | 지진은 얼마나 자주 | USGS |
| 🩺 | 얼마나 오래 사나 | Our World in Data |

## Gemini 촌평 (선택)

```
Vercel → Settings → Environment Variables
  GEMINI_API_KEY = (aistudio.google.com 에서 받은 키)
→ 저장하고 다시 배포
```

**키가 없으면 촌평만 빠지고 나머지는 그대로 돕니다.** 로컬(`python -m http.server`)에서는
서버리스 함수가 돌지 않으므로 촌평 자리가 나타나지 않습니다. 정상입니다.

`api/comment.py` 맨 위의 `MODEL` 은 AI Studio 에서 지금 쓸 수 있는 이름으로 맞춥니다.

## 고치는 자리

| 바꾸고 싶은 것 | 어디 |
|---|---|
| 문항 · 정답 · 근거 · 해설 · 한계 | `tools/build_topics.py` → 다시 돌리면 `topics/*.json` 갱신 |
| 등급 이름, 반응 문구 | `topics.json` (`build_topics.py` 가 씁니다) |
| 점수 계산식 | `app.js` 의 `scoreOne()` |
| 색 · 여백 | `style.css` 맨 위의 `:root` |

> **숫자를 손으로 옮겨 적지 마십시오.** `build_topics.py` 안에서 계산식으로 넣어야
> 데이터를 다시 받았을 때 자동으로 갱신됩니다.

## 이 데이터를 믿을 때의 한계

각 주제의 결과 화면 맨 아래에 **「이 데이터의 한계」** 상자가 있습니다. 공개 데이터라고
맞는 데이터가 아닙니다. 실제로 이 앱을 만들며 겪은 것들입니다.

- **ERA5 재분석**의 서울 역대 최고기온은 38.7도(2018년)지만, **기상청 관측 기록은 39.6도**입니다. 날씨는 받는 주소에 `&models=era5` 를 붙여 모델을 고정했습니다 — 적지 않으면 Open-Meteo가 고르는 모델이 바뀌어 지난 값이 달라집니다
- World Bank의 '모든 나라' 목록에는 **유럽연합·고소득국 같은 집계치가 78개** 섞여 있습니다
- 공휴일 API의 2025년 한국 목록에는 제헌절이 공휴일로 들어 있었습니다. **2025년 기준으로는
  틀린 값이지만, 2026년 5월 개정으로 제헌절은 다시 공휴일이 되었습니다** — 같은 값이
  기준 시점에 따라 맞기도 틀리기도 합니다. 공휴일 데이터는 제도 변경에 민감해 주제에서 뺐습니다

## 주의

- **Vercel Hobby 플랜은 개인·비상업 용도 전용입니다.**
- 공개 URL입니다. 회사 데이터를 올리지 마십시오.
- 키를 저장소에 커밋하지 마십시오. 한 번 올라가면 지워도 기록에 남습니다.
