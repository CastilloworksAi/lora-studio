const state = { projects: [], current: null };
const $ = (id) => document.getElementById(id);

const projectList = $("projectList");
const emptyState = $("emptyState");
const projectView = $("projectView");
const imageGrid = $("imageGrid");
const statusBox = $("status");
const dialog = $("newProjectDialog");

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

function status(message, type = "") {
  statusBox.textContent = message;
  statusBox.className = `status ${type}`.trim();
}

function clearStatus() {
  statusBox.className = "status hidden";
}

async function loadProjects(selectSlug) {
  state.projects = await api("/api/projects");
  renderProjects();
  const selected = selectSlug || state.current?.slug || state.projects[0]?.slug;
  if (selected) await openProject(selected);
  else {
    state.current = null;
    emptyState.classList.remove("hidden");
    projectView.classList.add("hidden");
  }
}

function renderProjects() {
  projectList.innerHTML = "";
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.className = `projectLink ${state.current?.slug === project.slug ? "active" : ""}`;
    button.innerHTML = `${escapeHtml(project.name)}<span>${escapeHtml(project.trigger_word)} - ${project.images.length} images</span>`;
    button.addEventListener("click", () => openProject(project.slug));
    projectList.appendChild(button);
  }
}

async function openProject(slug) {
  state.current = await api(`/api/projects/${slug}`);
  renderProjects();
  emptyState.classList.add("hidden");
  projectView.classList.remove("hidden");
  $("projectTitle").textContent = state.current.name;
  $("projectToken").textContent = state.current.trigger_word;
  renderImages();
  clearStatus();
}

function renderImages() {
  const images = state.current.images;
  $("countLabel").textContent = `${images.length} image${images.length === 1 ? "" : "s"} - ${images.filter(i => i.captioned).length} captioned`;
  $("captionAllButton").disabled = images.length === 0;
  $("exportButton").disabled = images.length === 0 || images.some((image) => !image.captioned);
  imageGrid.innerHTML = "";
  for (const image of images) {
    const article = document.createElement("article");
    article.className = "imageItem";
    article.innerHTML = `
      <img class="thumb" src="${image.url}" alt="" />
      <div class="imageBody">
        <div class="filename" title="${escapeHtml(image.name)}">${escapeHtml(image.name)}</div>
        <textarea class="caption" placeholder="Generate a caption, then review it.">${escapeHtml(image.caption)}</textarea>
        <div class="imageActions">
          <button class="button generate">Analyze</button>
          <button class="button save">Save caption</button>
        </div>
      </div>`;
    article.querySelector(".generate").addEventListener("click", () => captionImage(image.name, article));
    article.querySelector(".save").addEventListener("click", () => saveCaption(image.name, article.querySelector("textarea").value));
    imageGrid.appendChild(article);
  }
}

async function uploadFiles(files) {
  if (!state.current || !files.length) return;
  status(`Adding ${files.length} image${files.length === 1 ? "" : "s"}...`);
  const encoded = await Promise.all([...files].map((file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, data: reader.result });
    reader.onerror = reject;
    reader.readAsDataURL(file);
  })));
  try {
    const result = await api(`/api/projects/${state.current.slug}/upload`, {
      method: "POST",
      body: JSON.stringify({ files: encoded }),
    });
    state.current = result.project;
    renderImages();
    renderProjects();
    status(`${result.stored.length} image${result.stored.length === 1 ? "" : "s"} added.`, "success");
  } catch (error) {
    status(error.message, "error");
  }
}

async function captionImage(name, article) {
  const button = article.querySelector(".generate");
  button.disabled = true;
  button.textContent = "Analyzing...";
  status(`Analyzing ${name} locally. This can take a minute on first run.`);
  try {
    const result = await api(`/api/projects/${state.current.slug}/caption`, {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    article.querySelector("textarea").value = result.caption;
    state.current = await api(`/api/projects/${state.current.slug}`);
    renderImages();
    status(`Caption created for ${name}. Review and edit it before export.`, "success");
  } catch (error) {
    status(error.message, "error");
    button.disabled = false;
    button.textContent = "Analyze";
  }
}

async function saveCaption(name, caption) {
  try {
    await api(`/api/projects/${state.current.slug}/save-caption`, {
      method: "POST",
      body: JSON.stringify({ name, caption }),
    });
    state.current = await api(`/api/projects/${state.current.slug}`);
    renderImages();
    status(`Caption saved for ${name}.`, "success");
  } catch (error) {
    status(error.message, "error");
  }
}

async function captionAll() {
  for (const image of state.current.images) {
    const article = [...imageGrid.children].find((item) => item.querySelector(".filename").title === image.name);
    await captionImage(image.name, article);
  }
}

async function exportProject() {
  try {
    status("Preparing dataset and custom training workflow...");
    const result = await api(`/api/projects/${state.current.slug}/export`, {
      method: "POST",
      body: "{}",
    });
    $("datasetPath").textContent = result.dataset_dir;
    $("workflowPath").textContent = result.workflow_path;
    $("outputPath").textContent = result.output_dir;
    $("exportPanel").classList.remove("hidden");
    status(`Export complete: ${result.images} reviewed images are ready for training.`, "success");
  } catch (error) {
    status(error.message, "error");
  }
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value || "";
  return node.innerHTML;
}

function openDialog() {
  $("newProjectForm").reset();
  dialog.showModal();
}

$("newProjectButton").addEventListener("click", openDialog);
$("emptyNewButton").addEventListener("click", openDialog);
$("cancelButton").addEventListener("click", () => dialog.close());
$("newProjectForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  try {
    const project = await api("/api/projects", { method: "POST", body: JSON.stringify(payload) });
    dialog.close();
    await loadProjects(project.slug);
  } catch (error) {
    status(error.message, "error");
    dialog.close();
  }
});

const dropzone = $("dropzone");
dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("dragging");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragging"));
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragging");
  uploadFiles(event.dataTransfer.files);
});
$("fileInput").addEventListener("change", (event) => uploadFiles(event.target.files));
$("captionAllButton").addEventListener("click", captionAll);
$("exportButton").addEventListener("click", exportProject);

loadProjects().catch((error) => status(error.message, "error"));
