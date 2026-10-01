(() => {
  "use strict";

  const root = document.getElementById("review-workspace");
  if (!root) return;

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const elements = {
    status: document.getElementById("workspace-status"),
    pendingCount: document.getElementById("pending-count"),
    search: document.getElementById("review-search"),
    batchFilter: document.getElementById("batch-filter"),
    selectVisible: document.getElementById("select-visible"),
    clearSelection: document.getElementById("clear-selection"),
    selectionCount: document.getElementById("selection-count"),
    selectionSize: document.getElementById("selection-size"),
    batchSections: document.getElementById("batch-sections"),
    destinationSearch: document.getElementById("destination-search"),
    destinationShortcuts: document.getElementById("destination-shortcuts"),
    destinationTree: document.getElementById("destination-tree"),
    selectedDestination: document.getElementById("selected-destination"),
    newFolderName: document.getElementById("new-folder-name"),
    moveButton: document.getElementById("move-selected"),
    moveCount: document.getElementById("move-count"),
    imageDialog: document.getElementById("image-dialog"),
    dialogImage: document.getElementById("dialog-image"),
    dialogCaption: document.getElementById("dialog-caption"),
    dialogClose: document.getElementById("dialog-close"),
    toastStack: document.getElementById("toast-stack"),
  };

  const state = {
    items: [],
    batches: [],
    categories: [],
    recentDestinations: [],
    favoriteDestinations: [],
    selectedIds: new Set(),
    processingIds: new Set(),
    destination: "",
    initialBatch: root.dataset.initialBatch || "",
  };

  function setStatus(message, tone = "ready") {
    elements.status.dataset.tone = tone;
    elements.status.querySelector("span:last-child").textContent = message;
  }

  function showToast(message, tone = "success") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${tone}`;
    toast.textContent = message;
    elements.toastStack.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("visible"));
    setTimeout(() => {
      toast.classList.remove("visible");
      setTimeout(() => toast.remove(), 250);
    }, 5000);
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) {
      throw new Error(payload?.detail || `Request failed with HTTP ${response.status}`);
    }
    return payload;
  }

  async function loadWorkspace({ preserveSelection = false } = {}) {
    setStatus("Refreshing queue…", "busy");
    const previousSelection = preserveSelection ? new Set(state.selectedIds) : new Set();
    const payload = await fetchJson("/api/v1/review/workspace");
    state.items = payload.items || [];
    state.batches = payload.batches || [];
    state.categories = payload.categories || [];
    state.recentDestinations = payload.recent_destinations || [];
    state.favoriteDestinations = payload.favorite_destinations || [];
    state.processingIds = new Set(payload.processing_item_ids || []);
    const currentIds = new Set(state.items.map((item) => item.id));
    state.selectedIds = new Set([...previousSelection].filter((id) => currentIds.has(id)));
    renderBatchFilter();
    renderItems();
    renderDestinations();
    updateActionState();
    setStatus(`${state.items.length} pending file${state.items.length === 1 ? "" : "s"}`, "ready");
  }

  function renderBatchFilter() {
    const current = elements.batchFilter.value || state.initialBatch;
    const tokens = [...new Set(state.items.map((item) => item.batch_token).filter(Boolean))];
    const batchLookup = new Map(state.batches.map((batch) => [batch.batch_token, batch]));
    elements.batchFilter.replaceChildren(new Option("All batches", ""));
    tokens.forEach((token) => {
      const batch = batchLookup.get(token);
      const count = state.items.filter((item) => item.batch_token === token).length;
      const label = `Batch ${token.slice(0, 8)} · ${batch?.created_at ? formatDate(batch.created_at) : "pending"} · ${count}`;
      elements.batchFilter.appendChild(new Option(label, token));
    });
    if ([...elements.batchFilter.options].some((option) => option.value === current)) {
      elements.batchFilter.value = current;
    }
    state.initialBatch = "";
  }

  function getVisibleItems() {
    const query = elements.search.value.trim().toLowerCase();
    const batchToken = elements.batchFilter.value;
    return state.items.filter((item) => {
      if (batchToken && item.batch_token !== batchToken) return false;
      if (!query) return true;
      return [item.original_file_name, item.source_label, item.source_relative_path, item.file_label]
        .join(" ").toLowerCase().includes(query);
    });
  }

  function renderItems() {
    const visibleItems = getVisibleItems();
    elements.pendingCount.textContent = String(state.items.length);
    elements.batchSections.replaceChildren();
    if (!visibleItems.length) {
      const empty = document.createElement("div");
      empty.className = "workspace-empty";
      empty.textContent = state.items.length ? "No files match the current filters." : "The review queue is clear.";
      elements.batchSections.appendChild(empty);
      updateActionState();
      return;
    }

    const groups = new Map();
    visibleItems.forEach((item) => {
      const key = item.batch_token || "unbatched";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });

    groups.forEach((items, token) => {
      const section = document.createElement("section");
      section.className = "batch-section";
      const header = document.createElement("div");
      header.className = "batch-section-header";
      const title = document.createElement("strong");
      title.textContent = token === "unbatched" ? "Awaiting notification batch" : `Batch ${token.slice(0, 8)}`;
      const meta = document.createElement("span");
      meta.textContent = `${items.length} file${items.length === 1 ? "" : "s"}`;
      header.append(title, meta);
      const list = document.createElement("div");
      list.className = "document-list";
      items.forEach((item) => list.appendChild(buildDocumentRow(item)));
      section.append(header, list);
      elements.batchSections.appendChild(section);
    });
    updateActionState();
  }

  function buildDocumentRow(item) {
    const processing = state.processingIds.has(item.id);
    const row = document.createElement("article");
    row.className = `document-row${state.selectedIds.has(item.id) ? " selected" : ""}${processing ? " processing" : ""}${item.error_message ? " has-error" : ""}`;
    row.dataset.itemId = String(item.id);

    const checkboxLabel = document.createElement("label");
    checkboxLabel.className = "document-check";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedIds.has(item.id);
    checkbox.disabled = processing;
    checkbox.setAttribute("aria-label", `Select ${item.original_file_name}`);
    checkbox.addEventListener("change", () => {
      checkbox.checked ? state.selectedIds.add(item.id) : state.selectedIds.delete(item.id);
      row.classList.toggle("selected", checkbox.checked);
      updateActionState();
    });
    checkboxLabel.appendChild(checkbox);

    const preview = document.createElement(item.media_url ? "button" : "div");
    preview.className = "document-preview";
    if (item.media_url) {
      preview.type = "button";
      preview.title = "Open full image";
      preview.addEventListener("click", () => openImage(item));
    }
    if (item.thumbnail_url) {
      const image = document.createElement("img");
      image.src = item.thumbnail_url;
      image.alt = "";
      image.loading = "lazy";
      preview.appendChild(image);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "document-placeholder";
      placeholder.textContent = item.file_label || "FILE";
      preview.appendChild(placeholder);
    }

    const details = document.createElement("div");
    details.className = "document-details";
    const name = document.createElement("strong");
    name.className = "document-name";
    name.textContent = item.original_file_name;
    name.title = item.original_file_name;
    const path = document.createElement("span");
    path.textContent = item.source_relative_path || item.source_label;
    const facts = document.createElement("span");
    const dimensions = item.width && item.height ? ` · ${item.width}×${item.height}` : "";
    facts.textContent = `${item.display_size_mb} MB · ${item.file_label}${dimensions}`;
    details.append(name, path, facts);
    if (item.error_message) {
      const error = document.createElement("span");
      error.className = "document-error";
      error.textContent = item.error_message;
      details.appendChild(error);
    }

    const source = document.createElement("span");
    source.className = "source-badge";
    source.textContent = processing ? "Moving…" : item.source_label;
    row.append(checkboxLabel, preview, details, source);
    return row;
  }

  function renderDestinations() {
    const query = elements.destinationSearch.value.trim().toLowerCase();
    elements.destinationTree.replaceChildren();
    elements.destinationShortcuts.replaceChildren();

    const shortcuts = [...state.favoriteDestinations, ...state.recentDestinations]
      .filter((item, index, all) => all.findIndex((other) => other.destination_value === item.destination_value) === index)
      .slice(0, 8);
    if (shortcuts.length) {
      const label = document.createElement("span");
      label.className = "shortcut-label";
      label.textContent = "Quick access";
      elements.destinationShortcuts.appendChild(label);
      shortcuts.forEach((item) => elements.destinationShortcuts.appendChild(destinationButton(item.destination_value, item.label, true)));
    }

    state.categories.forEach((category) => {
      if (query) {
        const matches = flattenCategory(category).filter((entry) => entry.label.toLowerCase().includes(query));
        matches.forEach((entry) => elements.destinationTree.appendChild(destinationButton(entry.value, entry.label, false, entry.depth)));
        return;
      }
      elements.destinationTree.appendChild(buildCategoryTree(category));
    });
    if (!elements.destinationTree.children.length) {
      const empty = document.createElement("div");
      empty.className = "workspace-empty compact";
      empty.textContent = "No configured destination matches your search.";
      elements.destinationTree.appendChild(empty);
    }
    updateDestinationSummary();
  }

  function buildCategoryTree(category) {
    const details = document.createElement("details");
    details.className = "destination-category";
    details.open = destinationContains(category.name);
    const summary = document.createElement("summary");
    const folder = document.createElement("span");
    folder.className = "folder-icon";
    folder.textContent = "▸";
    const rootButton = destinationButton(category.name, category.name, false);
    rootButton.classList.add("category-root-button");
    summary.append(folder, rootButton);
    details.appendChild(summary);
    const children = document.createElement("div");
    children.className = "destination-children";
    appendDestinationChildren(children, category.name, category.children || [], 1);
    details.appendChild(children);
    return details;
  }

  function appendDestinationChildren(container, categoryName, nodes, depth) {
    nodes.forEach((node) => {
      const value = `${categoryName} / ${node.path}`;
      if (!node.children?.length) {
        container.appendChild(destinationButton(value, node.name, false, depth));
        return;
      }

      const details = document.createElement("details");
      details.className = "destination-folder";
      details.open = destinationContains(value);
      const summary = document.createElement("summary");
      const folder = document.createElement("span");
      folder.className = "folder-icon";
      folder.textContent = "▸";
      summary.append(folder, destinationButton(value, node.name, false, depth));
      details.appendChild(summary);
      const children = document.createElement("div");
      children.className = "destination-children";
      appendDestinationChildren(children, categoryName, node.children, depth + 1);
      details.appendChild(children);
      container.appendChild(details);
    });
  }

  function destinationContains(value) {
    return state.destination === value || state.destination.startsWith(`${value} / `);
  }

  function flattenCategory(category) {
    const result = [{ value: category.name, label: category.name, depth: 0 }];
    const visit = (nodes, depth) => nodes.forEach((node) => {
      result.push({ value: `${category.name} / ${node.path}`, label: `${category.name} / ${node.path}`, depth });
      visit(node.children || [], depth + 1);
    });
    visit(category.children || [], 1);
    return result;
  }

  function destinationButton(value, label, shortcut = false, depth = 0) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = shortcut ? "destination-chip" : "destination-row";
    button.classList.toggle("selected", state.destination === value);
    button.textContent = label;
    if (!shortcut) button.style.setProperty("--tree-depth", String(depth));
    button.title = value;
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      state.destination = value;
      renderDestinations();
      updateActionState();
    });
    return button;
  }

  function finalDestination() {
    const extra = elements.newFolderName.value.trim().replace(/\\/g, "/");
    return extra && state.destination ? `${state.destination} / ${extra}` : state.destination;
  }

  function updateDestinationSummary() {
    elements.selectedDestination.textContent = finalDestination() || "No destination selected";
  }

  function updateActionState() {
    const selectedItems = state.items.filter((item) => state.selectedIds.has(item.id));
    const size = selectedItems.reduce((total, item) => total + Number(item.display_size_mb || 0), 0);
    elements.selectionCount.textContent = `${selectedItems.length} selected`;
    elements.selectionSize.textContent = selectedItems.length ? `${size.toFixed(2)} MB across the queue` : "Choose documents from one or more batches";
    elements.moveCount.textContent = String(selectedItems.length);
    elements.moveButton.disabled = !selectedItems.length || !state.destination || selectedItems.some((item) => state.processingIds.has(item.id));
    updateDestinationSummary();
  }

  async function moveSelected() {
    const itemIds = [...state.selectedIds].filter((id) => state.items.some((item) => item.id === id));
    const destination = finalDestination();
    if (!itemIds.length || !destination) return;
    itemIds.forEach((id) => state.processingIds.add(id));
    renderItems();
    setStatus(`Moving ${itemIds.length} file${itemIds.length === 1 ? "" : "s"}…`, "busy");
    try {
      const job = await fetchJson("/api/v1/review/move", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ item_ids: itemIds, destination }),
      });
      await pollJob(job.id);
    } catch (error) {
      itemIds.forEach((id) => state.processingIds.delete(id));
      renderItems();
      setStatus("Move could not be started", "error");
      showToast(error.message, "error");
    }
  }

  async function pollJob(jobId) {
    for (;;) {
      const job = await fetchJson(`/api/v1/review/jobs/${encodeURIComponent(jobId)}`);
      if (job.status === "queued" || job.status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 650));
        continue;
      }
      if (job.status === "failed") {
        showToast(job.error || "The background move failed.", "error");
      } else {
        const result = job.result || {};
        const savedIds = new Set((result.saved_items || []).map((item) => item.id));
        savedIds.forEach((id) => state.selectedIds.delete(id));
        if (result.saved_count) showToast(`Moved ${result.saved_count} file${result.saved_count === 1 ? "" : "s"} to ${result.destination}.`);
        if (result.failed_count) showToast(`${result.failed_count} file${result.failed_count === 1 ? "" : "s"} could not be moved and remain selected.`, "error");
      }
      await loadWorkspace({ preserveSelection: true });
      return;
    }
  }

  function openImage(item) {
    elements.dialogImage.src = item.media_url;
    elements.dialogImage.alt = item.original_file_name;
    elements.dialogCaption.textContent = item.original_file_name;
    elements.imageDialog.showModal();
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  elements.search.addEventListener("input", renderItems);
  elements.batchFilter.addEventListener("change", renderItems);
  elements.destinationSearch.addEventListener("input", renderDestinations);
  elements.newFolderName.addEventListener("input", updateActionState);
  elements.selectVisible.addEventListener("click", () => {
    getVisibleItems().filter((item) => !state.processingIds.has(item.id)).forEach((item) => state.selectedIds.add(item.id));
    renderItems();
  });
  elements.clearSelection.addEventListener("click", () => { state.selectedIds.clear(); renderItems(); });
  elements.moveButton.addEventListener("click", moveSelected);
  elements.dialogClose.addEventListener("click", () => elements.imageDialog.close());
  elements.imageDialog.addEventListener("click", (event) => {
    if (event.target === elements.imageDialog) elements.imageDialog.close();
  });

  loadWorkspace().catch((error) => {
    setStatus("Queue unavailable", "error");
    elements.batchSections.textContent = error.message;
    showToast(error.message, "error");
  });
})();
