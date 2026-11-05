/* Shared helpers for the NileOps dashboard (ES module).
   Rule: server data is only ever inserted as text nodes, never as HTML strings. */

export const TOKEN_KEY = "nileops_token";
let memoryToken = null;

/* ------------------------------------------------------------------ DOM */
export function h(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === false || value == null) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}
export const $ = (id) => document.getElementById(id);
export const replace = (node, ...children) => node.replaceChildren(...children.flat(Infinity).filter(Boolean));
export const fmt = (n) => Number(n).toLocaleString("en");
export const when = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : new Intl.DateTimeFormat("ar-EG-u-nu-latn", { dateStyle: "short", timeStyle: "short" }).format(d);
};
export const bytes = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

/* ---------------------------------------------------------------- labels */
export const LABELS = {
  account: { pending: "قيد الانتظار", under_review: "تحت المراجعة", needs_information: "مطلوب استكمال", approved: "معتمد", rejected: "مرفوض", cancelled: "ملغى" },
  card: { pending: "قيد الانتظار", under_review: "تحت المراجعة", approved: "معتمد", rejected: "مرفوض", issued: "تم الإصدار المحاكى", cancelled: "ملغى" },
  complaint: { new: "جديدة", classified: "مصنّفة (تنتظر الفرز)", assigned: "مُسندة", in_progress: "قيد المعالجة", resolved: "تم حلها", closed: "مغلقة" },
  priority: { urgent: "عاجلة", high: "عالية", medium: "متوسطة", low: "منخفضة" },
  accountType: { savings: "توفير", current: "جاري" },
  cardType: { debit: "خصم مباشر", prepaid: "مدفوعة مسبقًا" },
  role: { viewer: "مشاهد", reviewer: "مراجع", admin: "مدير", human: "موظف", system: "النظام", ai: "النظام" },
  field: { full_name: "الاسم", national_id: "الرقم القومي", address: "العنوان", id_expiry_date: "تاريخ انتهاء البطاقة", mobile: "رقم الموبايل" },
  entity: { kyc_application: "طلب اعرف عميلك", complaint: "شكوى", card_issuance_request: "طلب بطاقة", account_opening_request: "طلب فتح حساب", policy_query: "سياسات", application_explanation: "شرح طلب", complaint_explanation: "شرح شكوى", explanation_application: "شرح طلب", explanation_complaint: "شرح شكوى", document: "مستند", user: "مستخدم", auth: "الدخول" },
  action: {
    HUMAN_REVIEW: "قرار مراجعة بشرية", DOCUMENT_HUMAN_REVIEW: "مراجعة مستند", POLICY_QUESTION: "سؤال عن سياسة", POLICY_RELOAD: "تحميل سياسات",
    EXPLANATION_VIEWED: "عرض شرح", EXPLANATION_GENERATED: "إعداد شرح", DOCUMENT_UPLOADED: "رفع مستند", DOCUMENT_OCR_EXTRACTED: "قراءة مستند آليًا", SIMULATED_CARD_ISSUANCE: "إصدار بطاقة محاكى", USER_CREATED: "إنشاء مستخدم", USER_UPDATED: "تعديل مستخدم",
    PASSWORD_RESET: "إعادة تعيين كلمة مرور", LOGIN_LOCKED: "إيقاف الدخول مؤقتًا", synthetic_complaint_created: "إنشاء شكوى تجريبية",
    synthetic_request_created: "إنشاء طلب تجريبي", synthetic_account_created: "إنشاء حساب تجريبي", synthetic_customer_created: "إنشاء عميل تجريبي",
  },
  ocr: { not_run: "لم تُقرأ", completed: "تمت القراءة", needs_review: "تمت القراءة — تحتاج مراجعة", failed: "فشلت القراءة", unavailable: "القراءة غير متاحة" },
};
export const label = (group, key) => (LABELS[group] && LABELS[group][key]) || String(key);

const TONE = {
  good: new Set(["approved", "issued", "resolved", "closed", "مكتمل وفق قواعد التحقق", "مستوفٍ مبدئيًا", "completed"]),
  bad: new Set(["rejected", "urgent", "high", "غير مستوفٍ لقواعد التحقق", "يحتاج إلى مراجعة", "منتهي الصلاحية", "failed", "cancelled"]),
  warn: new Set(["needs_review", "needs_information", "medium", "مطلوب استكمال بيانات", "غير مرفق", "classified"]),
};
export function pill(text, key = text) {
  const tone = TONE.good.has(key) ? "good" : TONE.bad.has(key) ? "bad" : TONE.warn.has(key) ? "warn" : "";
  return h("span", { class: `pill ${tone}` }, text);
}

/* ---------------------------------------------------------------- session */
export function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || memoryToken; } catch { return memoryToken; }
}
export function setToken(token) {
  memoryToken = token;
  try { token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked: memory only */ }
}

/* -------------------------------------------------------------------- API */
export class AuthError extends Error {}
const STATUS_TEXT = {
  403: "ليست لديك صلاحية لهذا الإجراء.",
  404: "العنصر غير موجود.",
  409: "الإجراء غير مسموح في الحالة الحالية.",
  413: "الملف أكبر من الحد المسموح.",
  429: "محاولات كثيرة. انتظر قليلًا ثم حاول مرة أخرى.",
  503: "الخدمة غير متاحة حاليًا.",
};
export async function api(path, { method = "GET", json, form, headers = {} } = {}) {
  const options = { method, headers: { Accept: "application/json", Authorization: `Bearer ${getToken()}`, ...headers } };
  if (json !== undefined) { options.body = JSON.stringify(json); options.headers["Content-Type"] = "application/json"; }
  if (form) options.body = form; // the browser sets the multipart boundary itself
  let response;
  try { response = await fetch(path, options); } catch { throw new Error("تعذر الاتصال بالخادم. تحقق من أنه يعمل."); }
  if (response.status === 401) throw new AuthError("انتهت الجلسة، سجّل الدخول مرة أخرى.");
  if (response.status === 204) return null;
  let body = null;
  try { body = await response.json(); } catch { /* not JSON */ }
  if (!response.ok) {
    const detail = body && body.detail;
    if (typeof detail === "string" && /[؀-ۿ]/.test(detail)) throw new Error(detail);
    if (Array.isArray(detail)) throw new Error("تحقق من القيم المدخلة؛ بعضها غير صالح.");
    throw new Error(STATUS_TEXT[response.status] || "تعذر تنفيذ الطلب.");
  }
  return body;
}
export const qs = (params) => {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== "" && v != null) out.set(k, v);
  const text = out.toString();
  return text ? `?${text}` : "";
};

/* ----------------------------------------------------------- feedback UI */
export function toast(message, kind = "ok") {
  const region = $("toasts");
  const node = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, message);
  region.append(node);
  setTimeout(() => node.remove(), kind === "error" ? 8000 : 4000);
}

/** Confirmation dialog. With `notes`, a justification (3+ characters) is required. */
export function confirmDialog({ title, body, confirmText = "تأكيد", danger = false, notes = false, notesLabel = "ملاحظات القرار (مطلوبة)" }) {
  return new Promise((resolve) => {
    const textarea = notes ? h("textarea", { id: "dlg-notes", rows: "3", maxlength: "2000", required: true, "aria-label": notesLabel }) : null;
    const error = h("p", { class: "error", role: "alert", hidden: true });
    const dialog = h("dialog", { class: "dlg", "aria-labelledby": "dlg-title" });
    const finish = (result) => { dialog.close(); dialog.remove(); resolve(result); };
    const ok = h("button", { type: "button", class: danger ? "primary danger" : "primary", onclick: () => {
      if (textarea && textarea.value.trim().length < 3) { error.textContent = "اكتب ملاحظة من 3 أحرف على الأقل."; error.hidden = false; textarea.focus(); return; }
      finish({ ok: true, notes: textarea ? textarea.value.trim() : "" });
    } }, confirmText);
    dialog.append(
      h("h2", { id: "dlg-title" }, title),
      body ? h("p", {}, body) : null,
      textarea ? h("label", { for: "dlg-notes" }, notesLabel) : null, textarea, error,
      h("div", { class: "dlg-actions" }, ok, h("button", { type: "button", onclick: () => finish({ ok: false }) }, "إلغاء")));
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish({ ok: false }); });
    document.body.append(dialog);
    dialog.showModal();
    (textarea || ok).focus();
  });
}

/** Generic form dialog: fields = [{name,label,type,required,options,help}] -> resolves values or null. */
export function formDialog({ title, fields, submitText = "حفظ", intro }) {
  return new Promise((resolve) => {
    const inputs = fields.map((f) => {
      const id = `fd-${f.name}`;
      const control = f.type === "select"
        ? h("select", { id, name: f.name, required: f.required !== false }, f.options.map(([value, text]) => h("option", { value }, text)))
        : h("input", { id, name: f.name, type: f.type || "text", required: f.required !== false, autocomplete: "off", dir: f.dir || null, minlength: f.minlength || null });
      return { f, control, wrap: h("div", {}, h("label", { for: id }, f.label), control, f.help ? h("p", { class: "muted" }, f.help) : null) };
    });
    const error = h("p", { class: "error", role: "alert", hidden: true });
    const dialog = h("dialog", { class: "dlg", "aria-labelledby": "dlg-title" });
    const finish = (value) => { dialog.close(); dialog.remove(); resolve(value); };
    const form = h("form", { onsubmit: (e) => { e.preventDefault(); finish(Object.fromEntries(inputs.map(({ f, control }) => [f.name, control.value]))); } },
      h("h2", { id: "dlg-title" }, title), intro ? h("p", {}, intro) : null, inputs.map((i) => i.wrap), error,
      h("div", { class: "dlg-actions" }, h("button", { type: "submit", class: "primary" }, submitText), h("button", { type: "button", onclick: () => finish(null) }, "إلغاء")));
    dialog.append(form);
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); finish(null); });
    document.body.append(dialog);
    dialog.showModal();
    inputs[0].control.focus();
  });
}

/* --------------------------------------------------------------- widgets */
export const loading = (text = "جارٍ التحميل...") => h("p", { class: "muted loading", role: "status" }, text);
export const errorBox = (message) => h("p", { class: "error", role: "alert" }, message);
export const empty = (text) => h("p", { class: "empty" }, text);
export function help(text) { return h("p", { class: "help" }, h("span", { "aria-hidden": "true" }, "ℹ "), text); }

export function table(headers, rows, { caption } = {}) {
  if (rows.length === 0) return empty("لا توجد عناصر.");
  return h("div", { class: "table-wrap" }, h("table", {}, caption ? h("caption", { class: "sr" }, caption) : null,
    h("thead", {}, h("tr", {}, headers.map((t) => h("th", { scope: "col" }, t)))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c) => h("td", {}, c)))))));
}

export function pager({ page, pageSize, total }, onChange) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return h("div", { class: "pager" },
    h("button", { type: "button", disabled: page <= 1, onclick: () => onChange(page - 1) }, "السابق"),
    h("span", {}, `صفحة ${fmt(page)} من ${fmt(pages)} — ${fmt(total)} عنصر`),
    h("button", { type: "button", disabled: page >= pages, onclick: () => onChange(page + 1) }, "التالي"));
}

export function bars(title, subtitle, counts, labeler = (k) => k) {
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return h("section", { class: "card" }, h("h3", {}, title), subtitle ? h("p", { class: "sub" }, subtitle) : null,
    entries.length === 0 ? empty("لا توجد بيانات.") :
      h("div", { class: "bars", role: "list" }, entries.map(([key, value]) => {
        const fill = h("span", { class: "bar-fill" });
        fill.style.width = `${Math.round((value / max) * 100)}%`; // CSSOM is allowed by the CSP; style attributes are not
        return h("div", { class: "bar-row", role: "listitem" }, h("span", {}, labeler(key)), h("span", { class: "bar-track", "aria-hidden": "true" }, fill), h("span", { class: "bar-num" }, fmt(value)));
      })));
}

export function confidenceBar(value) {
  const fill = h("span", { class: "bar-fill" });
  fill.style.width = `${Math.round(value * 100)}%`;
  return h("span", { class: "conf" }, h("span", { class: "bar-track", "aria-hidden": "true" }, fill), h("span", { class: "bar-num" }, `${Math.round(value * 100)}%`));
}

/** A busy-safe button handler: disables the button while the async action runs. */
export function busy(button, action) {
  return async (event) => {
    event?.preventDefault?.();
    if (button.disabled) return;
    button.disabled = true;
    try { await action(); } finally { button.disabled = false; }
  };
}

export function field(labelText, control, hint) {
  const id = control.id || `f-${Math.random().toString(36).slice(2, 8)}`;
  control.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, labelText), control, hint ? h("p", { class: "muted" }, hint) : null);
}

export function copyButton(text, textLabel = "نسخ") {
  return h("button", { type: "button", class: "ghost", onclick: async (e) => {
    try { await navigator.clipboard.writeText(text); e.target.textContent = "تم النسخ"; } catch { toast("تعذر النسخ تلقائيًا؛ انسخ النص يدويًا.", "error"); }
  } }, textLabel);
}
