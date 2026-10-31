/* 감 테스트 — 주제 고르기 · 진행 · 채점 · 결과 · 공유
 *
 * 계산은 전부 여기서 끝난다. 서버는 촌평 한 줄을 받을 때만 부른다.
 * 원본 데이터는 이 앱에 들어오지 않는다 — topics/*.json 에 정답과 근거만 있다.
 */

const $ = (id) => document.getElementById(id);
const views = {
  home: $("view-home"), quiz: $("view-quiz"), result: $("view-result"),
  salary: $("view-salary"), versus: $("view-versus"),
};

let INDEX = null;     // topics.json
let PACK = null;      // 지금 푸는 주제
let idx = 0;
let answers = [];

const DONE_KEY = "gam-test-done";

/* 푼 주제를 기억한다. 이 브라우저 안에만 남고 서버로 가지 않는다.
   시크릿 창이나 저장소 차단 환경에서는 읽기·쓰기가 막히므로 감싼다. */
function doneMap() {
  try { return JSON.parse(localStorage.getItem(DONE_KEY) || "{}"); }
  catch { return {}; }
}
function markDone(id, score) {
  try {
    const m = doneMap();
    m[id] = Math.max(score, m[id] ?? 0);
    localStorage.setItem(DONE_KEY, JSON.stringify(m));
  } catch { /* 저장이 막혀 있어도 앱은 그대로 돈다 */ }
}

/* ── 채점 ────────────────────────────────────────────
 * 택1     맞으면 100, 틀리면 0
 * 슬라이더 오차율 × 200 만큼 깎는다 (10% 빗나가면 80점)
 */
/* 택1 정답은 하나일 수도, 공동 1위처럼 여럿(배열)일 수도 있다 */
function isAnswer(q, o) {
  return Array.isArray(q.answer) ? q.answer.includes(o) : o === q.answer;
}
function scoreOne(q, mine) {
  if (q.type === "choice") return isAnswer(q, mine) ? 100 : 0;
  const err = Math.abs(mine - q.answer) / Math.abs(q.answer);
  return Math.max(0, Math.round(100 - err * 200));
}
function errPct(q, mine) {
  if (q.type === "choice") return isAnswer(q, mine) ? 0 : 100;
  return Math.abs(mine - q.answer) / Math.abs(q.answer) * 100;
}
function gradeOf(s) {
  return INDEX.grades.find((g) => s >= g.min) || INDEX.grades.at(-1);
}
/* 문구가 여럿이면 하나를 무작위로. 예전 topics.json 처럼 한 줄짜리여도 그대로 돈다 */
function pick(x) {
  return Array.isArray(x) ? x[Math.floor(Math.random() * x.length)] : (x || "");
}
function choiceReaction(hit) {
  return pick(INDEX.reactions_choice[hit ? "hit" : "miss"]);
}
/* 슬라이더는 오차 칸을 고른 뒤, 높게·낮게 빗나간 방향에 맞는 문구를 쓴다 */
function reactionOf(q, mine, pct) {
  if (q.type === "choice") return choiceReaction(isAnswer(q, mine));
  const r = INDEX.reactions.find((x) => pct <= x.max) || {};
  return pick(r.any || (mine > q.answer ? r.high : r.low) || r.text);
}
/* 받침이 있으면 을, 없으면 를 */
function josa(w) {
  const c = String(w).trim().slice(-1).charCodeAt(0);
  if (c < 0xac00 || c > 0xd7a3) return "을";
  return (c - 0xac00) % 28 ? "을" : "를";
}
/* Gemini 에게는 숫자가 아니라 방향만 보낸다 */
function directionOf(q, mine) {
  if (q.type === "choice") return isAnswer(q, mine) ? "맞힘" : "틀림";
  const d = (mine - q.answer) / Math.abs(q.answer);
  if (Math.abs(d) <= 0.08) return "비슷";
  return d > 0 ? "높게" : "낮게";
}

function show(name) {
  Object.entries(views).forEach(([k, el]) => (el.hidden = k !== name));
  window.scrollTo({ top: 0 });
}

/* ── 시작 ────────────────────────────────────────── */
async function boot() {
  INDEX = await (await fetch("topics.json")).json();
  $("title").textContent = INDEX.title;
  $("subtitle").textContent = INDEX.subtitle;

  const p = new URLSearchParams(location.search);
  if (p.has("s")) {
    const g = p.get("g") || "";
    const t = INDEX.topics.find((x) => x.id === p.get("t"));
    $("shared-banner").innerHTML =
      `친구가 ${t ? `<b>${t.emoji} ${t.title}</b> 에서 ` : ""}` +
      `<b>${Number(p.get("s"))}점 · ${g}</b>${josa(g || "점")} 받았습니다. 당신은요?`;
    $("shared-banner").hidden = false;
  }

  renderGrid();
  $("btn-next").onclick = next;
  $("btn-back").onclick = () => show("home");
  $("btn-more").onclick = () => { renderGrid(); show("home"); };
  $("btn-share").onclick = share;
  document.querySelectorAll(".back-home").forEach((b) => (b.onclick = () => show("home")));
  $("go-salary").onclick = openSalary;
  $("go-versus").onclick = openVersus;
  $("btn-salary").onclick = calcSalary;
  $("salary-in").onkeydown = (e) => { if (e.key === "Enter") calcSalary(); };
  $("btn-salary-share").onclick = shareSalary;
  $("vs-next").onclick = vsNext;
  $("vs-again").onclick = openVersus;
  $("vs-share").onclick = shareVersus;
  if (p.has("t")) {
    const t = INDEX.topics.find((x) => x.id === p.get("t"));
    if (t && !p.has("s")) start(t.id);
  }
  if (p.get("go") === "salary") openSalary();
  if (p.get("go") === "versus") openVersus();
}

/* 공유 — 폰이면 공유 창, 아니면 링크 복사 */
async function shareText(btn, text, query) {
  const url = `${location.origin}${location.pathname}?${query}`;
  const label = btn.textContent;
  try {
    if (navigator.share) { await navigator.share({ title: INDEX.title, text, url }); return; }
    await navigator.clipboard.writeText(`${text}\n${url}`);
    btn.textContent = "링크를 복사했습니다";
    setTimeout(() => (btn.textContent = label), 1800);
  } catch { /* 사용자가 취소한 경우 */ }
}

function renderGrid() {
  const done = doneMap();
  const n = INDEX.topics.filter((t) => t.id in done).length;
  $("progress-note").textContent =
    n ? `${INDEX.topics.length}개 중 ${n}개 풀었습니다` : "";
  $("topic-grid").innerHTML = "";
  INDEX.topics.forEach((t) => {
    const b = document.createElement("button");
    b.className = "tcard" + (t.id in done ? " done" : "");
    b.innerHTML =
      `<span class="temoji">${t.emoji}</span>` +
      `<span class="ttitle">${t.title}</span>` +
      `<span class="tblurb">${t.blurb}</span>` +
      (t.id in done ? `<span class="tscore">${done[t.id]}점</span>` : "");
    b.onclick = () => start(t.id);
    $("topic-grid").appendChild(b);
  });
}

async function start(id) {
  PACK = await (await fetch(`topics/${id}.json`)).json();
  idx = 0; answers = [];
  $("topic-name").textContent = `${PACK.emoji} ${PACK.title}`;
  renderQ();
  show("quiz");
}

/* ── 문항 ────────────────────────────────────────── */
function renderQ() {
  const q = PACK.questions[idx];
  $("progress-bar").style.width = `${(idx / PACK.questions.length) * 100}%`;
  $("q-count").textContent = `${idx + 1} / ${PACK.questions.length}`;
  $("q-text").textContent = q.text;
  $("q-hint").textContent = q.hint || "";      // 힌트는 있는 문항에만
  $("q-hint").hidden = !q.hint;
  $("btn-next").textContent =
    idx === PACK.questions.length - 1 ? "결과 보기" : "다음";

  const isChoice = q.type === "choice";
  $("q-choice").hidden = !isChoice;
  $("q-slider").hidden = isChoice;

  if (isChoice) {
    answers[idx] = null;
    $("btn-next").disabled = true;
    $("q-choice").innerHTML = "";
    q.options.forEach((opt) => {
      const b = document.createElement("button");
      b.className = "choice";
      b.type = "button";
      b.textContent = opt;
      b.setAttribute("aria-pressed", "false");
      b.onclick = () => {
        answers[idx] = opt;
        [...$("q-choice").children].forEach((c) =>
          c.setAttribute("aria-pressed", String(c === b)));
        $("btn-next").disabled = false;
      };
      $("q-choice").appendChild(b);
    });
  } else {
    const s = $("slider");
    s.min = q.min; s.max = q.max; s.step = q.step;
    // 기본값을 한가운데에서 한 칸 비켜 둔다 — 가운데면 생각 없이 맞는다
    s.value = Math.round(((q.min + q.max) / 2 + q.step) / q.step) * q.step;
    $("slider-min").textContent = `${q.min}${q.unit}`;
    $("slider-max").textContent = `${q.max}${q.unit}`;
    const paint = () => {
      answers[idx] = Number(s.value);
      $("slider-out").textContent = `${s.value}${q.unit}`;
    };
    s.oninput = paint;
    paint();
    $("btn-next").disabled = false;
  }
}

function next() {
  if (idx < PACK.questions.length - 1) { idx++; renderQ(); return; }
  renderResult();
  show("result");
}

/* ── 결과 ────────────────────────────────────────── */
function pos(q, v) {
  return Math.max(0, Math.min(100, ((v - q.min) / (q.max - q.min)) * 100));
}

function renderResult() {
  const total = Math.round(
    PACK.questions.map((q, i) => scoreOne(q, answers[i]))
      .reduce((a, b) => a + b, 0) / PACK.questions.length);
  const g = gradeOf(total);
  markDone(PACK.id, total);

  $("result-topic").textContent = `${PACK.emoji} ${PACK.title}`;
  $("score").innerHTML = `${total}<span>점</span>`;
  $("grade").textContent = g.name;
  $("grade-emoji").textContent = g.emoji || "";

  const box = $("cards");
  box.innerHTML = "";
  PACK.questions.forEach((q, i) => {
    const mine = answers[i];
    const pct = errPct(q, mine);
    const hit = q.type === "choice" ? isAnswer(q, mine) : pct <= 5;
    const el = document.createElement("div");
    el.className = "card";
    el.innerHTML = `
      <h3>${q.text}</h3>
      <p class="react ${hit ? "hit" : pct > 40 ? "miss" : ""}">${reactionOf(q, mine, pct)}</p>
      ${q.type === "choice" ? choiceBody(q, mine) : sliderBody(q, mine)}
      <p class="why">💡 ${q.why}</p>
      <details><summary>근거 보기</summary><p class="basis">${q.basis}</p></details>`;
    box.appendChild(el);
  });

  $("caveat").hidden = !PACK.caveat;
  if (PACK.caveat) $("caveat").innerHTML = `<b>이 데이터의 한계</b><br>${PACK.caveat}`;
  $("source").innerHTML =
    `출처 <a href="${PACK.source_url}" target="_blank" rel="noopener">${PACK.source}</a>`;

  requestAnimationFrame(() => setTimeout(animate, 60));
  askComment(total, g.name);
}

function choiceBody(q, mine) {
  const label = (o) => {
    const real = isAnswer(q, o);
    const cls = real && o === mine ? "both"
      : real ? "real" : o === mine ? "mine" : "";
    const tag = real && o === mine ? " 실제 · 내 답"
      : real ? " 실제" : o === mine ? " 내 답" : "";
    return `<span class="pill ${cls}">${o}${tag}</span>`;
  };
  return `<div class="pills">${q.options.map(label).join("")}</div>`;
}

function sliderBody(q, mine) {
  const rows = [["내 답", mine, "mine"]];
  rows.push(["실제", q.answer, "real"]);
  const max = Math.max(...rows.map((r) => Math.abs(Number(r[1])))) || 1;
  const bars = rows.map(([k, v, cls]) => `
      <div class="row">
        <span class="k">${k}</span>
        <span class="bar ${cls}"><i data-w="${Math.abs(Number(v)) / max * 100}"></i></span>
        <span class="v">${v}${q.unit}</span>
      </div>`).join("");
  return `
    <div class="track">
      <span class="line"></span>
      <span class="ghost" style="left:${pos(q, mine)}%"></span>
      <span class="live" data-left="${pos(q, q.answer)}" style="left:${pos(q, mine)}%"></span>
    </div>
    <div class="rows">${bars}</div>`;
}

function animate() {
  document.querySelectorAll(".bar > i").forEach((b) => (b.style.width = b.dataset.w + "%"));
  document.querySelectorAll(".track .live").forEach((m) => (m.style.left = m.dataset.left + "%"));
}

/* ── 촌평 — 없어도 앱은 그대로 돈다 ──────────────── */
async function askComment(total, grade) {
  const el = $("ai-comment");
  el.hidden = false;
  el.innerHTML = `<span class="tag">촌평을 받아오는 중…</span>`;
  const directions = PACK.questions.map((q, i) => directionOf(q, answers[i]));
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 3000);
    const r = await fetch("/api/comment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ score: total, grade, directions, topic: PACK.title }),
      signal: ctl.signal,
    });
    clearTimeout(timer);
    const j = await r.json();
    if (!j || !j.comment) { el.hidden = true; return; }
    el.innerHTML = `<span class="tag">한 줄 촌평</span><br>${j.comment}` +
      (j.nickname ? `<br><span class="nick">${j.nickname}</span>` : "");
  } catch {
    el.hidden = true;
  }
}

/* ── 공유 ────────────────────────────────────────── */
async function share() {
  const total = $("score").textContent.replace(/[^0-9]/g, "");
  const grade = $("grade").textContent;
  const url = `${location.origin}${location.pathname}` +
    `?t=${encodeURIComponent(PACK.id)}&s=${total}&g=${encodeURIComponent(grade)}`;
  const text = `감 테스트 · ${PACK.title} ${total}점 · ${grade}`;
  try {
    if (navigator.share) { await navigator.share({ title: INDEX.title, text, url }); return; }
    await navigator.clipboard.writeText(`${text}\n${url}`);
    $("btn-share").textContent = "링크를 복사했습니다";
    setTimeout(() => ($("btn-share").textContent = "친구에게 보내기"), 1800);
  } catch { /* 사용자가 취소한 경우 */ }
}

/* ── 내 월급 계산기 ──────────────────────────────────
 * extras/salary.json 의 세계 소득 분포 곡선(World Bank PIP)에서 읽는다.
 * curve = [하루 달러(2021년 구매력), 그보다 적게 버는 사람 %] 62점. k 는 원/월 → 하루 달러.
 * 입력한 월급은 이 함수 밖으로 나가지 않는다 — 공유할 때도 %만 보낸다.
 */
let SAL = null;
let salTop = null;

async function openSalary() {
  if (!SAL) SAL = await (await fetch("extras/salary.json")).json();
  $("salary-note").textContent =
    `세계은행 PIP ${SAL.year}년 세계 분포(가구 1인당 소득·소비, 2021년 구매력 기준)로 ` +
    `계산합니다. 넣은 금액을 혼자 쓰는 한 달 소득으로 보고 ${SAL.cpi_year}년 한국 ` +
    `물가로 바꿉니다. 세전 월급이나 가족이 함께 쓰는 돈이면 실제보다 높게 나옵니다.`;
  show("salary");
  $("salary-in").focus();
}

/* 곡선 위 두 점 사이를 로그 눈금으로 잇는다 (build_topics.py 의 below_pct 와 같은 셈) */
function belowPct(usd) {
  const c = SAL.curve;
  if (usd <= c[0][0]) return c[0][1];
  if (usd >= c[c.length - 1][0]) return c[c.length - 1][1];
  for (let i = 1; i < c.length; i++) {
    if (usd <= c[i][0]) {
      const [x0, y0] = c[i - 1], [x1, y1] = c[i];
      return y0 + (Math.log(usd) - Math.log(x0)) / (Math.log(x1) - Math.log(x0)) * (y1 - y0);
    }
  }
}

function calcSalary() {
  const man = Number($("salary-in").value);
  if (!(man > 0)) { $("salary-in").focus(); return; }
  const usd = man * 10000 * SAL.k;
  const top = 100 - belowPct(usd);
  const ceil = top <= 100 - SAL.curve[SAL.curve.length - 1][1];
  salTop = ceil ? `상위 ${(100 - SAL.curve[SAL.curve.length - 1][1]).toFixed(2)}% 안` :
    `상위 ${top < 1 ? top.toFixed(2) : top.toFixed(1)}%`;
  const times = usd / SAL.median_day;
  const tier = top <= 1 ? ["👑", "세계 1% 안의 소득"] : top <= 10 ? ["💎", "세계 10% 안의 소득"] :
    top <= 50 ? ["🙂", "세계 한가운데보다 위"] : ["🌱", "세계 한가운데보다 아래"];
  $("salary-emoji").textContent = tier[0];
  $("salary-top").textContent = salTop;
  $("salary-line").textContent = tier[1];
  $("salary-detail").innerHTML =
    `하루 <b>${usd.toFixed(1)}달러</b>(2021년 구매력 기준)로, 세계 한가운데 사람` +
    `(한 달 약 ${Math.round(SAL.median_month_won / 10000)}만 원)의 ` +
    `<b>${times >= 1 ? times.toFixed(1) + "배" : Math.round(times * 100) + "%"}</b>입니다. ` +
    `나보다 적게 버는 사람이 <b>${(100 - top).toFixed(1)}%</b>입니다.`;
  $("salary-out").hidden = false;
  $("salary-actions").hidden = false;
}

function shareSalary() {
  shareText($("btn-salary-share"), `내 월급은 전 세계 ${salTop} · 당신은요?`, "go=salary");
}

/* ── 더 많을까 적을까 ────────────────────────────────
 * extras/versus.json 의 짝 31개 중 10개를 무작위로 뽑는다. 값이 큰 쪽이 정답.
 * 최고 기록은 이 브라우저에만 남는다.
 */
const VS_KEY = "gam-test-vs-best";
const VS_N = 10;
let VS_ALL = null;
let vs = null;                  // { list, i, right, streak, maxStreak }

function shuffle(a) {
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function vsBest() {
  try { return JSON.parse(localStorage.getItem(VS_KEY) || "{}"); }
  catch { return {}; }
}

async function openVersus() {
  if (!VS_ALL) VS_ALL = await (await fetch("extras/versus.json")).json();
  // 왼쪽·오른쪽도 섞는다 — 늘 같은 쪽이 정답이면 눈치로 맞힌다
  const list = shuffle([...VS_ALL]).slice(0, VS_N)
    .map((p) => (Math.random() < 0.5 ? p : { ...p, a: p.b, b: p.a }));
  vs = { list, i: 0, right: 0, streak: 0, maxStreak: 0 };
  $("vs-play").hidden = false;
  $("vs-end").hidden = true;
  show("versus");
  vsRender();
}

function vsRender() {
  const p = vs.list[vs.i];
  $("vs-count").textContent = `${vs.i + 1} / ${VS_N}`;
  $("vs-streak").textContent = vs.streak >= 2 ? `🔥 ${vs.streak}연속` : "";
  $("vs-bar").style.width = `${(vs.i / VS_N) * 100}%`;
  $("vs-tag").textContent = p.tag;
  $("vs-q").textContent = p.q;
  for (const side of ["a", "b"]) {
    const b = $(`vs-${side}`);
    b.className = "vs-btn";
    b.disabled = false;
    b.innerHTML = `<span class="vs-label">${p[side].label}</span>` +
                  `<span class="vs-val" hidden>${p[side].show}</span>`;
    b.onclick = () => vsPick(side);
  }
  $("vs-react").hidden = true;
  $("vs-next").hidden = true;
}

function vsPick(side) {
  const p = vs.list[vs.i];
  const win = p.a.value > p.b.value ? "a" : "b";
  const hit = side === win;
  vs.right += hit;
  vs.streak = hit ? vs.streak + 1 : 0;
  vs.maxStreak = Math.max(vs.maxStreak, vs.streak);
  for (const s of ["a", "b"]) {
    const b = $(`vs-${s}`);
    b.disabled = true;
    b.querySelector(".vs-val").hidden = false;
    b.classList.add(s === win ? "win" : "lose", ...(s === side ? ["picked"] : []));
  }
  $("vs-react").textContent = choiceReaction(hit);
  $("vs-react").className = "vs-react " + (hit ? "hit" : "miss");
  $("vs-react").hidden = false;
  $("vs-streak").textContent = vs.streak >= 2 ? `🔥 ${vs.streak}연속` : "";
  $("vs-next").textContent = vs.i === VS_N - 1 ? "결과 보기" : "다음";
  $("vs-next").hidden = false;
}

function vsNext() {
  if (++vs.i < VS_N) { vsRender(); return; }
  const old = vsBest();
  const best = { right: Math.max(old.right || 0, vs.right),
                 streak: Math.max(old.streak || 0, vs.maxStreak) };
  try { localStorage.setItem(VS_KEY, JSON.stringify(best)); } catch { /* 막혀도 돈다 */ }
  const g = gradeOf(vs.right * 10);
  $("vs-emoji").textContent = g.emoji || "";
  $("vs-score").innerHTML = `${vs.right}<span> / ${VS_N}</span>`;
  $("vs-grade").textContent = g.name;
  $("vs-best").textContent =
    `이번 최고 연속 ${vs.maxStreak}개 · 내 최고 기록 ${best.right}/${VS_N}, ${best.streak}연속`;
  $("vs-bar").style.width = "100%";
  $("vs-play").hidden = true;
  $("vs-end").hidden = false;
  window.scrollTo({ top: 0 });
}

function shareVersus() {
  shareText($("vs-share"),
    `더 많을까 적을까 ${vs.right}/${VS_N} · 최고 ${vs.maxStreak}연속 · 당신은요?`, "go=versus");
}

boot();
