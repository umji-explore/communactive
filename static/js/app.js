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
