/* 감 테스트 — 주제 고르기 · 진행 · 채점 · 결과 · 공유
 *
 * 계산은 전부 여기서 끝난다. 서버는 촌평 한 줄을 받을 때만 부른다.
 * 원본 데이터는 이 앱에 들어오지 않는다 — topics/*.json 에 정답과 근거만 있다.
 */

const $ = (id) => document.getElementById(id);
const views = { home: $("view-home"), quiz: $("view-quiz"), result: $("view-result") };

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
function reactionOf(q, mine, pct) {
  if (q.type === "choice") {
    return isAnswer(q, mine) ? INDEX.reactions_choice.hit
                             : INDEX.reactions_choice.miss;
  }
  return (INDEX.reactions.find((r) => pct <= r.max) || { text: "" }).text;
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
  if (p.has("t")) {
    const t = INDEX.topics.find((x) => x.id === p.get("t"));
    if (t && !p.has("s")) start(t.id);
  }
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

boot();
