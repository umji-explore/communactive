// 삭제/구매 등 확인창
document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    if (!confirm(form.dataset.confirm)) e.preventDefault();
  });
});

// 중복 제출 방지 (버튼 연타로 글/댓글이 두 번 올라가는 것 막기)
document.querySelectorAll("form[method=post]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    if (e.defaultPrevented) return;
    if (form.dataset.submitting) { e.preventDefault(); return; }
    form.dataset.submitting = "1";
    setTimeout(() => delete form.dataset.submitting, 4000);
  });
});

// 글자 수 표시
document.querySelectorAll("[data-counter]").forEach((el) => {
  const out = document.getElementById(el.dataset.counter);
  const update = () => { out.textContent = el.value.length.toLocaleString(); };
  el.addEventListener("input", update);
  update();
});

// 작성 중인 글이 있으면 페이지 이탈 경고
document.querySelectorAll("form[data-unsaved]").forEach((form) => {
  let dirty = false;
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", () => { dirty = false; });
  window.addEventListener("beforeunload", (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
});

// ---------- 사진 업로드 (한 장씩 /upload 로) ----------
const CSRF = () => (document.querySelector("input[name=csrf_token]") || {}).value || "";
const MAX_SEND = 4 * 1024 * 1024; // Vercel 은 요청 하나당 4.5MB 제한

// 4MB 넘는 사진은 브라우저에서 먼저 줄여서 보낸다 (GIF 는 그대로)
async function shrinkIfNeeded(file) {
  if (file.size <= MAX_SEND || file.type === "image/gif") return file;
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, 2400 / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.88));
    return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
  } catch { return file; }
}

async function uploadOne(file, kind) {
  const body = new FormData();
  body.append("file", await shrinkIfNeeded(file));
  body.append("kind", kind);
  body.append("csrf_token", CSRF());
  const res = await fetch("/upload", { method: "POST", body, headers: { "X-CSRF-Token": CSRF() } });
  if (res.status === 413) throw new Error("파일이 너무 커요 (최대 4MB)");
  const data = await res.json().catch(() => ({ error: "업로드에 실패했어요" }));
  if (!res.ok) throw new Error(data.error || "업로드에 실패했어요");
  return data;
}

function insertAtCursor(textarea, text) {
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? start;
  const before = textarea.value.slice(0, start);
  const pre = before && !before.endsWith("\n") ? "\n" : "";
  const piece = pre + text + "\n";
  textarea.value = before + piece + textarea.value.slice(end);
  const pos = start + piece.length;
  textarea.setSelectionRange(pos, pos);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

function addTrayItem(tray, token, url) {
  tray.hidden = false;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tray-item";
  btn.dataset.token = token;
  btn.title = "본문에 한 번 더 넣기";
  const img = document.createElement("img");
  img.src = url;
  btn.appendChild(img);
  tray.appendChild(btn);
}

document.querySelectorAll("input[type=file][data-upload]").forEach((input) => {
  input.addEventListener("change", async () => {
    const kind = input.dataset.upload;
    const status = document.getElementById("upload-status");
    const files = [...input.files];
    input.value = "";
    for (let i = 0; i < files.length; i++) {
      if (status) status.textContent = `사진 올리는 중… (${i + 1}/${files.length})`;
      try {
        const up = await uploadOne(files[i], kind);
        if (input.dataset.insert) {                       // 글쓰기: 본문에 [[img:번호]] 넣기
          const ta = document.getElementById(input.dataset.insert);
          const token = `[[img:${up.id}]]`;
          insertAtCursor(ta, token);
          const tray = document.getElementById("image-tray");
          if (tray) addTrayItem(tray, token, up.url);
        } else if (input.dataset.collect) {               // 커뮤콘: 여러 장 모으기
          const box = document.getElementById(input.dataset.collect);
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "collect-item";
          btn.title = "빼기";
          const img = document.createElement("img");
          img.src = up.url;
          const hidden = document.createElement("input");
          Object.assign(hidden, { type: "hidden", name: "image_id", value: up.id });
          btn.append(img, hidden);
          btn.addEventListener("click", () => btn.remove());
          box.appendChild(btn);
        } else if (input.dataset.target) {                // 프로필 / 아이콘 / 배너: 한 장
          const t = input.dataset.target;
          document.getElementById(`${t}-id`).value = up.id;
          const preview = document.getElementById(`${t}-preview`);
          if (t === "banner") {
            preview.style.backgroundImage = `url('${up.url}')`;
            preview.classList.add("has");
            preview.textContent = "";
          } else {
            const img = document.createElement("img");
            Object.assign(img, { src: up.url, alt: "", width: 72, height: 72 });
            img.className = t === "icon" ? "communi-icon" : "avatar";
            preview.replaceChildren(img);
          }
          if (input.dataset.autosubmit) document.getElementById(input.dataset.autosubmit).submit();
        }
        if (status) status.textContent = "";
      } catch (e) {
        if (status) status.textContent = "";
        alert(`${files[i].name}: ${e.message}`);
      }
    }
  });
});

// 글쓰기: 아래 사진 목록을 누르면 본문 커서 위치에 다시 넣기
document.addEventListener("click", (e) => {
  const item = e.target.closest(".tray-item");
  if (!item) return;
  const ta = document.getElementById("content");
  if (ta) insertAtCursor(ta, item.dataset.token);
});

// ---------- 투표 만들기 ----------
(() => {
  const builder = document.querySelector(".poll-builder");
  if (!builder) return;
  const on = document.getElementById("poll-on");
  const box = document.getElementById("poll-options");
  // 도구 모음의 '투표' 버튼으로 열고, '투표 빼기'로 닫는다. 닫혀 있으면 투표 없이 등록된다.
  document.querySelectorAll("[data-open-poll]").forEach((b) => b.addEventListener("click", () => {
    builder.hidden = false;
    on.value = "1";
    builder.scrollIntoView({ behavior: "smooth", block: "center" });
    builder.querySelector("input[name=poll_question]").focus({ preventScroll: true });
  }));
  document.getElementById("remove-poll").addEventListener("click", () => {
    builder.hidden = true;
    on.value = "";
  });
  const renumber = () => box.querySelectorAll("input").forEach((inp, i) => { inp.placeholder = `항목 ${i + 1}`; });
  document.getElementById("add-option").addEventListener("click", () => {
    if (box.children.length >= 10) return alert("항목은 10개까지 넣을 수 있어요.");
    const row = box.firstElementChild.cloneNode(true);
    row.querySelector("input").value = "";
    box.appendChild(row);
    renumber();
    row.querySelector("input").focus();
  });
  box.addEventListener("click", (e) => {
    if (!e.target.matches("[data-remove-option]")) return;
    if (box.children.length <= 2) return alert("항목은 최소 2개가 필요해요.");
    e.target.closest(".poll-option-row").remove();
    renumber();
  });
})();

// 보안 코드 새로고침
(() => {
  const btn = document.getElementById("captcha-refresh");
  const img = document.getElementById("captcha-img");
  if (!btn || !img) return;
  btn.addEventListener("click", () => {
    img.src = img.src.split("?")[0] + "?t=" + Date.now();
    const input = document.querySelector("input[name=captcha]");
    if (input) { input.value = ""; input.focus(); }
  });
})();

// 복사 버튼
document.querySelectorAll("[data-copy]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const text = document.getElementById(btn.dataset.copy).textContent.trim();
    try { await navigator.clipboard.writeText(text); btn.textContent = "복사했어요!"; }
    catch { btn.textContent = "직접 선택해서 복사해주세요"; }
  });
});

// 홈 베스트 탭 (실시간 / 일간 / 주간)
document.querySelectorAll("[data-tabs]").forEach((box) => {
  box.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.tab;
      box.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b === btn));
      box.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== key; });
      box.querySelectorAll("[data-note]").forEach((n) => { n.hidden = n.dataset.note !== key; });
    });
  });
});

// 댓글 커뮤콘 선택기
(() => {
  const toggle = document.getElementById("con-toggle");
  if (!toggle) return;
  const picker = document.getElementById("con-picker");
  const hidden = document.getElementById("con-id");
  const preview = document.getElementById("con-preview");
  const previewImg = preview.querySelector("img");

  toggle.addEventListener("click", () => { picker.hidden = !picker.hidden; });

  picker.querySelectorAll(".con-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      picker.querySelectorAll(".con-tab").forEach((t) => t.classList.toggle("on", t === tab));
      picker.querySelectorAll(".con-items").forEach((box) => { box.hidden = box.dataset.pack !== tab.dataset.pack; });
    });
  });

  picker.querySelectorAll(".con-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      hidden.value = btn.dataset.id;
      previewImg.src = btn.dataset.src;
      preview.hidden = false;
      picker.hidden = true;
    });
  });

  document.getElementById("con-clear").addEventListener("click", () => {
    hidden.value = "";
    preview.hidden = true;
  });
})();

// 커뮤콘 만들기: 업로드 미리보기
(() => {
  const input = document.getElementById("con-files");
  const box = document.getElementById("con-file-preview");
  if (!input || !box) return;
  input.addEventListener("change", () => {
    box.innerHTML = "";
    [...input.files].forEach((file) => {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.title = file.name;
      box.appendChild(img);
    });
  });
})();
