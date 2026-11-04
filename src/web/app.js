"use strict";
/* NileOps dashboard. Every piece of server data is inserted with textContent
   (never as HTML strings), and the page's CSP forbids inline scripts. */

const TOKEN_KEY = "nileops_token";
const REFRESH_MS = 60000;
let memoryToken = null;
let refreshTimer = null;

const LABELS = {
  account: { pending: "قيد الانتظار", under_review: "تحت المراجعة", needs_information: "مطلوب استكمال", approved: "معتمد", rejected: "مرفوض", cancelled: "ملغى" },
  card: { pending: "قيد الانتظار", under_review: "تحت المراجعة", approved: "معتمد", rejected: "مرفوض", issued: "تم الإصدار المحاكى", cancelled: "ملغى" },
  complaint: { new: "جديدة", classified: "مصنّفة", assigned: "مُسندة", in_progress: "قيد المعالجة", resolved: "تم حلها", closed: "مغلقة" },
  priority: { urgent: "عاجلة", high: "عالية", medium: "متوسطة", low: "منخفضة" },
  accountType: { savings: "توفير", current: "جاري" },
  cardType: { debit: "خصم مباشر", prepaid: "مدفوعة مسبقًا" },
  role: { viewer: "مشاهد", reviewer: "مراجع", admin: "مدير", human: "موظف", system: "النظام", ai: "النظام" },
  entity: { kyc_application: "طلب اعرف عميلك", complaint: "شكوى", card_issuance_request: "طلب بطاقة", account_opening_request: "طلب فتح حساب", policy_query: "سياسات", application_explanation: "شرح طلب", complaint_explanation: "شرح شكوى", document: "مستند" },
  action: { HUMAN_REVIEW: "قرار مراجعة بشرية", POLICY_QUESTION: "سؤال عن سياسة", POLICY_RELOAD: "تحميل سياسات", EXPLANATION_VIEWED: "عرض شرح", synthetic_complaint_created: "إنشاء شكوى تجريبية", synthetic_request_created: "إنشاء طلب تجريبي", synthetic_account_created: "إنشاء حساب تجريبي", synthetic_customer_created: "إنشاء عميل تجريبي", SIMULATED_CARD_ISSUANCE: "إصدار بطاقة محاكى" },
};
const label = (group, key) => (LABELS[group] && LABELS[group][key]) || String(key);

/* ------------------------------------------------------------ DOM helpers */
function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}
const $ = (id) => document.getElementById(id);
const replace = (node, ...children) => node.replaceChildren(...children.flat().filter(Boolean));
const fmt = (n) => Number(n).toLocaleString("en");
const when = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : new Intl.DateTimeFormat("ar-EG-u-nu-latn", { dateStyle: "short", timeStyle: "short" }).format(d);
};

/* ------------------------------------------------------------------ token */
function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || memoryToken; } catch { return memoryToken; }
}
function setToken(token) {
  memoryToken = token;
  try { token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked: memory only */ }
}
function roleFromToken(token) {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload.role || "";
  } catch { return ""; }
}

/* -------------------------------------------------------------------- API */
class AuthError extends Error {}
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}), Authorization: `Bearer ${getToken()}` },
  });
  if (response.status === 401) throw new AuthError("انتهت الجلسة، سجّل الدخول مرة أخرى.");
  if (!response.ok) {
    let detail = "تعذر تنفيذ الطلب.";
    try { const body = await response.json(); if (typeof body.detail === "string") detail = body.detail; } catch { /* keep default */ }
    throw new Error(detail);
  }
  return response.json();
}

/* ---------------------------------------------------------------- widgets */
function bars(title, subtitle, counts, labeler = (k) => k) {
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return h("section", { class: "card" },
    h("h3", {}, title), subtitle ? h("p", { class: "sub" }, subtitle) : null,
    entries.length === 0 ? h("p", { class: "empty" }, "لا توجد بيانات.") :
      h("div", { class: "bars", role: "list" }, entries.map(([key, value]) =>
        h("div", { class: "bar-row", role: "listitem" },
          h("span", {}, labeler(key)),
          h("span", { class: "bar-track", "aria-hidden": "true" }, fillBar(value / max)),
          h("span", { class: "bar-num" }, fmt(value))))));
}
function fillBar(ratio) {
  const node = h("span", { class: "bar-fill" });
  node.style.width = `${Math.round(ratio * 100)}%`; // CSSOM is allowed under the CSP; inline style attributes are not
  return node;
}
function kpi(value, text, attention) {
  return h("div", { class: attention && value > 0 ? "kpi attention" : "kpi" }, h("div", { class: "value" }, fmt(value)), h("div", { class: "label" }, text));
}
function table(headers, rows) {
  if (rows.length === 0) return h("p", { class: "empty" }, "لا توجد عناصر مفتوحة.");
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((t) => h("th", { scope: "col" }, t)))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c) => h("td", {}, c)))))));
}
const pill = (text, cls) => h("span", { class: `pill ${cls || ""}` }, text);

/* ----------------------------------------------------------------- renders */
function renderOverview(s) {
  const k = s.kpis;
  replace($("tab-overview"),
    h("div", { class: "kpis" },
      kpi(k.open_reviews, "عناصر تنتظر مراجعة بشرية", true),
      kpi(k.urgent_open_complaints, "شكاوى عاجلة مفتوحة", true),
      kpi(k.ocr_fields_need_review, `حقول OCR تحتاج مراجعة (من ${fmt(k.ocr_fields_total)})`, false),
      kpi(k.active_policies, "سياسات معتمدة في قاعدة المعرفة", false)),
    h("div", { class: "grid2" },
      bars("طلبات اعرف عميلك", `الإجمالي ${fmt(s.applications.total)}`, s.applications.by_status),
      bars("حالة المستندات", `الإجمالي ${fmt(s.documents.total)}`, s.documents.by_status),
      bars("طلبات فتح الحساب", `الإجمالي ${fmt(s.account_requests.total)}`, s.account_requests.by_status, (x) => label("account", x)),
      bars("طلبات البطاقات", `الإجمالي ${fmt(s.card_requests.total)}`, s.card_requests.by_status, (x) => label("card", x)),
      bars("الشكاوى حسب الحالة", `الإجمالي ${fmt(s.complaints.total)}`, s.complaints.by_status, (x) => label("complaint", x)),
      bars("الشكاوى حسب الأولوية", "", s.complaints.by_priority, (x) => label("priority", x)),
      bars("الشكاوى حسب التصنيف", "", s.complaints.by_category)));
}
function renderQueues(s) {
  const explainBtn = (kind, id) => h("button", { type: "button", class: "linkish", onclick: () => showExplanation(kind, id) }, "شرح الحالة");
  replace($("tab-queues"),
    h("section", { class: "card" }, h("h3", {}, "طلبات اعرف عميلك المفتوحة"),
      table(["الرقم", "النوع", "الحالة", ""], s.queues.applications.map((r) => [r.number, r.kind, r.status, explainBtn("applications", r.id)]))),
    h("section", { class: "card" }, h("h3", {}, "الشكاوى المفتوحة (الأعلى أولوية أولًا)"),
      table(["الرقم", "التصنيف", "الأولوية", "الحالة", ""], s.queues.complaints.map((r) => [r.number, r.kind, pill(label("priority", r.priority), r.priority), label("complaint", r.status), explainBtn("complaints", r.id)]))),
    h("section", { class: "card" }, h("h3", {}, "طلبات البطاقات المفتوحة"),
      table(["الرقم", "النوع", "الحالة"], s.queues.cards.map((r) => [r.number, label("cardType", r.kind), label("card", r.status)]))),
    h("section", { class: "card" }, h("h3", {}, "طلبات فتح الحساب المفتوحة"),
      table(["الرقم", "النوع", "الحالة"], s.queues.accounts.map((r) => [r.number, label("accountType", r.kind), label("account", r.status)]))));
}
function renderActivity(s) {
  replace($("tab-activity"), h("section", { class: "card" }, h("h3", {}, "آخر الأنشطة"),
    h("p", { class: "sub" }, "الإجراء والجهة والوقت فقط، دون أي بيانات عملاء."),
    table(["الوقت", "العنصر", "الإجراء", "الجهة"], s.recent_activity.map((a) => [when(a.at), label("entity", a.entity), label("action", a.action), label("role", a.actor_role)]))));
}

/* ------------------------------------------------------------- explanation */
async function showExplanation(kind, id) {
  const body = $("detail-body");
  $("detail").hidden = false;
  replace(body, h("p", { class: "muted" }, "جارٍ التحميل..."));
  try {
    const e = await api(`/api/v1/explanations/${kind}/${id}`);
    replace(body,
      h("p", { class: "muted" }, `المرجع: ${e.reference}`),
      h("p", {}, e.summary),
      h("h3", {}, "تفاصيل"), h("ul", {}, e.points.map((p) => h("li", {}, p))),
      h("h3", {}, "الخطوات التالية"), h("ul", {}, e.next_steps.map((p) => h("li", {}, p))),
      e.citations.length ? h("h3", {}, "السند من السياسات") : null,
      e.citations.map((c) => h("blockquote", {}, h("strong", {}, `${c.policy_code} — الإصدار ${c.version} — ${c.heading}`), h("br"), c.excerpt)),
      e.note ? h("p", { class: "muted" }, e.note) : null,
      h("p", { class: "muted" }, `${e.advisory} (الأسلوب: ${e.mode === "llm" ? "صياغة النموذج المحلي" : "الشرح الجاهز"})`));
  } catch (err) {
    if (err instanceof AuthError) return endSession(err.message);
    replace(body, h("p", { class: "error", role: "alert" }, err.message));
  }
}

/* ----------------------------------------------------------- policy helper */
async function askPolicy(event) {
  event.preventDefault();
  const out = $("ask-result");
  replace(out, h("p", { class: "muted" }, "جارٍ البحث في السياسات..."));
  try {
    const r = await api("/api/v1/policies/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: $("question").value.trim() }) });
    replace(out, h("section", { class: r.status === "answered" ? "card" : "card abstain" },
      h("h3", {}, r.status === "answered" ? "الإجابة" : "لا توجد إجابة"),
      h("p", { class: "answer" }, r.answer),
      r.citations.length ? h("h3", {}, "المصادر") : null,
      r.citations.map((c) => h("blockquote", {}, h("strong", {}, `[${c.number}] ${c.title} — ${c.policy_code} الإصدار ${c.version} — ${c.heading}`), h("br"), c.excerpt)),
      r.note ? h("p", { class: "muted" }, r.note) : null,
      h("p", { class: "muted" }, r.advisory)));
  } catch (err) {
    if (err instanceof AuthError) return endSession(err.message);
    replace(out, h("p", { class: "error", role: "alert" }, err.message));
  }
}

/* -------------------------------------------------------------- session/tabs */
async function load() {
  $("status-line").textContent = "جارٍ التحديث...";
  try {
    const summary = await api("/api/v1/dashboard/summary");
    renderOverview(summary); renderQueues(summary); renderActivity(summary);
    $("status-line").textContent = `آخر تحديث: ${when(summary.generated_at)}`;
  } catch (err) {
    if (err instanceof AuthError) return endSession(err.message);
    $("status-line").textContent = err.message;
  }
}
function startSession() {
  const role = roleFromToken(getToken());
  $("who").textContent = role ? `الدور: ${label("role", role)}` : "";
  $("login").hidden = true; $("app").hidden = false; $("session").hidden = false;
  load();
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
}
function endSession(message) {
  clearInterval(refreshTimer);
  setToken(null);
  $("app").hidden = true; $("session").hidden = true; $("detail").hidden = true; $("login").hidden = false;
  $("password").value = "";
  const box = $("login-error");
  box.textContent = message || ""; box.hidden = !message;
}
async function login(event) {
  event.preventDefault();
  const box = $("login-error"); box.hidden = true;
  try {
    const response = await fetch("/api/v1/auth/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ username: $("username").value.trim(), password: $("password").value }) });
    if (!response.ok) throw new Error(response.status === 401 ? "اسم المستخدم أو كلمة المرور غير صحيحة." : "تعذر تسجيل الدخول الآن.");
    setToken((await response.json()).access_token);
    $("password").value = "";
    startSession();
  } catch (err) { box.textContent = err.message; box.hidden = false; }
}
function selectTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active));
    $(`tab-${tab.dataset.tab}`).hidden = !active;
  }
}

$("login-form").addEventListener("submit", login);
$("logout").addEventListener("click", () => endSession(""));
$("refresh").addEventListener("click", load);
$("ask-form").addEventListener("submit", askPolicy);
$("detail-close").addEventListener("click", () => { $("detail").hidden = true; });
for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => selectTab(tab.dataset.tab));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("detail").hidden = true; });
if (getToken()) startSession();
