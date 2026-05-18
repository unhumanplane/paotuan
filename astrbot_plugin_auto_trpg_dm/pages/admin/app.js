(function () {
  "use strict";

  var bridge = null;
  var bridgeReady = false;
  var selectedSessionKey = "";

  var els = {
    metricSessions: document.getElementById("metric-sessions"),
    metricBattles: document.getElementById("metric-battles"),
    metricAudits: document.getElementById("metric-audits"),
    metricBackups: document.getElementById("metric-backups"),
    sessionCount: document.getElementById("session-count"),
    sessionList: document.getElementById("session-list"),
    emptyDetail: document.getElementById("empty-detail"),
    sessionDetail: document.getElementById("session-detail"),
    detailTitle: document.getElementById("detail-title"),
    detailMeta: document.getElementById("detail-meta"),
    detailMode: document.getElementById("detail-mode"),
    snapshotTimeline: document.getElementById("snapshot-timeline"),
    snapshotCycle: document.getElementById("snapshot-cycle"),
    snapshotCounts: document.getElementById("snapshot-counts"),
    snapshotTracking: document.getElementById("snapshot-tracking"),
    snapshotScene: document.getElementById("snapshot-scene"),
    snapshotBattle: document.getElementById("snapshot-battle"),
    snapshotCharacters: document.getElementById("snapshot-characters"),
    auditList: document.getElementById("audit-list"),
    backupList: document.getElementById("backup-list"),
    toast: document.getElementById("toast"),
  };

  document.getElementById("refresh-all").addEventListener("click", function () {
    refreshAll();
  });

  document.querySelectorAll(".tab-button").forEach(function (button) {
    button.addEventListener("click", function () {
      document.querySelectorAll(".tab-button").forEach(function (item) {
        item.classList.remove("active");
      });
      document.querySelectorAll(".tab-panel").forEach(function (item) {
        item.classList.remove("active");
      });
      button.classList.add("active");
      document.getElementById("tab-" + button.dataset.tab).classList.add("active");
      if (selectedSessionKey && button.dataset.tab === "audit") {
        loadAudit(selectedSessionKey);
      }
      if (selectedSessionKey && button.dataset.tab === "backups") {
        loadBackups(selectedSessionKey);
      }
    });
  });

  async function refreshAll() {
    await loadStatus();
    await loadSessions();
    if (selectedSessionKey) {
      await openSession(selectedSessionKey);
    }
  }

  async function loadStatus() {
    var data = await apiGet("dm/web/status");
    if (!data) return;
    els.metricSessions.textContent = data.session_count || 0;
    els.metricBattles.textContent = data.active_battle_count || 0;
    els.metricAudits.textContent = data.audit_file_count || 0;
    els.metricBackups.textContent = data.backup_file_count || 0;
  }

  async function loadSessions() {
    els.sessionList.innerHTML = skeleton(6);
    var sessions = await apiGet("dm/web/sessions");
    if (!sessions) {
      els.sessionList.innerHTML = emptyHtml("会话加载失败。");
      els.sessionCount.textContent = "加载失败";
      return;
    }
    els.sessionCount.textContent = sessions.length + " 个";
    if (sessions.length === 0) {
      els.sessionList.innerHTML = emptyHtml("暂无存档会话。");
      return;
    }
    els.sessionList.innerHTML = sessions.map(renderSessionButton).join("");
    els.sessionList.querySelectorAll(".session-item").forEach(function (button) {
      button.addEventListener("click", function () {
        openSession(button.dataset.key);
      });
    });
    if (!selectedSessionKey && sessions[0]) {
      openSession(sessions[0].session_key);
    }
  }

  function renderSessionButton(item) {
    var active = item.session_key === selectedSessionKey ? " active" : "";
    var battle = item.battle_active ? '<span class="pill warn">战斗</span>' : '<span class="pill good">叙事</span>';
    return '<button class="session-item' + active + '" data-key="' + esc(item.session_key) + '">' +
      '<div class="session-title-row"><span class="session-title">' + esc(item.title || "未命名团") + "</span>" + battle + "</div>" +
      '<div class="session-id">' + esc(item.session_id || item.session_key) + "</div>" +
      '<div class="subtle">' + esc(item.timeline_text || "") + "</div>" +
      '<div class="subtle">' + esc(item.current_objective || "暂无当前目标") + "</div>" +
      "</button>";
  }

  async function openSession(sessionKey) {
    selectedSessionKey = sessionKey;
    els.sessionList.querySelectorAll(".session-item").forEach(function (item) {
      item.classList.toggle("active", item.dataset.key === sessionKey);
    });
    var snapshot = await apiGet("dm/web/sessions/" + encodeURIComponent(sessionKey) + "/snapshot");
    if (!snapshot) return;
    renderSnapshot(snapshot);
    await loadAudit(sessionKey);
    await loadBackups(sessionKey);
  }

  function renderSnapshot(snapshot) {
    els.emptyDetail.classList.add("hidden");
    els.sessionDetail.classList.remove("hidden");
    els.detailTitle.textContent = snapshot.title || "未命名团";
    els.detailMeta.textContent = (snapshot.session_id || "") + " · 更新 " + (snapshot.updated_at || "");
    els.detailMode.textContent = snapshot.mode || "-";
    els.snapshotTimeline.textContent = snapshot.timeline_text || "-";
    els.snapshotCycle.textContent = (snapshot.cycle_state || "-") + " / #" + String(snapshot.current_cycle_id || 0);
    els.snapshotCounts.textContent = String((snapshot.characters || []).length) + " / " + String((snapshot.participants || []).length);
    els.snapshotTracking.textContent = snapshot.scene_tracking_status || "暂无场景追踪。";
    els.snapshotScene.textContent = pretty(snapshot.visible_scene || {});
    els.snapshotBattle.textContent = pretty(snapshot.battle || {});
    els.snapshotCharacters.innerHTML = renderCharacters(snapshot.characters || []);
  }

  function renderCharacters(characters) {
    if (!characters.length) return emptyHtml("暂无角色。");
    return characters.map(function (character) {
      return '<div class="character">' +
        "<strong>" + esc(character.name || character.character_id || "未命名角色") + "</strong>" +
        '<div class="subtle">' + esc(character.character_id || "") + "</div>" +
        '<div>' + esc(character.summary || "暂无摘要") + "</div>" +
        "</div>";
    }).join("");
  }

  async function loadAudit(sessionKey) {
    els.auditList.innerHTML = skeleton(4);
    var records = await apiGet("dm/web/sessions/" + encodeURIComponent(sessionKey) + "/audit");
    if (!records) {
      els.auditList.innerHTML = emptyHtml("审计记录加载失败。");
      return;
    }
    if (!records.length) {
      els.auditList.innerHTML = emptyHtml("暂无审计记录。");
      return;
    }
    els.auditList.innerHTML = records.slice().reverse().map(function (record) {
      var title = record.tool || record.action || record.type || "audit";
      var status = record.ok === false ? '<span class="pill warn">失败</span>' : '<span class="pill good">记录</span>';
      return '<article class="event">' +
        '<div class="event-head"><span class="event-title">' + esc(title) + "</span>" + status + "</div>" +
        '<div class="event-time">' + esc(record.at || "") + "</div>" +
        (record.message ? '<div class="event-message">' + esc(record.message) + "</div>" : "") +
        (record.error ? '<div class="event-message">错误: ' + esc(record.error) + "</div>" : "") +
        '<pre class="json-panel">' + esc(pretty(record.record || {})) + "</pre>" +
        "</article>";
    }).join("");
  }

  async function loadBackups(sessionKey) {
    els.backupList.innerHTML = skeleton(4);
    var backups = await apiGet("dm/web/sessions/" + encodeURIComponent(sessionKey) + "/backups");
    if (!backups) {
      els.backupList.innerHTML = emptyHtml("备份列表加载失败。");
      return;
    }
    if (!backups.length) {
      els.backupList.innerHTML = emptyHtml("暂无备份。");
      return;
    }
    els.backupList.innerHTML = backups.map(function (backup) {
      return '<article class="event">' +
        '<div class="event-head"><span class="event-title">' + esc(backup.name || "backup") + '</span><span class="pill">' + esc(formatBytes(backup.size || 0)) + "</span></div>" +
        '<div class="event-time">' + esc(backup.created_at || backup.mtime || "") + "</div>" +
        '<div class="event-message">' + esc(backup.reason || "无原因记录") + "</div>" +
        "</article>";
    }).join("");
  }

  async function apiGet(endpoint) {
    try {
      var result = bridgeReady
        ? await bridge.apiGet(endpoint, {})
        : await fetchPluginApi(endpoint);
      return unwrap(result);
    } catch (error) {
      toast(error.message || "请求失败");
      return null;
    }
  }

  async function fetchPluginApi(endpoint) {
    var token = window.localStorage ? localStorage.getItem("token") : "";
    if (!token) {
      throw new Error("未登录 AstrBot Dashboard");
    }
    var response = await fetch("/api/plug/auto_trpg_dm/" + endpoint.replace(/^\/+/, ""), {
      headers: { Authorization: "Bearer " + token },
    });
    var data = await response.json().catch(function () {
      return { status: "error", message: "响应不是 JSON" };
    });
    if (!response.ok) {
      throw new Error(data.message || ("HTTP " + response.status));
    }
    return data;
  }

  function unwrap(result) {
    if (result && result.status === "ok" && Object.prototype.hasOwnProperty.call(result, "data")) {
      return result.data;
    }
    if (result && result.status === "error") {
      throw new Error(result.message || "请求失败");
    }
    return result;
  }

  function skeleton(count) {
    var html = "";
    for (var i = 0; i < count; i++) {
      html += '<div class="event"><div class="subtle">加载中...</div></div>';
    }
    return html;
  }

  function emptyHtml(text) {
    return '<div class="empty"><p>' + esc(text) + "</p></div>";
  }

  function pretty(value) {
    return JSON.stringify(value || {}, null, 2);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatBytes(value) {
    var size = Number(value || 0);
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return Math.round(size / 1024) + " KB";
    return (size / 1024 / 1024).toFixed(1) + " MB";
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.classList.add("show");
    clearTimeout(els.toast._timer);
    els.toast._timer = setTimeout(function () {
      els.toast.classList.remove("show");
    }, 3500);
  }

  function waitForBridge(maxWaitMs) {
    return new Promise(function (resolve, reject) {
      var started = Date.now();
      function check() {
        if (window.AstrBotPluginPage) {
          resolve(window.AstrBotPluginPage);
          return;
        }
        if (Date.now() - started > maxWaitMs) {
          reject(new Error("Bridge SDK 加载超时"));
          return;
        }
        setTimeout(check, 200);
      }
      check();
    });
  }

  async function init() {
    try {
      bridge = await waitForBridge(1200);
      await Promise.race([
        bridge.ready(),
        new Promise(function (resolve) { setTimeout(resolve, 3000); }),
      ]);
      bridgeReady = true;
    } catch (_error) {
      bridgeReady = false;
    }

    await refreshAll();
    if (!bridgeReady) {
      toast("已使用直接访问模式");
    }
  }

  init();
})();
