// 삭제/구매 등 확인창
document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (e) => {
    if (!confirm(form.dataset.confirm)) e.preventDefault();
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
