// communactive 글쓰기 에디터
// - 서식: 브라우저 기본 편집 기능(execCommand) 사용, 서버에서 다시 한 번 걸러서 저장
// - 사진/동영상/커뮤콘/유튜브/외부 콘텐츠/AI 이미지: <figure data-embed data-id> 블록으로 넣고, 저장할 땐 자리표시만 보냄

const form = document.getElementById("write-form");
const editor = document.getElementById("editor");
if (form && editor) init();

function init() {
  const status = document.getElementById("upload-status");
  const csrf = () => form.querySelector("input[name=csrf_token]").value;
  const setStatus = (t) => { status.textContent = t || ""; };

  document.execCommand("styleWithCSS", false, true);
  document.execCommand("defaultParagraphSeparator", false, "div");

  // ---------- 커서 위치 기억 (도구 버튼을 눌러도 입력 위치를 잃지 않게) ----------
  let savedRange = null;
  const inEditor = (node) => node && (node === editor || editor.contains(node));
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (sel.rangeCount && inEditor(sel.anchorNode)) savedRange = sel.getRangeAt(0).cloneRange();
  });
  function restoreRange() {
    editor.focus();
    const sel = window.getSelection();
    if (savedRange && inEditor(savedRange.startContainer)) {
      sel.removeAllRanges();
      sel.addRange(savedRange);
    } else {
      const r = document.createRange();
      r.selectNodeContents(editor);
      r.collapse(false);
      sel.removeAllRanges();
      sel.addRange(r);
    }
  }

  // ---------- 서식 도구 ----------
  document.querySelectorAll(".ed-toolbar button, .ed-panel .swatch").forEach((b) =>
    b.addEventListener("mousedown", (e) => e.preventDefault()));
  document.querySelectorAll(".ed-toolbar [data-cmd]").forEach((el) => {
    const run = () => {
      restoreRange();
      const cmd = el.dataset.cmd;
      let value = el.dataset.value || el.value || null;
      if (cmd === "createLink") {
        value = prompt("링크 주소를 입력하세요 (https://...)");
        if (!value || !/^https?:\/\//i.test(value)) return;
      }
      if (cmd === "formatBlock") value = `<${value}>`;
      if (cmd === "fontSize" && !value) return;
      document.execCommand(cmd, false, value);
      if (el.tagName === "SELECT") el.value = "";
      updateCount();
    };
    el.addEventListener(el.tagName === "SELECT" ? "change" : "click", run);
  });
  document.querySelectorAll("[data-cmd-color]").forEach((sw) => sw.addEventListener("click", () => {
    restoreRange();
    document.execCommand(sw.dataset.cmdColor, false, sw.dataset.color);
    closePanels();
  }));

  // ---------- 도구 패널 열고 닫기 ----------
  const panels = document.querySelectorAll(".ed-panel");
  function closePanels() { panels.forEach((p) => { p.hidden = true; }); }
  document.querySelectorAll("[data-panel]").forEach((btn) => btn.addEventListener("click", () => {
    const p = document.querySelector(`[data-panel-body="${btn.dataset.panel}"]`);
    const wasOpen = !p.hidden;
    closePanels();
    p.hidden = wasOpen;
    if (!p.hidden) { const inp = p.querySelector("input:not([type=radio]), textarea"); if (inp) inp.focus(); }
  }));
  document.querySelectorAll("[data-open-poll]").forEach((b) => b.addEventListener("click", () => {
    const d = document.getElementById("poll-builder");
    d.open = true;
    d.scrollIntoView({ behavior: "smooth", block: "center" });
  }));

  // ---------- 블록(figure) 넣기 ----------
  function topBlock(node) {
    while (node && node.parentNode !== editor) node = node.parentNode;
    return node;
  }
  function insertFigure(kind, id, inner) {
    restoreRange();
    const fig = document.createElement("figure");
    fig.className = `emb emb-${kind}`;
    fig.dataset.embed = kind;
    fig.dataset.id = id;
    fig.contentEditable = "false";
    fig.innerHTML = inner + '<button type="button" class="emb-remove" title="빼기">✕</button>';
    const sel = window.getSelection();
    const anchor = sel.rangeCount ? topBlock(sel.getRangeAt(0).startContainer) : null;
    if (anchor && anchor !== editor) anchor.after(fig); else editor.appendChild(fig);
    const next = document.createElement("div");
    next.innerHTML = "<br>";
    fig.after(next);
    const r = document.createRange();
    r.setStart(next, 0);
    r.collapse(true);
    sel.removeAllRanges();
    sel.addRange(r);
    savedRange = r.cloneRange();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    return fig;
  }
  editor.addEventListener("click", (e) => {
    const rm = e.target.closest(".emb-remove");
    if (rm) { rm.closest("figure").remove(); editor.dispatchEvent(new Event("input", { bubbles: true })); }
  });
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- 서버 요청 ----------
  async function post(url, data) {
    const body = new FormData();
    Object.entries(data).forEach(([k, v]) => body.append(k, v));
    body.append("csrf_token", csrf());
    const res = await fetch(url, { method: "POST", body, headers: { "X-CSRF-Token": csrf() } });
    if (res.status === 413) throw new Error("파일이 너무 커요 (최대 4MB)");
    const json = await res.json().catch(() => ({ error: "요청에 실패했어요" }));
    if (!res.ok) throw new Error(json.error || "요청에 실패했어요");
    return json;
  }

  // 4MB 넘는 사진은 브라우저에서 먼저 줄여서 보낸다 (GIF 는 그대로)
  async function shrink(file) {
    if (file.size <= 4 * 1024 * 1024 || file.type === "image/gif") return file;
    try {
      const bmp = await createImageBitmap(file);
      const scale = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * scale);
      c.height = Math.round(bmp.height * scale);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.88));
      return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
    } catch { return file; }
  }

  async function uploadImages(files) {
    files = [...files].filter((f) => f.type.startsWith("image/"));
    for (let i = 0; i < files.length; i++) {
      setStatus(`이미지 올리는 중… (${i + 1}/${files.length})`);
      try {
        const up = await post("/upload", { file: await shrink(files[i]), kind: "post" });
        insertFigure("image", up.id, `<img src="${esc(up.url)}" alt="">`);
      } catch (e) { alert(`${files[i].name}: ${e.message}`); }
    }
    setStatus("");
  }

  async function uploadVideo(file) {
    try {
      setStatus("동영상 준비 중…");
      const t = await post("/upload/video-token", { type: file.type, size: file.size, name: file.name });
      let up;
      if (t.mode === "blob") {
        const { put } = await import("/static/vendor/blob-client.js");
        const blob = await put(t.pathname, file, {
          access: "public", token: t.token, contentType: file.type, multipart: file.size > 8 * 1024 * 1024,
          onUploadProgress: (p) => setStatus(`동영상 올리는 중… ${Math.round(p.percentage)}%`),
        });
        up = await post("/upload/video-done", { pathname: t.pathname, url: blob.url });
      } else {
        setStatus("동영상 올리는 중…");
        up = await post("/upload", { file, kind: "video" });
      }
      insertFigure("video", up.id, `<video src="${esc(up.url)}" controls preload="metadata" playsinline></video>`);
    } catch (e) { alert(`동영상: ${e.message}`); }
    setStatus("");
  }

  document.querySelectorAll("[data-ed-upload]").forEach((input) => input.addEventListener("change", () => {
    const files = [...input.files];
    input.value = "";
    if (input.dataset.edUpload === "video") files.forEach(uploadVideo);
    else uploadImages(files);
  }));

  // 붙여넣기: 사진은 업로드, 글자는 서식 없이
  const members = editor.dataset.members === "1";  // 비회원은 사진·동영상 업로드 불가
  editor.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
    e.preventDefault();
    if (files.length) { if (members) uploadImages(files); else alert("사진은 회원만 올릴 수 있어요."); return; }
    const text = e.clipboardData.getData("text/plain");
    document.execCommand("insertText", false, text);
  });
  editor.addEventListener("dragover", (e) => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); });
  editor.addEventListener("drop", (e) => {
    const files = [...(e.dataTransfer?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    if (!members) { alert("사진·동영상은 회원만 올릴 수 있어요."); return; }
    const pos = document.caretRangeFromPoint?.(e.clientX, e.clientY);
    if (pos) savedRange = pos;
    uploadImages(files.filter((f) => f.type.startsWith("image/")));
    files.filter((f) => f.type.startsWith("video/")).forEach(uploadVideo);
  });

  // ---------- 커뮤콘 ----------
  document.querySelectorAll(".ed-panel .con-tab").forEach((tab) => tab.addEventListener("click", () => {
    const box = tab.closest(".ed-panel");
    box.querySelectorAll(".con-tab").forEach((t) => t.classList.toggle("on", t === tab));
    box.querySelectorAll(".con-items").forEach((c) => { c.hidden = c.dataset.pack !== tab.dataset.pack; });
  }));
  document.querySelectorAll("[data-ed-con]").forEach((btn) => btn.addEventListener("click", () => {
    insertFigure("con", btn.dataset.edCon, `<img src="${esc(btn.dataset.src)}" alt="커뮤콘">`);
    closePanels();
  }));

  // ---------- 유튜브 / 외부 콘텐츠 ----------
  document.querySelectorAll("[data-embed-go]").forEach((btn) => {
    const which = btn.dataset.embedGo;
    const input = document.querySelector(`[data-embed-input="${which}"]`);
    const go = async () => {
      const url = input.value.trim();
      if (!url) return;
      btn.disabled = true;
      setStatus("불러오는 중…");
      try {
        const r = await post("/embed", { url });
        if (which === "video" && r.embed === "link") throw new Error("유튜브나 Vimeo 동영상 주소가 아니에요. '외부 콘텐츠'를 이용해 주세요.");
        if (r.embed === "youtube") {
          insertFigure("youtube", r.id, `<div class="emb-video-ph"><img src="https://i.ytimg.com/vi/${esc(r.id)}/hqdefault.jpg" alt="" referrerpolicy="no-referrer"><span>▶ 유튜브 동영상</span></div>`);
        } else if (r.embed === "vimeo") {
          insertFigure("vimeo", r.id, `<div class="emb-video-ph"><span>▶ Vimeo 동영상</span></div>`);
        } else {
          insertFigure("link", r.id, `<div class="link-card"><span class="lc-text"><strong>${esc(r.title)}</strong><span class="lc-site">${esc(url)}</span></span></div>`);
        }
        input.value = "";
        closePanels();
      } catch (e) { alert(e.message); }
      btn.disabled = false;
      setStatus("");
    };
    btn.addEventListener("click", go);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
  });

  // ---------- 시리즈 ----------
  const seriesId = document.getElementById("series-id");
  const chip = document.getElementById("series-chip");
  const chipTitle = document.getElementById("series-chip-title");
  const seriesList = document.getElementById("series-list");
  function pickSeries(radio) {
    seriesId.value = radio.value;
    chip.hidden = !radio.value;
    chipTitle.textContent = radio.dataset.title || "";
  }
  if (seriesList) seriesList.addEventListener("change", (e) => { if (e.target.name === "_series") pickSeries(e.target); });
  document.getElementById("create-series")?.addEventListener("click", async () => {
    const inp = document.getElementById("new-series");
    const title = inp.value.trim();
    if (!title) return inp.focus();
    try {
      const s = await post("/series", { title });
      const label = document.createElement("label");
      label.className = "check small";
      label.innerHTML = `<input type="radio" name="_series" value="${s.id}"> `;
      label.firstChild.dataset.title = s.title;
      label.append(s.title);
      document.getElementById("series-list").appendChild(label);
      label.firstChild.checked = true;
      pickSeries(label.firstChild);
      inp.value = "";
    } catch (e) { alert(e.message); }
  });

  // ---------- 글자 수 · 저장 ----------
  const count = document.getElementById("char-count");
  function updateCount() { count.textContent = editor.innerText.replace(/\n/g, "").length.toLocaleString(); }
  editor.addEventListener("input", updateCount);
  updateCount();

  form.addEventListener("submit", (e) => {
    const clone = editor.cloneNode(true);
    clone.querySelectorAll("figure[data-embed]").forEach((f) => {
      f.innerHTML = "";
      f.removeAttribute("contenteditable");
      f.removeAttribute("class");
    });
    const html = clone.innerHTML.trim();
    if (!editor.innerText.trim() && !clone.querySelector("figure")) {
      e.preventDefault();
      alert("내용을 입력해 주세요.");
      return;
    }
    document.getElementById("content-input").value = html;
  }, true);
}
