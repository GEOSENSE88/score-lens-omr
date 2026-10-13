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
const CB_MONTHS = [8];             // 충북교육청 모의평가

function examMonth(examId){
  const m = /^\d{4}(\d{2})\d{2}\d$/.exec(examId || "");
  return m ? parseInt(m[1], 10) : null;
}

function updateCardBadge(){
  const el = $("#cardBadge");
  if (!el) return;
  const ex = EXAMS.find(e => e.exam_id === $("#exam_select").value);
  const editBtn = $("#btnCustomEdit");
  if (editBtn) editBtn.style.display = ex && ex.custom ? "" : "none";
  if (ex && ex.custom){
    const cardName = (CUSTOM_UI.cards[grade] || {})[ex.card] || ex.card;
    el.textContent = `🗂 사설 모의고사 — ${cardName} 카드로 판독 · 정답 입력 ${ex.subjects.length}과목` +
      (ex.subjects.length ? ` (${ex.subjects.join("·")})` : " — '정답 수정'에서 과목별로 입력하세요");
    el.style.display = "block";
    return;
  }
  const mo = examMonth($("#exam_select").value);
  if (grade === 3 && mo != null){
    el.textContent = HP_MONTHS.includes(mo)
      ? "🗂 전국연합 학력평가(교육청) 카드로 판독 — 수험번호·성명·선택과목·탐구코드 자동 인식"
      : CB_MONTHS.includes(mo)
      ? "🗂 충북교육청 모의평가 카드로 판독 — 현재 국어 보정 완료 (타 과목은 카드 스캔 후 활성화)"
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
  ORIGIN = null;                       // 새 판독은 새 보관 항목으로
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
    saveRunLocal();                                 // 브라우저 사본 (서버 만료 대비)
    if (!(SCAN_JOB && SCAN_JOB.restored)) prefetchCards();
  }catch(e){ $("#reviewSlot").style.display = "none"; }
}

// 탐구 탭 순서: 고1·2 통합과목은 사회→과학 고정, 그 외(고3 선택과목)는 등장 순
const TAMGU_ORDER = ["통합사회", "통합과학"];

function tabList(){
  const tabs = RESULT.subjects.slice();
  if (RESULT.has_tamgu){
    const seen = new Set();
    const extra = [];
    RESULT.students.forEach(s => (s.탐구||[]).forEach(t => {
      if (t.과목 && !tabs.includes(t.과목) && !seen.has(t.과목)){ seen.add(t.과목); extra.push(t.과목); }
    }));
    const rank = t => { const i = TAMGU_ORDER.indexOf(t); return i < 0 ? TAMGU_ORDER.length : i; };
    extra.sort((a, b) => rank(a) - rank(b));   // 안정 정렬 — 나머지는 등장 순 유지
    tabs.push(...extra);
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
  // 플래그 행: 스캔 원본 카드 보기
  $$("#rvBody .rv-card").forEach(btn => btn.addEventListener("click", () =>
    showCard(curSubject, +btn.dataset.page)));
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
  const cardBtn = d.페이지
    ? `<button class="rv-card ${stt.flag?'':'quiet'}" data-page="${d.페이지}" title="스캔 원본을 보며 답안·수험번호·성명을 대조하세요">🔍 카드 보기</button> `
    : "";
  let html = `<tr class="stu ${stt.flag?'flag':''}">
    <td><input class="rv-cell-edit sm" data-idx="${idx}" data-field="반" value="${esc(s.반)}"></td>
    <td><input class="rv-cell-edit sm" data-idx="${idx}" data-field="번호" value="${esc(s.번호)}"></td>
    <td><input class="rv-cell-edit nm" data-idx="${idx}" data-field="이름" value="${esc(s.이름)}"></td>
    <td>${stt.html}</td>
    <td style="text-align:right;white-space:nowrap">${cardBtn}<button class="rv-toggle ${open?'open':''}" data-idx="${idx}">답안 ${open?'닫기':'보기·수정'}</button></td>
  </tr>`;
  if (open){
    const noKey = d.cells.length > 0 &&
      d.cells.every(c => c.정답 === null || c.정답 === undefined);
    const legend = noKey
      ? `<span>📋 <b>정답 공개 전</b> — 학생이 마킹한 답만 표시합니다. 정답 등록 후 채점하면 정답·오답이 색으로 표시됩니다.</span>
         <span>눌러서 수정 가능</span>`
      : `<span><i class="rv-swatch" style="background:var(--success-soft);border:1px solid #B6E6D4"></i>정답</span>
         <span><i class="rv-swatch" style="background:var(--danger-soft);border:1px solid #F2C5C4"></i>오답(작은 숫자=정답)</span>
         <span><i class="rv-swatch" style="background:#F1F4F6"></i>미마킹</span>
         <span>큰 숫자 = 학생이 마킹한 답 · 눌러서 수정</span>`;
    html += `<tr class="rv-detail"><td colspan="5"><div class="rv-detail-inner">
      <div class="rv-legend">${legend}</div>
      <div class="rv-grid">${d.cells.map(c => qHtml(idx, c)).join("")}</div>
    </div></td></tr>`;
  }
  return html;
}

// 수학 단답형(16~22·29~30)은 0~999 — 드롭다운 대신 직접 입력
function isShortAns(q){
  return curSubject === "수학" && ((q >= 16 && q <= 22) || q === 29 || q === 30);
}

function qHtml(idx, c){
  const a = c.답;
  const noKey = c.정답 === null || c.정답 === undefined;   // 정답 공개 전 — 판독표만
  const cls = noKey ? (a === 0 || a === -1 ? "blank" : "pending")
                    : (c.ok ? "ok" : (a === 0 || a === -1 ? "blank" : "wrong"));
  const shown = ANS_LABEL[a] !== undefined ? ANS_LABEL[a] : (a ?? "·");
  const corr = (!c.ok && !noKey) ? `<span class="qc">${c.정답 ?? ""}</span>` : `<span class="qc"></span>`;
  if (isShortAns(c.q))
    return `<div class="rv-q ${cls} sa" data-idx="${idx}" data-q="${c.q}" title="눌러서 0~999 직접 입력">
      <span class="qn">${c.q}</span><span class="qa">${shown}</span>${corr}
    </div>`;
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
  $$("#rvBody .rv-q.sa").forEach(cell => cell.addEventListener("click", () => {
    if (cell.querySelector("input")) return;
    const idx = +cell.dataset.idx, q = +cell.dataset.q;
    const qa = cell.querySelector(".qa");
    const cur = qa.textContent.trim();
    const inp = document.createElement("input");
    inp.className = "sa-edit";
    inp.type = "text"; inp.inputMode = "numeric"; inp.maxLength = 3;
    inp.value = /^\d+$/.test(cur) ? cur : "";
    inp.placeholder = "·";
    qa.style.display = "none";
    cell.appendChild(inp);
    inp.focus(); inp.select();
    let done = false;
    const finish = save => {
      if (done) return; done = true;
      const raw = inp.value.trim();
      inp.remove(); qa.style.display = "";
      if (!save) return;
      if (raw === ""){ saveAnswer(idx, q, 0); return; }     // 빈칸 = 미마킹
      if (!/^\d{1,3}$/.test(raw)){ showAlert("단답형은 0~999 숫자만 입력할 수 있습니다."); return; }
      saveAnswer(idx, q, parseInt(raw, 10));
    };
    inp.addEventListener("keydown", e => {
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") finish(false);
    });
    inp.addEventListener("blur", () => finish(true));
  }));
}

async function postEdit(payload){
  const r = await (await fetch(`/api/result/${RUN_ID}/edit`, {
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)
  })).json();
  if (!r.ok){ showAlert(r.message || "수정에 실패했습니다."); return null; }
  RESULT.students[payload.idx] = r.student;
  $("#rvStale").classList.add("show");
  scheduleSaveRunLocal();
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

/* ── 스캔 원본 카드 모달 (플래그 행 검토용) ── */
const SUBJ_KEY = {국어:"korean", 수학:"math", 영어:"english", 한국사:"history",
                  통합사회:"social", 통합과학:"science"};

function ensureCardModal(){
  let ov = $("#cardModal");
  if (ov) return ov;
  ov = document.createElement("div");
  ov.id = "cardModal";
  ov.innerHTML = `
    <div class="cm-box">
      <div class="cm-head">
        <span class="cm-title"></span>
        <span class="cm-hint">이미지를 보며 표의 반·번호·성명 칸을 직접 수정하세요 (클릭=확대)</span>
        <button class="cm-close" title="닫기">✕</button>
      </div>
      <div class="cm-body"><img alt="답안지 스캔"></div>
    </div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", e => { if (e.target === ov) ov.style.display = "none"; });
  ov.querySelector(".cm-close").addEventListener("click", () => ov.style.display = "none");
  const img = ov.querySelector("img");
  img.addEventListener("click", () => img.classList.toggle("zoom"));
  img.addEventListener("error", () => {
    ov.querySelector(".cm-title").textContent = "이미지를 찾을 수 없습니다 (결과가 만료됐을 수 있음)";
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") ov.style.display = "none"; });
  return ov;
}

async function showCard(subjLabel, page){
  if (!RUN_ID || !page) return;
  const key = SUBJ_KEY[subjLabel] || "explore";   // 탐구 과목명 탭 → explore 카드
  const ov = ensureCardModal();
  ov.querySelector(".cm-title").textContent = `${subjLabel} 답안지 ${page}쪽 — 스캔 원본`;
  const img = ov.querySelector("img");
  img.classList.remove("zoom");
  img.removeAttribute("src");
  ov.style.display = "flex";
  // 1) 서버 이미지 → 2) 브라우저 보관본(IndexedDB) → 3) 안내
  const url = `/review_img/${RUN_ID}/${key}/${page}`;
  let blob = null;
  try{
    const res = await fetch(url);
    if (res.ok){ blob = await res.blob(); LocalStore.putCard(cardKey(key, page), blob).catch(()=>{}); }
  }catch(e){ /* 서버 불가 */ }
  if (!blob) blob = await LocalStore.getCard(cardKey(key, page)).catch(() => null);
  if (blob){ img.src = URL.createObjectURL(blob); }
  else ov.querySelector(".cm-title").textContent =
    "이미지를 찾을 수 없습니다 — 서버에서 만료됐고 브라우저에도 저장되지 않은 쪽입니다";
}


/* ── 브라우저 보관(IndexedDB): 판독 결과 사본 + 확인필요 카드 이미지 ──
   서버는 TTL·재시작·다운로드 후 삭제로 결과를 잃지만, 이 브라우저에 남긴 사본으로
   언제든 복원(/api/result/restore)해 검토·수정·다운로드를 이어간다. */
const LocalStore = (() => {
  const NAME = "omrlens", VER = 1;
  let dbp = null;
  function open(){
    if (dbp) return dbp;
    dbp = new Promise((res, rej) => {
      if (!window.indexedDB){ rej(new Error("no idb")); return; }
      const rq = indexedDB.open(NAME, VER);
      rq.onupgradeneeded = () => {
        const db = rq.result;
        if (!db.objectStoreNames.contains("runs")) db.createObjectStore("runs", {keyPath:"origin"});
        if (!db.objectStoreNames.contains("cards")) db.createObjectStore("cards");
      };
      rq.onsuccess = () => res(rq.result);
      rq.onerror = () => rej(rq.error);
    });
    return dbp;
  }
  function tx(store, mode, fn){
    return open().then(db => new Promise((res, rej) => {
      const t = db.transaction(store, mode), st = t.objectStore(store);
      const out = fn(st);
      t.oncomplete = () => res(out && out.result !== undefined ? out.result : undefined);
      t.onerror = () => rej(t.error);
    }));
  }
  return {
    putRun: rec => tx("runs", "readwrite", st => st.put(rec)),
    getRun: origin => tx("runs", "readonly", st => st.get(origin)),
    delRun: origin => tx("runs", "readwrite", st => st.delete(origin)),
    listRuns: () => tx("runs", "readonly", st => st.getAll()),
    putCard: (k, blob) => tx("cards", "readwrite", st => st.put(blob, k)),
    getCard: k => tx("cards", "readonly", st => st.get(k)),
    delCards: origin => tx("cards", "readwrite", st => {
      const rq = st.openCursor();
      rq.onsuccess = () => { const c = rq.result; if (!c) return;
        if (String(c.key).startsWith(origin + "/")) c.delete(); c.continue(); };
      return null;
    }),
  };
})();

let ORIGIN = null;            // 브라우저 보관 키(최초 판독 run_id) — 복원해도 유지
const cardKey = (key, page) => `${ORIGIN || RUN_ID}/${key}/${page}`;
let saveTimer = null;
function scheduleSaveRunLocal(){ clearTimeout(saveTimer); saveTimer = setTimeout(saveRunLocal, 1500); }

async function saveRunLocal(){
  if (!RUN_ID) return;
  try{
    const ex = await (await fetch(`/api/result/${RUN_ID}/export`)).json();
    if (!ex.ok) return;
    ORIGIN = ORIGIN || RUN_ID;
    const sel = $("#exam_select");
    const prev = await LocalStore.getRun(ORIGIN).catch(() => null);
    const examLabel = (prev && prev.exam_label) ||
      (sel && sel.selectedOptions[0] ? sel.selectedOptions[0].textContent.split(" · ")[0] : "") || "";
    await LocalStore.putRun({
      origin: ORIGIN, exam_label: examLabel, grade: ex.grade, exam_id: ex.exam_id,
      exam_year: ex.exam_year, students: ex.students, n: ex.students.length,
      created_at: (prev && prev.created_at) || Date.now(), saved_at: Date.now(),
      cards: (prev && prev.cards) || 0,
    });
    renderSavedRuns();
  }catch(e){ /* 저장 실패는 조용히 — 서버 흐름엔 영향 없음 */ }
}

/* 확인필요 행(+미마킹·중복 행)의 카드 이미지를 미리 받아 둔다 — 서버 만료 대비.
   전 페이지(수백 MB)는 과하므로 검토가 필요한 쪽만, 최대 CARD_CAP 장. */
const CARD_CAP = 400;
async function prefetchCards(){
  if (!RESULT || !RUN_ID) return;
  const want = [];
  const seen = new Set();
  RESULT.students.forEach(s => {
    const all = Object.entries(s.subjects || {})
      .concat((s.탐구 || []).map(t => [t.과목, t]));
    all.forEach(([label, d]) => {
      if (!d || !d.페이지) return;
      const st = readStatus(d);
      const need = st.flag || /warn/.test(st.html);
      const key = SUBJ_KEY[label] || "explore";
      const k = cardKey(key, d.페이지);
      if (need && !seen.has(k)){ seen.add(k); want.push([k, `/review_img/${RUN_ID}/${key}/${d.페이지}`]); }
    });
  });
  let done = 0;
  for (const [k, url] of want.slice(0, CARD_CAP)){
    try{
      if (await LocalStore.getCard(k)) { done++; continue; }
      const res = await fetch(url);
      if (res.ok){ await LocalStore.putCard(k, await res.blob()); done++; }
    }catch(e){ /* skip */ }
  }
  try{
    const rec = await LocalStore.getRun(ORIGIN || RUN_ID);
    if (rec){ rec.cards = done; await LocalStore.putRun(rec); renderSavedRuns(); }
  }catch(e){}
}

function fmtWhen(ts){
  const d = new Date(ts);
  return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
}

async function renderSavedRuns(){
  const panel = $("#panel-saved"), list = $("#savedList");
  if (!panel || !list) return;
  let runs = [];
  try{ runs = await LocalStore.listRuns(); }catch(e){ runs = []; }
  runs.sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0));
  if (!runs.length){ panel.style.display = "none"; return; }
  panel.style.display = "block";
  list.innerHTML = runs.map(r => `
    <div class="saved-item" data-origin="${esc(r.origin)}">
      <div class="ic">고${r.grade}</div>
      <div>
        <div class="nm">${esc(r.exam_label || (r.exam_id ? "시험 " + r.exam_id : "시험 미지정"))} · 학생 ${r.n}명</div>
        <div class="ds">판독 ${fmtWhen(r.created_at)} · 마지막 저장 ${fmtWhen(r.saved_at)} · 보관 카드 이미지 ${r.cards || 0}장${ORIGIN === r.origin ? " · <b>현재 열림</b>" : ""}</div>
      </div>
      <div class="sp"></div>
      <button type="button" class="pri" data-act="load" ${ORIGIN === r.origin ? "disabled" : ""}>불러오기</button>
      <button type="button" data-act="del">삭제</button>
    </div>`).join("") +
    `<div class="saved-note">💡 결과는 이 PC의 이 브라우저에만 저장됩니다. 브라우저 데이터를 지우면 함께 사라지니 통합성적표 엑셀은 꼭 내려받아 두세요.</div>`;
  $$("#savedList .saved-item button").forEach(b => b.addEventListener("click", async () => {
    const origin = b.closest(".saved-item").dataset.origin;
    if (b.dataset.act === "del"){
      if (!confirm("이 브라우저에 보관된 판독 결과를 삭제할까요? (서버와 무관, 되돌릴 수 없음)")) return;
      await LocalStore.delRun(origin).catch(()=>{});
      await LocalStore.delCards(origin).catch(()=>{});
      if (ORIGIN === origin) ORIGIN = null;
      renderSavedRuns();
      return;
    }
    b.disabled = true; b.textContent = "복원 중…";
    try{ await restoreRun(origin); }
    finally{ b.disabled = false; b.textContent = "불러오기"; }
  }));
}

async function restoreRun(origin){
  const rec = await LocalStore.getRun(origin);
  if (!rec){ showAlert("보관된 결과를 찾을 수 없습니다."); return; }
  const r = await (await fetch("/api/result/restore", {
    method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({grade: rec.grade, exam_id: rec.exam_id, exam_year: rec.exam_year, students: rec.students}),
  })).json();
  if (!r.ok){ showAlert(r.message || "복원에 실패했습니다."); return; }
  ORIGIN = origin;
  SCAN_JOB = r.job;
  GRADED = false;
  $("#gradeMsg").innerHTML = "위 판독 결과를 확인·수정한 뒤 채점하면 원점수·예상등급이 산출됩니다.";
  $("#btnGrade").textContent = "채점하기";
  $("#summarySlot").style.display = "none";
  $("#panel-result").style.display = "none";
  $("#panel-scan").style.display = "block";
  renderProgress(r.job);
  $("#scanHint").textContent = "브라우저 보관 결과 복원됨 — 아래에서 검토하세요";
  await loadReview(r.run_id);
}

document.addEventListener("DOMContentLoaded", renderSavedRuns);

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

/* ══ 사설 모의고사: 정답·배점 직접 입력 ══
   과목별로 1..n 정답·배점을 입력해 서버 keys/ 에 EBSi 키와 같은 형식으로 저장한다.
   선택과목 과목(고3 국어·수학)은 공통 문항을 한 번만 입력하고 선택과목 문항만 따로. */
const CE = {open:false, examId:null, grade:3, subj:null, el:null, expl:null, data:{}, saved:new Set()};

function ceLayouts(){ return CUSTOM_UI.layouts[CE.grade] || {}; }
function ceTabs(){
  const L = ceLayouts();
  const main = Object.keys(L).filter(s => !L[s].explore);
  return CE.grade === 3 ? [...main, "탐구"] : main;
}
function ceExplList(){ const L = ceLayouts(); return Object.keys(L).filter(s => L[s].explore); }
function ceSubj(){ return CE.subj === "탐구" ? CE.expl : CE.subj; }
function ceLay(){ return ceLayouts()[ceSubj()]; }

// 과목 상태: {common:{answers,points}, els:{선택과목:{answers,points}}} 또는 {answers,points}
function ceState(subj){
  const lay = ceLayouts()[subj];
  if (!CE.data[subj]){
    const blankPts = () => Object.fromEntries(Object.entries(lay.pts));
    CE.data[subj] = lay.electives
      ? {common:{answers:{}, points:blankPts()},
         els:Object.fromEntries(lay.electives.map(e => [e, {answers:{}, points:blankPts()}]))}
      : {answers:{}, points:blankPts()};
  }
  return CE.data[subj];
}
// 현재 화면 문항 q 의 저장 위치
function ceSlot(q){
  const lay = ceLay(), st = ceState(ceSubj());
  if (!lay.electives) return st;
  return lay.common.includes(q) ? st.common : st.els[CE.el];
}
function ceVisibleQs(){
  const lay = ceLay(); return Array.from({length:lay.n}, (_, i) => i + 1);
}

function ensureCustomModal(){
  let ov = $("#customModal");
  if (ov) return ov;
  ov = document.createElement("div");
  ov.id = "customModal";
  ov.innerHTML = `
    <div class="ce-box" role="dialog" aria-label="사설 모의고사 정답 입력">
      <div class="ce-head">
        <span class="ce-title">사설 모의고사 · 정답·배점 직접 입력</span>
        <button type="button" class="ce-close" title="닫기">✕</button>
      </div>
      <div class="ce-body">
        <div class="ce-meta">
          <label class="ce-f wide"><span>시험 이름</span><input type="text" id="ceName" maxlength="40" placeholder="예: ○○ 사설 모의고사 3회"></label>
          <label class="ce-f"><span>시행 연·월</span><span class="ce-ym"><select id="ceY"></select><select id="ceM"></select></span></label>
          <label class="ce-f"><span>OMR 카드</span><select id="ceCard"></select></label>
          <div class="ce-f"><span>학년</span><b id="ceGrade"></b></div>
        </div>
        <div class="ce-cardnote">학생들이 실제로 마킹한 <b>OMR 답안지 종류</b>를 고르세요. 사설 시험지는 대부분 평가원·수능형 카드를 씁니다.</div>
        <div class="ce-tabs" id="ceTabs"></div>
        <div class="ce-sub" id="ceSub"></div>
        <div class="ce-quick">
          <label>정답 한 번에 입력 <input type="text" id="ceQuickA" placeholder="예: 41452 21312 … (객관식은 숫자를 이어서, 단답형은 띄어서)"></label>
          <button type="button" id="ceQuickAGo">채우기</button>
          <label>배점 한 번에 입력 <input type="text" id="ceQuickP" placeholder="예: 22322 … 또는 2 2 3 1.5"></label>
          <button type="button" id="ceQuickPGo">채우기</button>
        </div>
        <div class="ce-grid" id="ceGrid"></div>
        <div class="ce-status" id="ceStatus"></div>
      </div>
      <div class="ce-foot">
        <span class="ce-total" id="ceTotal"></span>
        <button type="button" class="ce-del-exam" id="ceDelExam">시험 삭제</button>
        <button type="button" class="ce-del" id="ceDel">이 과목 삭제</button>
        <button type="button" class="ce-save" id="ceSave">이 과목 저장</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  const close = () => { ov.style.display = "none"; CE.open = false; };
  ov.querySelector(".ce-close").addEventListener("click", close);
  ov.addEventListener("mousedown", e => { if (e.target === ov) close(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && CE.open) close(); });
  const ny = new Date().getFullYear();
  for (let y = ny + 1; y >= ny - 2; y--) $("#ceY").appendChild(new Option(y + "년", y));
  for (let m = 1; m <= 12; m++) $("#ceM").appendChild(new Option(m + "월", m));
  $("#ceQuickAGo").addEventListener("click", () => ceQuick("answers"));
  $("#ceQuickPGo").addEventListener("click", () => ceQuick("points"));
  $("#ceQuickA").addEventListener("keydown", e => { if (e.key === "Enter"){ e.preventDefault(); ceQuick("answers"); }});
  $("#ceQuickP").addEventListener("keydown", e => { if (e.key === "Enter"){ e.preventDefault(); ceQuick("points"); }});
  $("#ceSave").addEventListener("click", ceSave);
  $("#ceDel").addEventListener("click", ceDeleteSubject);
  $("#ceDelExam").addEventListener("click", ceDeleteExam);
  return ov;
}

async function openCustomEditor(examId){
  const ov = ensureCustomModal();
  Object.assign(CE, {open:true, examId:examId || null, grade, data:{}, saved:new Set(),
                     subj:null, el:null, expl:null});
  $("#ceGrade").textContent = "고" + grade;
  const cs = $("#ceCard"); cs.innerHTML = "";
  Object.entries(CUSTOM_UI.cards[grade]).forEach(([k, v]) => cs.appendChild(new Option(v, k)));
  cs.disabled = grade !== 3;
  $("#ceName").value = "";
  $("#ceY").value = new Date().getFullYear();
  $("#ceM").value = new Date().getMonth() + 1;
  ceStatus("");
  if (examId){
    try{
      const j = await safeJson(await fetch(`/api/exams/custom/${examId}`));
      if (!j.ok) throw new Error(j.message);
      $("#ceName").value = j.meta.name || "";
      $("#ceY").value = j.meta.year; $("#ceM").value = j.meta.month; cs.value = j.meta.card;
      Object.entries(j.subjects).forEach(([subj, byEl]) => ceLoadSubject(subj, byEl));
    }catch(err){ ceStatus("불러오기 실패: " + err.message, true); }
  }
  // 연·월은 시험 ID 에 들어가므로 만든 뒤에는 바꿀 수 없다
  $("#ceY").disabled = $("#ceM").disabled = !!examId;
  CE.subj = ceTabs()[0];
  CE.expl = ceExplList().find(s => CE.saved.has(s)) || ceExplList()[0] || null;
  ceRender();
  ov.style.display = "flex";
  $("#ceName").focus();
}

function ceLoadSubject(subj, byEl){
  const lay = ceLayouts()[subj];
  if (!lay) return;
  const st = ceState(subj);
  const pick = (src, qs) => ({
    answers:Object.fromEntries(qs.filter(q => src.answers[q] != null).map(q => [q, String(src.answers[q])])),
    points:Object.fromEntries(qs.filter(q => src.points[q] != null).map(q => [q, String(src.points[q])])),
  });
  if (lay.electives){
    const all = Array.from({length:lay.n}, (_, i) => i + 1);
    const elQs = all.filter(q => !lay.common.includes(q));
    Object.entries(byEl).forEach(([el, src]) => {
      if (!st.els[el]) return;
      Object.assign(st.els[el].answers, pick(src, elQs).answers);
      Object.assign(st.els[el].points, pick(src, elQs).points);
      Object.assign(st.common.answers, pick(src, lay.common).answers);
      Object.assign(st.common.points, pick(src, lay.common).points);
      CE.saved.add(subj + "|" + el);
    });
  } else {
    const src = byEl[""] || Object.values(byEl)[0];
    if (!src) return;
    st.answers = Object.fromEntries(Object.entries(src.answers).map(([q, v]) => [q, String(v)]));
    st.points = Object.fromEntries(Object.entries(src.points).map(([q, v]) => [q, String(v)]));
  }
  CE.saved.add(subj);
}

function ceRender(){
  // 과목 탭
  $("#ceTabs").innerHTML = ceTabs().map(t => {
    const done = t === "탐구" ? ceExplList().some(s => CE.saved.has(s)) : CE.saved.has(t);
    return `<button type="button" class="${t === CE.subj ? "on" : ""}" data-t="${esc(t)}">${esc(t)}${done ? " ✓" : ""}</button>`;
  }).join("");
  $$("#ceTabs button").forEach(b => b.addEventListener("click", () => {
    CE.subj = b.dataset.t; CE.el = null; ceRender();
  }));
  // 선택과목 / 탐구 과목 선택
  const sub = $("#ceSub");
  if (CE.subj === "탐구"){
    sub.innerHTML = `<label>탐구 과목 <select id="ceExpl">${ceExplList().map(s =>
      `<option value="${esc(s)}" ${s === CE.expl ? "selected" : ""}>${esc(s)}${CE.saved.has(s) ? " ✓ 입력됨" : ""}</option>`).join("")}</select></label>
      <span class="ce-hint">시험을 본 과목만 하나씩 골라 입력·저장하세요.</span>`;
    $("#ceExpl").addEventListener("change", e => { CE.expl = e.target.value; ceRender(); });
  } else {
    const lay = ceLay();
    if (lay.electives){
      if (!CE.el) CE.el = lay.electives.find(e => CE.saved.has(CE.subj + "|" + e)) || lay.electives[0];
      sub.innerHTML = `<span class="ce-hint">공통 ${lay.common[0]}~${lay.common[lay.common.length - 1]}번은 한 번만 입력 · 선택과목:</span>
        ${lay.electives.map(e => `<button type="button" class="ce-el ${e === CE.el ? "on" : ""}" data-e="${esc(e)}">${esc(e)}${CE.saved.has(CE.subj + "|" + e) ? " ✓" : ""}</button>`).join("")}
        <span class="ce-hint">· 입력한 선택과목만 저장됩니다</span>`;
      $$("#ceSub .ce-el").forEach(b => b.addEventListener("click", () => { CE.el = b.dataset.e; ceRender(); }));
    } else sub.innerHTML = "";
  }
  ceRenderGrid();
}

function ceRenderGrid(){
  const lay = ceLay(), short = new Set(lay.short);
  const grid = $("#ceGrid");
  let html = "", lastSec = null;
  ceVisibleQs().forEach(q => {
    if (lay.electives){
      const sec = lay.common.includes(q) ? "공통" : CE.el;
      if (sec !== lastSec){ html += `<div class="ce-sec">${esc(sec)}</div>`; lastSec = sec; }
    }
    const slot = ceSlot(q);
    const a = slot.answers[q] ?? "", p = slot.points[q] ?? "";
    html += `<div class="ce-q ${short.has(q) ? "short" : ""}" data-q="${q}">
      <span class="n">${q}</span>
      <input class="a" inputmode="numeric" maxlength="${short.has(q) ? 3 : 1}" value="${esc(a)}"
             placeholder="${short.has(q) ? "단답" : "답"}" aria-label="${q}번 정답">
      <input class="p" inputmode="decimal" maxlength="4" value="${esc(p)}" aria-label="${q}번 배점">
    </div>`;
  });
  grid.innerHTML = html;
  grid.querySelectorAll(".ce-q").forEach(cell => {
    const q = +cell.dataset.q, ia = cell.querySelector(".a"), ip = cell.querySelector(".p");
    ia.addEventListener("input", () => {
      ia.value = ia.value.replace(/\D/g, "");
      if (!short.has(q)) ia.value = ia.value.replace(/[^1-5]/g, "").slice(-1);
      ceSlot(q).answers[q] = ia.value;
      cell.classList.toggle("bad", ia.value === "");
      if (!short.has(q) && ia.value){         // 객관식: 한 자리 입력하면 다음 문항으로
        const all = Array.from(grid.querySelectorAll(".ce-q .a"));
        const next = all[all.indexOf(ia) + 1];
        if (next) next.focus(), next.select();
      }
    });
    ia.addEventListener("keydown", e => {
      if (e.key === "Enter"){ e.preventDefault();
        const all = Array.from(grid.querySelectorAll(".ce-q .a")); const n = all[all.indexOf(ia) + 1]; if (n) n.focus(); }
    });
    ip.addEventListener("input", () => {
      ip.value = ip.value.replace(/[^\d.]/g, "");
      ceSlot(q).points[q] = ip.value; ceUpdateTotal();
    });
  });
  ceUpdateTotal();
  $("#ceDel").style.display = CE.saved.has(ceSubj()) ? "" : "none";
  $("#ceDelExam").style.display = CE.examId ? "" : "none";
}

function ceTotalFor(el){
  const lay = ceLay(), st = ceState(ceSubj());
  const src = q => lay.electives ? (lay.common.includes(q) ? st.common : st.els[el]) : st;
  return ceVisibleQs().reduce((s, q) => s + (parseFloat(src(q).points[q]) || 0), 0);
}
function ceUpdateTotal(){
  const lay = ceLay(), tot = Math.round(ceTotalFor(CE.el) * 100) / 100;
  const el = $("#ceTotal");
  el.textContent = `배점 합계 ${tot} / ${lay.max}점` + (lay.electives ? ` (${CE.el})` : "");
  el.classList.toggle("warn", tot !== lay.max);
}

// "41452 6 11 38" → 객관식은 숫자 묶음을 한 자리씩, 단답형은 토큰 그대로
function ceQuick(kind){
  const inp = kind === "answers" ? $("#ceQuickA") : $("#ceQuickP");
  const tokens = inp.value.trim().split(/[\s,;/]+/).filter(Boolean);
  if (!tokens.length) return;
  const lay = ceLay(), short = new Set(lay.short);
  const qs = ceVisibleQs();
  // 시작 문항: 커서가 있던 칸이 없으면 첫 빈칸부터
  let i = 0;
  if (kind === "answers"){ const f = qs.findIndex(q => !ceSlot(q).answers[q]); i = f < 0 ? 0 : f; }
  for (const t of tokens){
    if (i >= qs.length) break;
    const spread = kind === "answers" ? !short.has(qs[i]) && /^[1-5]{2,}$/.test(t)
                                      : /^\d{2,}$/.test(t);
    const vals = spread ? t.split("") : [t];
    for (const v of vals){
      if (i >= qs.length) break;
      const q = qs[i];
      if (kind === "answers" && !short.has(q) && !/^[1-5]$/.test(v)){ i++; continue; }
      ceSlot(q)[kind][q] = v; i++;
    }
  }
  inp.value = "";
  ceRenderGrid();
}

function ceStatus(msg, err){
  const s = $("#ceStatus"); if (!s) return;
  s.textContent = msg; s.classList.toggle("err", !!err);
}

function ceBuildParts(){
  const lay = ceLay(), subj = ceSubj(), st = ceState(subj);
  const all = Array.from({length:lay.n}, (_, i) => i + 1);
  const missing = (src, qs) => qs.filter(q => !src.answers[q] || !src.points[q]);
  if (!lay.electives){
    const m = missing(st, all);
    if (m.length) throw new Error(`${m.slice(0, 8).join("·")}${m.length > 8 ? "…" : ""}번 정답·배점을 입력하세요.`);
    return [{elective:null, answers:st.answers, points:st.points}];
  }
  const mc = missing(st.common, lay.common);
  if (mc.length) throw new Error(`공통 ${mc.slice(0, 8).join("·")}${mc.length > 8 ? "…" : ""}번 정답·배점을 입력하세요.`);
  const elQs = all.filter(q => !lay.common.includes(q));
  const parts = [];
  for (const e of lay.electives){
    const src = st.els[e];
    const filled = elQs.filter(q => src.answers[q]).length;
    if (!filled) continue;                       // 입력 안 한 선택과목은 건너뜀
    const m = missing(src, elQs);
    if (m.length) throw new Error(`${e} ${m.slice(0, 8).join("·")}번 정답·배점을 입력하세요.`);
    parts.push({elective:e, answers:{...st.common.answers, ...src.answers},
                points:{...st.common.points, ...src.points}});
  }
  if (!parts.length) throw new Error("선택과목 문항 정답을 하나 이상 입력하세요.");
  return parts;
}

async function ceSave(){
  const name = $("#ceName").value.trim();
  if (!name){ ceStatus("시험 이름을 입력하세요.", true); $("#ceName").focus(); return; }
  let parts;
  try{ parts = ceBuildParts(); }catch(err){ ceStatus(err.message, true); return; }
  const lay = ceLay();
  const off = parts.map(p => ({e:p.elective, t:Math.round(Object.values(p.points).reduce((s, v) => s + (parseFloat(v) || 0), 0) * 100) / 100}))
                   .filter(x => x.t !== lay.max);
  if (off.length && !confirm(`배점 합계가 만점(${lay.max}점)과 다릅니다: ` +
      off.map(x => `${x.e || ceSubj()} ${x.t}점`).join(", ") + "\n그대로 저장할까요?")) return;
  const btn = $("#ceSave"); btn.disabled = true; ceStatus("저장 중…");
  try{
    const res = await fetch("/api/exams/custom", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({exam_id:CE.examId, year:+$("#ceY").value, month:+$("#ceM").value,
                           grade:CE.grade, name, card:$("#ceCard").value,
                           subject:ceSubj(), parts})});
    const j = await safeJson(res);
    if (!j.ok) throw new Error(j.message || "저장 실패");
    CE.examId = j.exam_id;
    $("#ceY").disabled = $("#ceM").disabled = true;
    CE.saved.add(ceSubj());
    parts.forEach(p => p.elective && CE.saved.add(ceSubj() + "|" + p.elective));
    EXAMS = j.exams; renderExams(CE.examId);
    ceStatus(`✓ ${ceSubj()}${parts[0].elective ? " (" + parts.map(p => p.elective).join("·") + ")" : ""} 정답 저장 완료 — 다른 과목도 이어서 입력하세요.`);
    ceRender();
  }catch(err){ ceStatus("✕ " + err.message, true); }
  finally{ btn.disabled = false; }
}

async function ceDeleteSubject(){
  const subj = ceSubj();
  if (!CE.examId || !confirm(`${subj} 정답을 삭제할까요?`)) return;
  try{
    const j = await safeJson(await fetch(`/api/exams/custom/${CE.examId}/delete`, {method:"POST",
      headers:{"Content-Type":"application/json"}, body:JSON.stringify({subject:subj})}));
    if (!j.ok) throw new Error(j.message);
    [...CE.saved].filter(k => k === subj || k.startsWith(subj + "|")).forEach(k => CE.saved.delete(k));
    delete CE.data[subj];
    EXAMS = j.exams; renderExams(CE.examId);
    ceStatus(`${subj} 정답을 삭제했습니다.`); ceRender();
  }catch(err){ ceStatus("✕ " + err.message, true); }
}

async function ceDeleteExam(){
  if (!CE.examId || !confirm(`사설 시험 「${$("#ceName").value}」과 입력한 정답을 모두 삭제할까요?`)) return;
  try{
    const j = await safeJson(await fetch(`/api/exams/custom/${CE.examId}/delete`, {method:"POST",
      headers:{"Content-Type":"application/json"}, body:"{}"}));
    if (!j.ok) throw new Error(j.message);
    EXAMS = j.exams; renderExams();
    $("#customModal").style.display = "none"; CE.open = false;
  }catch(err){ ceStatus("✕ " + err.message, true); }
}

$("#btnCustomNew").addEventListener("click", () => openCustomEditor(null));
$("#btnCustomEdit").addEventListener("click", () => openCustomEditor($("#exam_select").value));

renderSubjects();
renderExams();
updateGo();
