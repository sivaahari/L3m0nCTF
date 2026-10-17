(function() {
  "use strict";

  const app = document.getElementById("guard-app");
  if (!app) return;

  const apiBase = app.dataset.api;
  const statusEl = document.getElementById("guard-status");
  const tableWrap = document.getElementById("guard-table-wrap");
  const btnRefresh = document.getElementById("btn-refresh");
  const filterStatus = document.getElementById("filter-status");
  const filterKind = document.getElementById("filter-kind");
  const filterTeam = document.getElementById("filter-team");

  function getCsrfToken() {
    if (window.init && window.init.csrfNonce) {
      return window.init.csrfNonce;
    }
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  async function loadEvidence() {
    statusEl.textContent = "Loading evidence queue…";
    const params = new URLSearchParams();
    if (filterStatus.value) params.set("status", filterStatus.value);
    if (filterKind.value) params.set("kind", filterKind.value);
    if (filterTeam.value.trim()) params.set("team_id", filterTeam.value.trim());

    try {
      const resp = await fetch(`${apiBase}?${params.toString()}`, {
        headers: { "Accept": "application/json" },
        cache: "no-store"
      });
      if (!resp.ok) {
        throw new Error(`Server returned ${resp.status}`);
      }
      const res = await resp.json();
      if (!res.success) {
        throw new Error(res.error || "Failed to load evidence.");
      }
      renderRows(res.data);
      statusEl.textContent = `${res.data.length} item(s) found.`;
    } catch (err) {
      statusEl.textContent = `Error: ${err.message}`;
      tableWrap.innerHTML = "";
    }
  }

  function renderRows(items) {
    if (!items || items.length === 0) {
      tableWrap.innerHTML = '<div class="alert alert-info">No evidence records matching the filter.</div>';
      return;
    }

    let html = `
      <table class="table table-striped table-bordered">
        <thead>
          <tr>
            <th>ID</th>
            <th>Kind</th>
            <th>Studio / User</th>
            <th>Detail</th>
            <th>Address</th>
            <th>Recorded</th>
            <th>Status</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
    `;

    for (const item of items) {
      const studioLabel = item.team_name ? escapeHtml(item.team_name) : (item.team_id ? `Studio #${item.team_id}` : "-");
      const otherLabel = item.other_team_name ? `<br><small class="text-muted">vs ${escapeHtml(item.other_team_name)}</small>` : "";
      const userLabel = item.user_name ? `<br><small class="text-muted">${escapeHtml(item.user_name)}</small>` : "";

      html += `
        <tr data-id="${item.id}">
          <td data-label="ID">${item.id}</td>
          <td data-label="Kind"><span class="badge badge-secondary badge-kind">${escapeHtml(item.kind)}</span></td>
          <td data-label="Studio / User">${studioLabel}${otherLabel}${userLabel}</td>
          <td data-label="Detail">${escapeHtml(item.detail)}</td>
          <td data-label="Address">${escapeHtml(item.address || "-")}</td>
          <td data-label="Recorded">${escapeHtml(item.created_at || "-")}</td>
          <td data-label="Status" class="status-${escapeHtml(item.status)}">
            ${escapeHtml(item.status)}
            ${item.note ? `<br><small class="text-muted">${escapeHtml(item.note)}</small>` : ""}
          </td>
          <td data-label="Action">
            <div class="guard-actions">
              <input type="text" class="form-control form-control-sm note-input" placeholder="Review note" value="${escapeHtml(item.note || "")}">
              <button type="button" class="btn btn-sm btn-outline-info btn-action" data-status="reviewed">Review</button>
              <button type="button" class="btn btn-sm btn-outline-success btn-action" data-status="actioned">Action</button>
              <button type="button" class="btn btn-sm btn-outline-secondary btn-action" data-status="dismissed">Dismiss</button>
            </div>
          </td>
        </tr>
      `;
    }

    html += "</tbody></table>";
    tableWrap.innerHTML = html;

    // Attach action handlers
    const buttons = tableWrap.querySelectorAll(".btn-action");
    buttons.forEach(btn => {
      btn.addEventListener("click", onActionClick);
    });
  }

  async function onActionClick(evt) {
    const btn = evt.currentTarget;
    const tr = btn.closest("tr");
    const id = tr.dataset.id;
    const newStatus = btn.dataset.status;
    const noteInput = tr.querySelector(".note-input");
    const note = noteInput ? noteInput.value.trim() : "";

    if (!confirm(`Mark evidence #${id} as ${newStatus}?`)) {
      return;
    }

    btn.disabled = true;
    try {
      const resp = await fetch(`${apiBase}/${id}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json",
          "CSRF-Token": getCsrfToken()
        },
        body: JSON.stringify({ status: newStatus, note: note })
      });
      const res = await resp.json();
      if (!res.success) {
        alert(`Error: ${res.message || "Failed to update"}`);
      } else {
        await loadEvidence();
      }
    } catch (err) {
      alert(`Network error: ${err.message}`);
    } finally {
      btn.disabled = false;
    }
  }

  btnRefresh.addEventListener("click", loadEvidence);
  filterStatus.addEventListener("change", loadEvidence);
  filterKind.addEventListener("change", loadEvidence);

  loadEvidence();
})();
