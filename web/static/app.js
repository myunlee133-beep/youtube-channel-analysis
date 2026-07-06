const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  activeRunId: null,
  pollTimer: null,
};

const els = {
  form: $("#analysisForm"),
  channel: $("#channelInput"),
  maxVideos: $("#maxVideosInput"),
  sleep: $("#sleepInput"),
  withComments: $("#withCommentsInput"),
  commentOptions: $("#commentOptions"),
  commentVideos: $("#commentVideosInput"),
  maxComments: $("#maxCommentsInput"),
  serverState: $("#serverState"),
  historyList: $("#historyList"),
  refreshRuns: $("#refreshRunsButton"),
  jobPanel: $("#jobPanel"),
  jobTitle: $("#jobTitle"),
  jobSubtitle: $("#jobSubtitle"),
  jobPercent: $("#jobPercent"),
  progressBar: $("#progressBar"),
  logList: $("#logList"),
  emptyState: $("#emptyState"),
  resultArea: $("#resultArea"),
  resultRunId: $("#resultRunId"),
  resultTitle: $("#resultTitle"),
  resultSubtitle: $("#resultSubtitle"),
  resultActions: $("#resultActions"),
  metricGrid: $("#metricGrid"),
  summaryView: $("#summaryView"),
  chartsView: $("#chartsView"),
  videosView: $("#videosView"),
  filesView: $("#filesView"),
};

function fmtNumber(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Number(value).toLocaleString("ko-KR");
}

function fmtPercent(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${Number(value).toLocaleString("ko-KR")}%`;
}

function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setServerState(text, tone = "idle") {
  els.serverState.textContent = text;
  const color = tone === "busy" ? "#b36b00" : tone === "error" ? "#c7352f" : "#667276";
  els.serverState.style.color = color;
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "요청 실패");
  }
  return payload;
}

function toggleCommentFields() {
  const enabled = els.withComments.checked;
  els.commentOptions.classList.toggle("enabled", enabled);
  els.commentVideos.disabled = !enabled;
  els.maxComments.disabled = !enabled;
}

function formPayload() {
  return {
    channel: els.channel.value.trim(),
    maxVideos: Number(els.maxVideos.value),
    sleep: Number(els.sleep.value),
    withComments: els.withComments.checked,
    commentVideos: Number(els.commentVideos.value),
    maxComments: Number(els.maxComments.value),
  };
}

function showJobPanel() {
  els.jobPanel.classList.remove("hidden");
}

function renderLogs(logs = []) {
  els.logList.innerHTML = logs
    .slice(-80)
    .map((line) => `<li><span>${escapeHtml(line.time)}</span><span>${escapeHtml(line.message)}</span></li>`)
    .join("");
  els.logList.scrollTop = els.logList.scrollHeight;
}

function renderJob(job) {
  const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
  els.jobTitle.textContent = job.status === "failed" ? "작업 실패" : job.status === "complete" ? "작업 완료" : "작업 진행 중";
  els.jobSubtitle.textContent = job.options?.channel || job.run_id || "";
  els.jobPercent.textContent = `${progress}%`;
  els.progressBar.style.width = `${progress}%`;
  renderLogs(job.logs);
  showJobPanel();

  if (job.status === "failed") {
    setServerState("실패", "error");
    els.form.querySelector("button").disabled = false;
  } else if (job.status === "complete") {
    setServerState("완료");
    els.form.querySelector("button").disabled = false;
  } else {
    setServerState("실행 중", "busy");
  }
}

async function pollJob(jobId) {
  window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(async () => {
    try {
      const job = await fetchJSON(`/api/jobs/${encodeURIComponent(jobId)}`);
      renderJob(job);
      if (job.status === "complete") {
        window.clearInterval(state.pollTimer);
        await loadRuns();
        await loadRun(job.run_id);
      }
      if (job.status === "failed") {
        window.clearInterval(state.pollTimer);
      }
    } catch (error) {
      window.clearInterval(state.pollTimer);
      setServerState("오류", "error");
      renderLogs([{ time: "error", message: error.message }]);
    }
  }, 1200);
}

function historyStatusLabel(status) {
  if (status === "sample") return "샘플";
  if (status === "complete") return "완료";
  if (status === "failed") return "실패";
  return status || "기록";
}

function renderHistory(runs) {
  if (!runs.length) {
    els.historyList.innerHTML = `<p class="muted">저장된 분석이 없습니다.</p>`;
    return;
  }
  els.historyList.innerHTML = runs
    .map((run) => `
      <button class="history-item ${run.run_id === state.activeRunId ? "active" : ""}" type="button" data-run-id="${escapeHtml(run.run_id)}">
        <strong>${escapeHtml(run.channel || run.run_id)}</strong>
        <span class="history-meta">
          <span>${escapeHtml(historyStatusLabel(run.status))}</span>
          <span>${fmtNumber(run.video_count)}개 영상</span>
          <span>${fmtNumber(run.comment_count)}개 댓글</span>
        </span>
      </button>
    `)
    .join("");
}

async function loadRuns() {
  const payload = await fetchJSON("/api/runs");
  renderHistory(payload.runs || []);
  if (!state.activeRunId && payload.runs?.length) {
    const preferred = payload.runs.find((run) => run.status === "complete")
      || payload.runs.find((run) => run.status === "sample")
      || payload.runs[0];
    await loadRun(preferred.run_id);
  }
}

function metric(label, value) {
  return `
    <div class="metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function videoRows(videos) {
  if (!videos?.length) {
    return `<tr><td colspan="7" class="muted">표시할 영상이 없습니다.</td></tr>`;
  }
  return videos
    .map((video) => `
      <tr>
        <td>
          <a class="video-link" href="${escapeHtml(video.url || "#")}" target="_blank" rel="noreferrer">
            ${escapeHtml(video.title || "(제목 없음)")}
          </a>
        </td>
        <td>${escapeHtml(video.upload_date || "-")}</td>
        <td class="number">${fmtNumber(video.view_count)}</td>
        <td class="number">${fmtNumber(video.like_count)}</td>
        <td class="number">${fmtNumber(video.comment_count)}</td>
        <td class="number">${video.duration_min ?? "-"}</td>
        <td class="number">${video.like_rate ?? "-"}</td>
      </tr>
    `)
    .join("");
}

function renderVideoTable(title, videos) {
  return `
    <section class="table-panel">
      <h3>${escapeHtml(title)}</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>제목</th>
              <th>업로드</th>
              <th>조회수</th>
              <th>좋아요</th>
              <th>댓글</th>
              <th>길이(분)</th>
              <th>좋아요율</th>
            </tr>
          </thead>
          <tbody>${videoRows(videos)}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderSummary(result) {
  const top = result.stats.top_video;
  const topVideo = top
    ? `
      <section class="top-video">
        <h3>최고 조회수 영상</h3>
        <a class="video-link" href="${escapeHtml(top.url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(top.title)}</a>
        <div class="detail-list">
          <span>조회수 ${fmtNumber(top.view_count)}회</span>
          <span>업로드 ${escapeHtml(top.upload_date || "-")}</span>
          <span>길이 ${escapeHtml(top.duration_min ?? "-")}분</span>
        </div>
      </section>
    `
    : `
      <section class="top-video">
        <h3>최고 조회수 영상</h3>
        <p class="muted">계산할 조회수 데이터가 없습니다.</p>
      </section>
    `;

  els.summaryView.innerHTML = `
    <div class="summary-grid">
      <section class="summary-box">
        <pre>${escapeHtml(result.summary_markdown || "summary.md가 아직 없습니다.")}</pre>
      </section>
      ${topVideo}
    </div>
  `;
}

function renderCharts(result) {
  if (!result.charts?.length) {
    els.chartsView.innerHTML = `<p class="muted">생성된 차트가 없습니다.</p>`;
    return;
  }
  els.chartsView.innerHTML = `
    <div class="chart-grid">
      ${result.charts
        .map((chart) => `
          <article class="chart-tile">
            <h3>${escapeHtml(chart.label)}</h3>
            <a href="${escapeHtml(chart.url)}" target="_blank" rel="noreferrer">
              <img src="${escapeHtml(chart.url)}" alt="${escapeHtml(chart.label)}">
            </a>
          </article>
        `)
        .join("")}
    </div>
  `;
}

function renderVideos(result) {
  els.videosView.innerHTML = `
    <div class="table-stack">
      ${renderVideoTable("조회수 상위 영상", result.top_videos)}
      ${renderVideoTable("최근 업로드 영상", result.recent_videos)}
    </div>
  `;
}

function renderFiles(result) {
  if (!result.files?.length) {
    els.filesView.innerHTML = `<p class="muted">다운로드할 파일이 없습니다.</p>`;
    return;
  }
  els.filesView.innerHTML = `
    <section class="file-panel">
      <h3>다운로드</h3>
      <div class="file-list">
        ${result.files
          .map((file) => `
            <div class="file-row">
              <strong>${escapeHtml(file.name)}</strong>
              <span class="file-kind">${escapeHtml(file.kind)} · ${fmtBytes(file.size)}</span>
              <a class="download-button" href="${escapeHtml(file.url)}">받기</a>
            </div>
          `)
          .join("")}
      </div>
    </section>
  `;
}

function renderActions(result) {
  const summary = result.files?.find((file) => file.name === "summary.md");
  const videos = result.files?.find((file) => file.name === "videos.csv");
  const comments = result.files?.find((file) => file.name === "comments.csv");
  const actions = [summary, videos, comments]
    .filter(Boolean)
    .map((file) => `<a class="download-button" href="${escapeHtml(file.url)}">${escapeHtml(file.name)}</a>`)
    .join("");
  els.resultActions.innerHTML = actions;
}

function renderMetrics(stats) {
  els.metricGrid.innerHTML = [
    metric("영상", `${fmtNumber(stats.video_count)}개`),
    metric("댓글", `${fmtNumber(stats.comment_count)}개`),
    metric("총 조회수", fmtNumber(stats.total_views)),
    metric("평균 조회수", fmtNumber(stats.avg_views)),
    metric("숏폼 비율", fmtPercent(stats.short_ratio)),
  ].join("");
}

function renderRun(result) {
  state.activeRunId = result.run_id;
  els.emptyState.classList.add("hidden");
  els.resultArea.classList.remove("hidden");

  const stats = result.stats || {};
  els.resultRunId.textContent = result.is_sample ? "현재 로컬 샘플" : result.run_id;
  els.resultTitle.textContent = stats.channel || result.meta?.channel_target || "채널";
  const period = stats.date_start && stats.date_end ? `${stats.date_start} ~ ${stats.date_end}` : "기간 없음";
  els.resultSubtitle.textContent = `${period} · 평균 길이 ${stats.avg_duration_min ?? "-"}분 · 좋아요율 ${stats.avg_like_rate ?? "-"}%`;

  renderActions(result);
  renderMetrics(stats);
  renderSummary(result);
  renderCharts(result);
  renderVideos(result);
  renderFiles(result);
  renderHistoryItemState();
}

function renderHistoryItemState() {
  $$(".history-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.runId === state.activeRunId);
  });
}

async function loadRun(runId) {
  const result = await fetchJSON(`/api/runs/${encodeURIComponent(runId)}`);
  renderRun(result);
  renderHistoryItemState();
}

function setActiveView(name) {
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === name));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `${name}View`));
}

els.withComments.addEventListener("change", toggleCommentFields);
els.refreshRuns.addEventListener("click", () => loadRuns().catch((error) => setServerState(error.message, "error")));
els.historyList.addEventListener("click", (event) => {
  const button = event.target.closest(".history-item");
  if (!button) return;
  loadRun(button.dataset.runId).catch((error) => setServerState(error.message, "error"));
});

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => setActiveView(tab.dataset.view));
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    els.form.querySelector("button").disabled = true;
    setServerState("시작", "busy");
    const job = await fetchJSON("/api/analyze", {
      method: "POST",
      body: JSON.stringify(formPayload()),
    });
    renderJob(job);
    await pollJob(job.job_id);
  } catch (error) {
    els.form.querySelector("button").disabled = false;
    setServerState("오류", "error");
    showJobPanel();
    renderLogs([{ time: "error", message: error.message }]);
  }
});

toggleCommentFields();
loadRuns()
  .then(() => setServerState("대기"))
  .catch((error) => {
    setServerState("오류", "error");
    els.emptyState.querySelector("p").textContent = error.message;
  });
