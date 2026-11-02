"use strict";
// Resolution and local download share layout, while the server enforces their boundary.
const baseConfig = config;
config = async function () {
  const data = await baseConfig();
  state.resolveOnly = Boolean(data.resolve_only);
  if (!state.resolveOnly) return data;
  document.title = "拾流 · 仅解析资源地址";
  $("h1").textContent = "视频链接解析";
  $(".page-title p").textContent = "服务端只读取页面与清单，视频由你的电脑从原站下载。";
  $(".breadcrumb").textContent = "工作空间 / 资源解析";
  $(".local-badge").textContent = "仅解析模式";
  $(".nav-item.active").lastChild.textContent = "资源解析";
  $("#composer-title").innerHTML = icon("link") + "解析视频链接";
  $("#submit-button span").textContent = "开始解析";
  $(".input-hint").textContent = "仅返回地址与下载参数，不在服务端下载或合并视频。";
  $(".download-options").hidden = true;
  $("#settings-button").hidden = true;
  $("#open-downloads").hidden = true;
  $("#task-title").firstChild.textContent = "解析任务 ";
  $(".queue-note").textContent = "仅处理网页与播放清单";
  $("#storage-caption").textContent = "保存策略";
  $("#output-path").textContent = "仅保留 30 分钟解析结果，不保存视频";
  $("#offline-banner").textContent = "解析服务暂时无法连接。双击 start_resolver.cmd 启动后，页面会自动连接。";
  $("#site-records").innerHTML = [
    ["文章页与 HLS", "文章 → 播放器 → 清单，保留源站分段地址"],
    ["YouTube / 抖音 / B站", "匿名读取可用格式，不使用个人保存会话"],
    ["X / Twitter", "匿名接口与指定公开解析服务，只获取地址"],
    ["动态播放器", "限制媒体加载，只读取配置与清单"],
    ["需要登录或网站验证", "导出本机任务，由你自己的电脑重新解析"]
  ].map(([name, note]) => `<div class="site-record"><div><h3>${escapeHTML(name)}</h3><small>${escapeHTML(note)}</small></div><span class="verification">仅解析</span></div>`).join("");
  return data;
};

function resolvedContent(job) {
  const result = job.resolution;
  if (!result) return `<p class="task-note">${escapeHTML(job.message || "暂无解析结果。")}</p>`;
  const note = result.sources.some(item => item.role === "video" || item.role === "audio")
    ? "视频与音轨需要在本机分别下载并合并。"
    : result.sources.some(item => item.kind === "hls" || item.kind === "dash")
      ? "这是播放清单，视频分段仍需在本机下载、合并。"
      : "已找到媒体文件地址，可由浏览器或本机下载器从源站下载。";
  const rows = result.sources.map((item, index) => {
    const label = item.role === "audio" ? "音轨地址" : item.role === "video" ? "视频轨地址" : item.kind === "hls" ? "m3u8 播放清单" : item.kind === "dash" ? "DASH 清单" : "媒体文件地址";
    return `<div class="resolved-source"><div><strong>${label}${item.height ? ` · ${item.height}p` : ""}</strong><small>${escapeHTML(hostname(item.url))}</small></div><button class="small-button" data-action="copy-source" data-id="${job.id}" data-index="${index}">复制地址</button></div>`;
  }).join("");
  return `<div class="resolved-result">${result.status === "local_required" ? `<p class="task-note waiting-note">服务端未取得可直接交付的资源。导出任务后，由本机工具重新解析。</p>` : rows}<p class="task-note">${result.status === "ready" ? note : "登录和网站验证由你在自己的电脑完成。"}</p><div class="resolution-actions"><a class="small-button" href="/api/tasks/${job.id}/plan" download>导出本机任务 JSON ↓</a><span>用 download_plan.cmd 打开</span></div><small class="resolution-disclaimer">资源地址可能过期或绑定 IP；跨网络可用性尚未验证。</small></div>`;
}

const baseTaskCard = taskCard;
taskCard = function (job) {
  if (job.kind !== "resolve") return baseTaskCard(job);
  const completed = job.status === "completed";
  const status = completed ? job.resolution?.status === "ready" ? "已解析" : job.resolution_expired ? "已过期" : "需本机处理" : labels[job.status];
  return `<article class="task-card ${job.status}" data-job="${job.id}"><div class="task-symbol">URL</div><div class="task-content"><div class="task-first"><h3 class="task-name"><button data-action="detail" data-id="${job.id}">${escapeHTML(job.route)} · 资源解析</button></h3><span class="task-status">${status}</span></div><div class="task-meta"><span class="url-short">${escapeHTML(hostname(job.url))}</span><span>·</span><span>${date(job.created)}</span></div>${job.status === "running" ? progress(job) : ""}${completed ? resolvedContent(job) : job.error ? `<p class="task-error">${escapeHTML(job.error)}</p>` : ""}</div><div class="task-actions">${actions(job)}<button class="small-button" data-action="detail" data-id="${job.id}">详情</button></div></article>`;
};

const baseDetail = refreshDetail;
refreshDetail = async function () {
  const current = state.tasks.find(job => job.id === state.selected);
  if (!current || current.kind !== "resolve") return baseDetail();
  if (!$("#detail-dialog").open) return;
  const job = await api(`/api/tasks/${state.selected}`);
  const expanded = $("#detail-body .log-section")?.open;
  $("#detail-title").textContent = job.route + " · 资源解析";
  $("#detail-kind").textContent = date(job.created);
  $("#detail-body").innerHTML = `<p class="detail-url">${escapeHTML(job.url)}</p>${active.has(job.status) ? progress(job) : resolvedContent(job)}${job.error || job.resolution?.reason ? `<div class="detail-error">${escapeHTML(job.error || job.resolution.reason)}</div>` : ""}<details class="log-section" ${expanded ? "open" : ""}><summary>解析记录</summary><pre>${escapeHTML(job.log || "暂无记录。")}</pre></details>`;
};

document.addEventListener("click", event => {
  const button = event.target.closest('[data-action="copy-source"]');
  if (!button) return;
  withButton(button, async () => {
    const job = state.tasks.find(item => item.id === button.dataset.id);
    const address = job?.resolution?.sources[Number(button.dataset.index)]?.url;
    if (!address) throw new Error("地址不存在或已过期，请重新解析。");
    await navigator.clipboard.writeText(address);
    toast("资源地址已复制；需要请求参数时，请导出完整的本机任务。");
  });
});
// Also apply the mode when the initial config request finished before this script loaded.
config().catch(() => {});
