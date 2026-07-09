let grade = 3;
let pollTimer = null;

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const grid = $("#subjectGrid");
const files = {};   // key -> File

/* ── 학년 세그먼트 ── */
document.querySelectorAll("#gradeSeg button").forEach(b => {
  b.addEventListener("click", () => {
    grade = +b.dataset.g;
    document.querySelectorAll("#gradeSeg button").forEach(x => x.classList.toggle("on", x === b));
    Object.keys(files).forEach(k => delete files[k]);
    renderSubjects();
    renderExams();
    updateGo();
  });
});

/* ── 시험 선택 드롭다운 ── */
const HP_MONTHS = [3, 4, 7, 10];   // 전국연합 학력평가 시행 월

function examMonth(examId){
  const m = /^\d{4}(\d{2})\d{2}\d$/.exec(examId || "");
  return m ? parseInt(m[1], 10) : null;
}

function updateCardBadge(){
  const el = $("#cardBadge");
  if (!el) return;
  const mo = examMonth($("#exam_select").value);
  if (grade === 3 && mo != null){
    const hp = HP_MONTHS.includes(mo);
    el.textContent = hp
      ? "🗂 전국연합 학력평가(교육청) 카드로 판독 — 수험번호·선택과목·탐구코드 자동 인식"
      : "🗂 대수능 모의평가(평가원) 카드로 판독";
    el.style.display = "block";
  } else el.style.display = "none";
}

function renderExams(keepId){
  const sel = $("#exam_select");
  const mine = EXAMS.filter(e => e.grade === grade);
  sel.innerHTML = "";
  if (!mine.length){
    sel.appendChild(new Option("등록된 시험 없음 — 아래에서 가져오기", ""));
  } else {
    mine.forEach(e => sel.appendChild(
      new Option(`${e.label} · ${e.subjects.length}과목 정답 준비됨`, e.exam_id)));
  }
  if (keepId && [...sel.options].some(o => o.value === keepId)) sel.value = keepId;
  updateCardBadge();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#exam_select").addEventListener("change", updateCardBadge);
});

/* 연/월 선택 초기화 */
(function(){
  const fy = $("#fy"), fm = $("#fm");
  const nowY = new Date().getFullYear();
  for (let y = nowY; y >= nowY - 2; y--) fy.appendChild(new Option(y + "년", y));
  [3,4,5,6,7,9,10,11].forEach(m => fm.appendChild(new Option(m + "월", m)));
  fm.value = "6";
})();

/* EBSi 자동 등록 */
$("#btnFetch").addEventListener("click", async () => {
  const btn = $("#btnFetch"), st = $("#fetchStatus");
  btn.disabled = true; st.classList.remove("err");
  st.textContent = "EBSi에서 시험을 찾는 중…";
  try{
    const data = new FormData();
    data.append("year", $("#fy").value);
    data.append("month", $("#fm").value);
    data.append("grade", grade);
    const res = await fetch("/api/exams/register", {method:"POST", body:data});
    const j = await safeJson(res);
    if (!j.ok) throw new Error(j.message || "요청 실패");
    // 등록 잡 폴링
    await new Promise((resolve, reject) => {
      const t = setInterval(async () => {
        try{
          const s = await (await fetch(j.status_url)).json();
          st.textContent = s.message || "진행 중…";
          if (s.status === "done" || s.status === "error"){
            clearInterval(t);
            if (s.exams) EXAMS = s.exams;
            const ir = s.result && s.result.irecord;
            renderExams(ir || undefined);
            if (s.ok) { st.textContent = "✓ " + (s.message || "등록 완료"); resolve(); }
            else reject(new Error(s.message || (s.result && s.result.errors && s.result.errors[0]) || "등록 실패"));
          }
        }catch(e){ /* 다음 폴링에서 재시도 */ }
      }, 1500);
    });
  }catch(err){
    st.classList.add("err");
    st.textContent = "✕ " + err.message;
  }finally{
    btn.disabled = false;
  }
});

/* ── 과목 그리드 렌더 ── */
function renderSubjects(){
  const specs = CATALOG[grade] || [];
  const pending = specs.filter(s => !s.available).map(s => s.label);
  const note = $("#gradeNote");
  if (pending.length){
    note.textContent = `고${grade} ${pending.join("·")} 판독 좌표는 첫 실물 스캔으로 보정한 뒤 활성화됩니다.`;
    note.classList.add("show");
  } else note.classList.remove("show");

  grid.innerHTML = "";
  specs.forEach(s => {
    const el = document.createElement("div");
    el.className = "subj" + (s.available ? "" : " off");
    el.innerHTML = `
      ${s.available ? "" : `<span class="badge">보정 대기</span>`}
      <div class="subj-top">
        <div class="subj-ic">${s.icon}</div>
        <div>
          <div class="subj-name">${s.label}</div>
          <div class="subj-state" data-state>${s.available ? "PDF 선택 또는 드래그" : "고" + grade + " 스캔 보정 후 사용 가능"}</div>
        </div>
      </div>
      <div class="file-chip">
        <span>📄</span><span class="nm" data-nm></span>
        <button type="button" class="rm" title="제거">✕</button>
      </div>
      <input type="file" accept=".pdf" multiple ${s.available ? "" : "disabled"}>
    `;
    const input = el.querySelector("input");
    const addFiles = list => {
      const pdfs = Array.from(list || []).filter(f => f && f.name.toLowerCase().endsWith(".pdf"));
      if (!pdfs.length) return;
      const cur = files[s.key] || [];
      // 이름+크기 중복 제거하며 누적(여러 번 나눠 선택 가능)
      const seen = new Set(cur.map(f => f.name + f.size));
      pdfs.forEach(f => { if (!seen.has(f.name + f.size)){ cur.push(f); seen.add(f.name + f.size); } });
      files[s.key] = cur;
      const mb = cur.reduce((a, f) => a + f.size, 0) / 1048576;
      const label = cur.length === 1 ? cur[0].name : `${cur.length}개 파일`;
      el.classList.add("has");
      el.querySelector("[data-nm]").textContent = `${label} (${mb.toFixed(1)}MB)`;
      el.querySelector("[data-state]").textContent = `업로드 준비 완료 · ${cur.length}개`;
      updateGo();
    };
    input.addEventListener("change", () => addFiles(input.files));
    // 카드 클릭 → 파일 열기 창 (제거 버튼 클릭은 제외)
    el.addEventListener("click", e => {
      if (e.target.closest(".rm")) return;
      if (s.available) input.click();
    });
    el.querySelector(".rm")?.addEventListener("click", e => {
      e.preventDefault(); e.stopPropagation();
      delete files[s.key]; input.value = "";
      el.classList.remove("has");
      el.querySelector("[data-state]").textContent = "PDF 선택 또는 드래그 (여러 개 가능)";
      updateGo();
    });
    if (s.available){
      el.querySelector("[data-state]").textContent = "PDF 선택 또는 드래그 (여러 개 가능)";
      ["dragover","dragenter"].forEach(ev => el.addEventListener(ev, e => {
        e.preventDefault(); el.classList.add("drag");
      }));
      ["dragleave","drop"].forEach(ev => el.addEventListener(ev, e => {
        e.preventDefault(); el.classList.remove("drag");
      }));
      el.addEventListener("drop", e => addFiles(e.dataTransfer.files));
    }
    grid.appendChild(el);
  });
}

/* ── 성명 CSV ── */
$("#namesDrop").addEventListener("click", () => $("#namesInput").click());
$("#namesInput").addEventListener("change", e => {
  const f = e.target.files[0];
  const d = $("#namesDrop");
  if (f){ $("#namesLabel").textContent = `✓ ${f.name}`; d.classList.add("has"); }
  else { $("#namesLabel").textContent = "📎 반·번호·성명 CSV 첨부 — 없으면 국어 판독 성명 사용"; d.classList.remove("has"); }
});

function updateGo(){
  const n = Object.keys(files).length;
  $("#btnGo").disabled = n === 0;
  const labels = (CATALOG[grade]||[]).filter(s => files[s.key]).map(s => s.label);
  $("#goSummary").textContent = n ? `${labels.join(" · ")} — ${n}과목 스캔` : "업로드된 과목 없음";
}

// 응답이 JSON 이 아니면(프록시 HTML 오류 등) 친절한 메시지로 변환
async function safeJson(res){
  const text = await res.text();
  try{
    return JSON.parse(text);
  }catch{
    const code = res.status || 0;
    if (code === 413) throw new Error("업로드 용량이 너무 큽니다. PDF 크기를 줄이거나 나눠서 올려주세요.");
    if (code === 502 || code === 504) throw new Error("서버 처리 시간이 초과됐습니다. 파일 수를 줄여 다시 시도해 주세요.");
    throw new Error(`서버 응답 오류(${code || "네트워크"}). 잠시 후 다시 시도해 주세요.`);
  }
}

function showAlert(msg){
  const a = $("#alert");
  a.textContent = msg; a.classList.add("show");
  window.scrollTo({top:0, behavior:"smooth"});
  setTimeout(() => a.classList.remove("show"), 6000);
}

/* ── 제출 & 폴링 ── */
// 파일을 한 개씩 작은 요청으로 올린다. 큰 요청은 느린 회선에서 프록시 타임아웃/
// 연결끊김("Failed to fetch")이 나므로, 작은 요청 + 자동 재시도로 안정화한다.
const FILE_LIMIT = 95 * 1024 * 1024;    // 단일 파일 상한(프록시 100MB 여유)
const MAX_RETRY = 4;

// 한 요청을 재시도와 함께 수행 (fetch 자체 실패=네트워크 끊김 포함)
async function postWithRetry(url, opts, what){
  let lastErr;
  for (let a = 1; a <= MAX_RETRY; a++){
    try{
      const r = await safeJson(await fetch(url, opts));
      if (!r.ok) throw new Error(r.message || (what + " 실패"));
      return r;
    }catch(err){
      lastErr = err;
      if (a < MAX_RETRY) await new Promise(res => setTimeout(res, 700 * a));
    }
  }
  throw new Error(`${what} 실패 (네트워크 확인 후 다시 시도): ${lastErr.message}`);
}

$("#form").addEventListener("submit", async e => {
  e.preventDefault();
  const items = [];
  for (const [k, arr] of Object.entries(files))
    (Array.isArray(arr) ? arr : [arr]).forEach(f => items.push({k, f}));
  const big = items.find(it => it.f.size > FILE_LIMIT);
  if (big){
    showAlert(`'${big.f.name}' (${(big.f.size/1048576).toFixed(0)}MB)이 너무 큽니다. 한 파일을 100MB 미만으로 나눠 저장해 올려주세요.`);
    return;
  }

  $("#btnGo").disabled = true;
  $("#btnGo").textContent = "업로드 준비 중…";
  try{
    // 1) 업로드 세션 생성
    const init = await postWithRetry("/api/score/init", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({grade, exam_id:$("#exam_select").value, exam_year:$("#exam_year").value})
    }, "업로드 준비");
    const rid = init.run_id;

    // 2) 성명 CSV
    const nf = $("#namesInput").files[0];
    if (nf){
      const d = new FormData(); d.append("names", nf);
      await postWithRetry(`/api/score/${rid}/file`, {method:"POST", body:d}, "명단 업로드");
    }

    // 3) PDF 파일을 한 개씩 업로드 (각 요청이 작아 느린 회선에서도 안정)
    let done = 0;
    for (const it of items){
      const d = new FormData(); d.append(`pdf_${it.k}`, it.f);
      await postWithRetry(`/api/score/${rid}/file`, {method:"POST", body:d},
                          `'${it.f.name}' 업로드`);
      done++;
      $("#btnGo").textContent = `업로드 중… ${done}/${items.length}`;
    }

    // 4) 채점 시작
    const st = await postWithRetry(`/api/score/${rid}/start`, {
      method:"POST", headers:{"Content-Type":"application/json"}, body:"{}"}, "채점 시작");
    $("#btnGo").textContent = "스캔 진행 중…";
    $("#panel-scan").style.display = "block";
    $("#panel-scan").scrollIntoView({behavior:"smooth"});
    poll(st.status_url);
  }catch(err){
    showAlert(err.message);
    $("#btnGo").disabled = false;
    $("#btnGo").textContent = "답안지 스캔 시작";
  }
});

function poll(url){
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try{
      const j = await (await fetch(url)).json();
      renderProgress(j);
      if (j.status === "done" || j.status === "error"){
        clearInterval(pollTimer);
        onScanDone(j);
      }
    }catch(e){ /* 서버 일시 오류 — 다음 폴링에서 재시도 */ }
  }, 1200);
}

function renderProgress(j){
  // 과목 단위 + 진행 중 과목들의 페이지 단위(subs)를 합쳐 부드러운 진행률
  let frac = j.completed || 0;
  const subs = Object.values(j.subs || {}).filter(s => s && s.total);
  subs.forEach(s => { frac += Math.min(1, s.done / s.total); });
  const pct = j.total ? Math.min(90, Math.round((frac / j.total) * 90))
                        + (j.status === "done" ? 10 : 0) : 0;
  $("#progFill").style.width = pct + "%";
  $("#progPct").textContent = pct + "%";
  $("#progText").textContent = subs.length
    ? subs.map(s => `${s.label} ${s.done}/${s.total}장`).join(" · ") + " 판독 중"
    : (j.message || "");
  const tl = $("#timeline");
  tl.innerHTML = (j.events || []).map(ev =>
    `<div class="ev ${ev.kind}"><span class="t">${ev.t}</span><span class="dot"></span><span>${ev.text}</span></div>`
  ).join("");
  tl.scrollTop = tl.scrollHeight;
}

// ── STEP 3 완료: 스캔 결과를 검토 뷰로 ──────────────
function onScanDone(j){
  $("#btnGo").disabled = false;
  $("#btnGo").textContent = "답안지 스캔 시작";
  SCAN_JOB = j;
  if (j.status === "error"){
    $("#scanHint").textContent = "스캔 오류";
    showAlert(j.message || "스캔 중 오류가 발생했습니다.");
    return;
  }
  $("#scanHint").textContent = "판독 완료 — 아래에서 검토하세요";
  if (j.run_id) loadReview(j.run_id);
}

// ── STEP 5: 다운로드 카드 ──────────────────────────
function renderDownloads(j){
  const combined = (j.downloads || []).find(d => d.kind === "combined");
  const others = (j.downloads || []).filter(d => d.kind !== "combined");
  $("#heroSlot").innerHTML = combined ? `
    <div class="hero-dl">
      <div class="ic">📊</div>
      <div>
        <div class="tt">${combined.title}</div>
        <div class="ds">대교협 가채점 · UNIV 양식(예상 등급·표점·백분위) · 과목별 정오표 · 예상등급컷 근거</div>
      </div>
      <a href="${combined.url}">엑셀 다운로드</a>
    </div>` : "";
  $("#dlList").innerHTML = others.map(d => `
    <a class="dl-item" href="${d.url}">
      <div class="ic">XLS</div>
      <div><div class="nm">${d.title}</div><div class="sz">${(d.size/1024).toFixed(0)} KB</div></div>
    </a>`).join("");
}

// ── 결과 확인·수정 뷰 ──────────────────────────────
let RESULT = null, RUN_ID = null, curSubject = null, expanded = new Set();
let SCAN_JOB = null, curSum = null, GRADED = false;
const ANS_LABEL = {0:"·", "-1":"중복", 1:"1", 2:"2", 3:"3", 4:"4", 5:"5"};

async function loadReview(runId){
  RUN_ID = runId;
  try{
    const r = await (await fetch(`/api/result/${runId}`)).json();
    if (!r.ok){ $("#reviewSlot").style.display = "none"; return; }
    RESULT = r; expanded = new Set();
    $("#rvStale").classList.toggle("show", !!r.stale);
    buildTabs();
    $("#reviewSlot").style.display = "block";
    $("#panel-grade").style.display = "block";     // STEP 4 노출
    $("#panel-scan").scrollIntoView({behavior:"smooth"});
  }catch(e){ $("#reviewSlot").style.display = "none"; }
}

function tabList(){
  const tabs = RESULT.subjects.slice();
  if (RESULT.has_tamgu){
    const seen = new Set();
    RESULT.students.forEach(s => (s.탐구||[]).forEach(t => {
      if (t.과목 && !tabs.includes(t.과목) && !seen.has(t.과목)){ seen.add(t.과목); tabs.push(t.과목); }
    }));
  }
  return tabs;
}

function subjData(stu, subj){
  if (stu.subjects && stu.subjects[subj]) return stu.subjects[subj];
  return (stu.탐구||[]).find(t => t.과목 === subj) || null;
}

function buildTabs(){
  const tabs = tabList();
  if (!curSubject || !tabs.includes(curSubject)) curSubject = tabs[0] || null;
  $("#rvTabs").innerHTML = tabs.map(t => {
    const n = RESULT.students.filter(s => subjData(s, t)).length;
    return `<button class="rv-tab ${t===curSubject?'on':''}" data-subj="${t}">${t}<span class="cnt">${n}</span></button>`;
  }).join("");
  $$("#rvTabs .rv-tab").forEach(b => b.addEventListener("click", () => {
    curSubject = b.dataset.subj; expanded = new Set(); buildTabs(); renderTable();
  }));
  renderTable();
}

function renderTable(){
  const q = ($("#rvSearch").value || "").trim().toLowerCase();
  const body = $("#rvBody");
  const rows = RESULT.students
    .map((s, idx) => ({s, idx}))
    .filter(({s}) => subjData(s, curSubject))
    .filter(({s}) => !q || (`${s.이름} ${s.반} ${s.번호}`).toLowerCase().includes(q));
  const header = `<tr>
    <th>반</th><th>번호</th><th>성명</th><th>판독 상태</th><th></th></tr>`;
  if (!rows.length){
    body.innerHTML = header + `<tr><td colspan="5"><div class="rv-empty">해당 학생이 없습니다.</div></td></tr>`;
    return;
  }
  body.innerHTML = header + rows.map(({s, idx}) => rowHtml(s, idx)).join("");
  // 이름·반·번호 편집
  $$("#rvBody .rv-cell-edit").forEach(inp => {
    inp.addEventListener("keydown", e => { if (e.key === "Enter"){ e.preventDefault(); inp.blur(); }});
    inp.addEventListener("change", () => saveField(+inp.dataset.idx, inp.dataset.field, inp.value));
  });
  // 답안 보기/수정 토글
  $$("#rvBody .rv-toggle").forEach(btn => btn.addEventListener("click", () => {
    const idx = +btn.dataset.idx;
    if (expanded.has(idx)) expanded.delete(idx); else expanded.add(idx);
    renderTable();
  }));
  bindGrid();
}

function readStatus(d){
  const flag = (d.확인 || "").toString().trim();
  if (flag) return {flag:true, html:`<span class="rv-flag-badge">⚠ ${esc(flag)}</span>`};
  let blank = 0, dup = 0;
  (d.cells || []).forEach(c => { if (c.답 === 0) blank++; else if (c.답 === -1) dup++; });
  if (blank || dup){
    const parts = [];
    if (blank) parts.push(`미마킹 ${blank}`);
    if (dup) parts.push(`중복 ${dup}`);
    return {flag:false, html:`<span class="rv-status warn">${parts.join(" · ")}</span>`};
  }
  return {flag:false, html:`<span class="rv-status ok">정상</span>`};
}

function rowHtml(s, idx){
  const d = subjData(s, curSubject);
  const stt = readStatus(d);
  const open = expanded.has(idx);
  let html = `<tr class="stu ${stt.flag?'flag':''}">
    <td><input class="rv-cell-edit sm" data-idx="${idx}" data-field="반" value="${esc(s.반)}"></td>
    <td><input class="rv-cell-edit sm" data-idx="${idx}" data-field="번호" value="${esc(s.번호)}"></td>
    <td><input class="rv-cell-edit nm" data-idx="${idx}" data-field="이름" value="${esc(s.이름)}"></td>
    <td>${stt.html}</td>
    <td style="text-align:right"><button class="rv-toggle ${open?'open':''}" data-idx="${idx}">답안 ${open?'닫기':'보기·수정'}</button></td>
  </tr>`;
  if (open) html += `<tr class="rv-detail"><td colspan="5"><div class="rv-detail-inner">
      <div class="rv-legend">
        <span><i class="rv-swatch" style="background:var(--success-soft);border:1px solid #B6E6D4"></i>정답</span>
        <span><i class="rv-swatch" style="background:var(--danger-soft);border:1px solid #F2C5C4"></i>오답(작은 숫자=정답)</span>
        <span><i class="rv-swatch" style="background:#F1F4F6"></i>미마킹</span>
        <span>큰 숫자 = 학생이 마킹한 답 · 눌러서 수정</span>
      </div>
      <div class="rv-grid">${d.cells.map(c => qHtml(idx, c)).join("")}</div>
    </div></td></tr>`;
  return html;
}

function qHtml(idx, c){
  const a = c.답;
  const cls = c.ok ? "ok" : (a === 0 || a === -1 ? "blank" : "wrong");
  const shown = ANS_LABEL[a] !== undefined ? ANS_LABEL[a] : (a ?? "·");
  const corr = !c.ok ? `<span class="qc">${c.정답 ?? ""}</span>` : `<span class="qc"></span>`;
  const opts = [["0","·"],["1","1"],["2","2"],["3","3"],["4","4"],["5","5"],["-1","중복"]]
    .map(([v,l]) => `<option value="${v}" ${String(a)===v?"selected":""}>${l}</option>`).join("");
  return `<div class="rv-q ${cls}">
    <span class="qn">${c.q}</span><span class="qa">${shown}</span>${corr}
    <select data-idx="${idx}" data-q="${c.q}">${opts}</select>
  </div>`;
}

function bindGrid(){
  $$("#rvBody .rv-q select").forEach(sel => sel.addEventListener("change", () =>
    saveAnswer(+sel.dataset.idx, +sel.dataset.q, +sel.value)));
}

async function postEdit(payload){
  const r = await (await fetch(`/api/result/${RUN_ID}/edit`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)
  })).json();
  if (!r.ok){ showAlert(r.message || "수정에 실패했습니다."); return null; }
  RESULT.students[payload.idx] = r.student;
  $("#rvStale").classList.add("show");
  return r.student;
}

async function saveField(idx, field, value){
  const st = await postEdit({idx, field, value});
  if (st){ buildTabs(); if (GRADED) renderSummary(); }   // 이름·반은 검색/카운트/요약 갱신
}

async function saveAnswer(idx, q, value){
  const st = await postEdit({idx, field:"answer", subject:curSubject, q, value});
  if (st){ renderTable(); if (GRADED) renderSummary(); }  // 판독상태·요약 즉시 반영(펼침 유지)
}

function esc(v){ return String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

$("#rvSearch").addEventListener("input", () => renderTable());

// ── STEP 4 → 5: 채점 ───────────────────────────────
$("#btnGrade").addEventListener("click", () => {
  if (!RESULT || !SCAN_JOB) return;
  GRADED = true;
  $("#gradeMsg").innerHTML = "✓ 채점 완료 — 아래 <b>5단계</b>에서 결과를 확인하고 성적표를 내려받으세요.";
  $("#btnGrade").textContent = "채점 다시 하기";
  renderDownloads(SCAN_JOB);
  $("#summarySlot").style.display = "block";
  renderSummary();
  $("#panel-result").style.display = "block";
  $("#panel-result").scrollIntoView({behavior:"smooth"});
});

// ── STEP 5: 채점 결과 요약(읽기 전용) ──────────────
function renderSummary(){
  const tabs = tabList();
  if (!curSum || !tabs.includes(curSum)) curSum = tabs[0] || null;
  $("#sumTabs").innerHTML = tabs.map(t => {
    const n = RESULT.students.filter(s => subjData(s, t)).length;
    return `<button class="rv-tab ${t===curSum?'on':''}" data-subj="${t}">${t}<span class="cnt">${n}</span></button>`;
  }).join("");
  $$("#sumTabs .rv-tab").forEach(b => b.addEventListener("click", () => {
    curSum = b.dataset.subj; renderSummary();
  }));
  const rows = RESULT.students.map(s => ({s, d:subjData(s, curSum)})).filter(x => x.d);
  const scores = rows.map(x => Number(x.d.원점수)).filter(v => !isNaN(v));
  const avg = scores.length ? (scores.reduce((a,b)=>a+b,0)/scores.length) : 0;
  const mx = scores.length ? Math.max(...scores) : 0;
  const mn = scores.length ? Math.min(...scores) : 0;
  $("#sumStats").innerHTML = `
    <div class="sum-stat"><div class="lb">응시</div><div class="vl">${rows.length}명</div></div>
    <div class="sum-stat"><div class="lb">평균</div><div class="vl">${avg.toFixed(1)}</div></div>
    <div class="sum-stat"><div class="lb">최고</div><div class="vl">${mx}</div></div>
    <div class="sum-stat"><div class="lb">최저</div><div class="vl">${mn}</div></div>`;
  const header = `<tr><th>반</th><th>번호</th><th>성명</th><th>원점수</th><th>예상등급</th></tr>`;
  $("#sumBody").innerHTML = header + rows.map(({s, d}) => {
    const gr = d.예상등급 ? `<span class="rv-grade">${d.예상등급}등급</span>` : `<span class="rv-grade none">–</span>`;
    return `<tr class="stu">
      <td>${esc(s.반)}</td><td>${esc(s.번호)}</td><td style="font-weight:700">${esc(s.이름)}</td>
      <td><span class="rv-score">${d.원점수}<span class="mx"> / ${d.만점}</span></span></td>
      <td>${gr}</td></tr>`;
  }).join("");
}

$("#btnReset").addEventListener("click", () => location.reload());

renderSubjects();
renderExams();
updateGo();
