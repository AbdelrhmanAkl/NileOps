import { h, api, qs, replace, fmt, when, label, pill, table, pager, empty, loading, errorBox, help, field, toast, confirmDialog, formDialog, busy } from "./util.js";
import { explainPanel } from "./v_explain.js";

const canReview = (session) => session.role === "reviewer" || session.role === "admin";
const num = (n) => h("span", { dir: "ltr" }, n);
const back = (href, text) => h("a", { class: "back", href }, `→ ${text}`);

/* ----------------------------------------------------------- generic list */
function listPage({ title, intro, path, filters = [], columns, rowLink, toolbar }) {
  return async (root, session, params) => {
    const state = { q: params.get("q") || "", page: Number(params.get("page") || 1), ...Object.fromEntries(filters.map((f) => [f.name, params.get(f.name) || ""])) };
    const body = h("div", {});
    const load = async () => {
      replace(body, loading());
      try {
        const data = await api(`${path}${qs({ ...state, page_size: 15 })}`);
        replace(body,
          table(columns.map((c) => c[0]), data.items.map((row) => [...columns.map((c) => c[1](row, session)), h("a", { href: rowLink(row) }, canReview(session) ? "فتح / مراجعة" : "عرض")].filter((x) => x !== undefined)), { caption: title }),
          data.items.length === 0 ? help("جرّب تغيير البحث أو التصفية.") : null,
          pager(data, (p) => { state.page = p; load(); }));
      } catch (error) { if (error.name === "AuthError") throw error; replace(body, errorBox(error.message)); }
    };
    const form = h("form", { class: "filters", onsubmit: (e) => { e.preventDefault(); state.page = 1; load(); } },
      field("بحث", h("input", { type: "search", value: state.q, placeholder: "رقم الطلب أو الاسم", maxlength: "150", oninput: (e) => { state.q = e.target.value; } })),
      filters.map((f) => field(f.label, h("select", { onchange: (e) => { state[f.name] = e.target.value; } }, [["", "الكل"], ...f.options].map(([v, t]) => h("option", { value: v, selected: v === state[f.name] }, t))))),
      h("button", { type: "submit", class: "primary" }, "بحث"));
    replace(root, h("div", { class: "page-head" }, h("h2", {}, title), toolbar ? toolbar(session, load) : null), intro ? help(intro) : null, form, body);
    await load();
  };
}

const COMPLAINT_STATUS = Object.entries({ classified: "", assigned: "", in_progress: "", resolved: "", closed: "" }).map(([k]) => [k, label("complaint", k)]);
const PRIORITIES = ["urgent", "high", "medium", "low"].map((k) => [k, label("priority", k)]);
const CATEGORIES = ["بطاقات", "حسابات", "تحويلات", "خدمة العملاء", "احتيال وأمان"];
const DEPARTMENTS = ["إدارة البطاقات", "خدمة الحسابات", "عمليات التحويلات", "خدمة العملاء", "إدارة مكافحة الاحتيال"];

export const kycList = listPage({
  title: "طلبات اعرف عميلك", intro: "اختر طلبًا لرؤية مستنداته، ورفع الملفات وقراءتها، ثم اتخاذ القرار.", path: "/api/v1/kyc/applications",
  filters: [{ name: "status", label: "الحالة", options: ["قيد المراجعة", "تحت المراجعة", "مطلوب استكمال بيانات", "مكتمل وفق قواعد التحقق", "غير مستوفٍ لقواعد التحقق"].map((s) => [s, s]) }],
  columns: [["الرقم", (r) => num(r.application_number)], ["العميل", (r) => r.customer_name], ["النوع", (r) => r.application_type], ["الحالة", (r) => pill(r.status)], ["آخر تحديث", (r) => when(r.updated_at)]],
  rowLink: (r) => `#/kyc/${r.id}`,
});
export const cardsList = listPage({
  title: "طلبات إصدار البطاقات", intro: "كل الطلبات محاكاة؛ لا تُصدر بطاقات حقيقية.", path: "/api/v1/banking/card-issuance",
  filters: [{ name: "status", label: "الحالة", options: Object.keys({ pending: 1, under_review: 1, approved: 1, rejected: 1, issued: 1, cancelled: 1 }).map((k) => [k, label("card", k)]) }],
  columns: [["الرقم", (r) => num(r.request_number)], ["النوع", (r) => label("cardType", r.card_type)], ["الحالة", (r) => pill(label("card", r.status), r.status)], ["آخر تحديث", (r) => when(r.updated_at)]],
  rowLink: (r) => `#/cards/${r.id}`,
});
export const accountsList = listPage({
  title: "طلبات فتح الحساب", path: "/api/v1/banking/account-opening",
  filters: [{ name: "status", label: "الحالة", options: Object.keys({ pending: 1, under_review: 1, needs_information: 1, approved: 1, rejected: 1, cancelled: 1 }).map((k) => [k, label("account", k)]) }],
  columns: [["الرقم", (r) => num(r.request_number)], ["النوع", (r) => label("accountType", r.account_type)], ["الفرع", (r) => r.branch_name || "—"], ["الحالة", (r) => pill(label("account", r.status), r.status)], ["آخر تحديث", (r) => when(r.updated_at)]],
  rowLink: (r) => `#/accounts/${r.id}`,
});
export const complaintsList = listPage({
  title: "الشكاوى", intro: "الشكوى المصنّفة «تنتظر الفرز» تحتاج قرار موظف لتحديد القسم.", path: "/api/v1/banking/complaints",
  filters: [{ name: "status", label: "الحالة", options: COMPLAINT_STATUS }, { name: "priority", label: "الأولوية", options: PRIORITIES }, { name: "category", label: "التصنيف", options: CATEGORIES.map((c) => [c, c]) }],
  columns: [["الرقم", (r) => num(r.complaint_number)], ["التصنيف", (r) => r.category], ["الأولوية", (r) => pill(label("priority", r.priority), r.priority)], ["الحالة", (r) => pill(label("complaint", r.status), r.status)], ["القسم", (r) => r.assigned_department || "—"], ["تاريخ", (r) => when(r.created_at)]],
  rowLink: (r) => `#/complaints/${r.id}`,
  toolbar: (session, reload) => canReview(session) ? h("button", { type: "button", class: "primary", onclick: async () => {
    const v = await formDialog({ title: "شكوى جديدة (تجريبية)", intro: "لا تكتب بيانات عميل حقيقية. سيصنّفها النظام مبدئيًا وقد تحتاج فرزًا بشريًا.", submitText: "إنشاء",
      fields: [{ name: "description", label: "وصف الشكوى (10 أحرف على الأقل)", minlength: 10 }, { name: "category", label: "التصنيف", type: "select", required: false, options: [["", "تلقائي"], ...CATEGORIES.map((c) => [c, c])] }] });
    if (!v) return;
    try {
      const r = await api("/api/v1/banking/complaints", { method: "POST", json: { description: v.description, ...(v.category ? { category: v.category } : {}) } });
      toast(`تم إنشاء الشكوى ${r.complaint.complaint_number}.`); location.hash = `#/complaints/${r.complaint.id}`;
    } catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); }
  } }, "شكوى جديدة") : null,
});

/* ------------------------------------------------------ decision helper */
function decisionBar({ session, allowed, labels, onDecide, danger = [] }) {
  if (!canReview(session)) return h("p", { class: "muted" }, "وضع المشاهدة: لا تملك صلاحية اتخاذ قرار.");
  if (!allowed.length) return h("p", { class: "muted" }, "لا توجد قرارات متاحة في الحالة الحالية.");
  return h("div", { class: "actions" }, allowed.map(([key, text]) => {
    const b = h("button", { type: "button", class: danger.includes(key) ? "danger" : "" }, text);
    b.addEventListener("click", busy(b, async () => {
      const r = await confirmDialog({ title: text, body: "سيُسجَّل القرار باسمك في سجل التدقيق.", confirmText: text, danger: danger.includes(key), notes: true });
      if (r.ok) await onDecide(key, r.notes);
    }));
    return b;
  }));
}
async function act(promise, okText, reload) {
  try { const r = await promise; toast(okText || r.message); await reload(); } catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); }
}
const dl = (pairs) => h("dl", { class: "dl" }, pairs.filter(Boolean).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));

/* ------------------------------------------------------------ complaints */
const COMPLAINT_NEXT = { assigned: [["in_progress", "بدء المعالجة"]], in_progress: [["resolved", "تم الحل"]], resolved: [["closed", "إغلاق"], ["in_progress", "إعادة فتح للمعالجة"]] };
export async function complaintDetail(root, session, id) {
  const render = async () => {
    replace(root, loading());
    const c = await api(`/api/v1/banking/complaints/${id}`);
    const assign = canReview(session) && ["classified", "assigned"].includes(c.status) ? h("button", { type: "button", onclick: async () => {
      const v = await formDialog({ title: c.status === "classified" ? "فرز الشكوى إلى قسم" : "إعادة توجيه الشكوى", submitText: "تأكيد",
        fields: [{ name: "department", label: "القسم", type: "select", options: DEPARTMENTS.map((d) => [d, d]) }, { name: "priority", label: "الأولوية", type: "select", required: false, options: [["", "كما هي"], ...PRIORITIES] }, { name: "notes", label: "سبب القرار (مطلوب)", minlength: 3 }] });
      if (v) await act(api(`/api/v1/banking/complaints/${id}/assign`, { method: "POST", json: { department: v.department, notes: v.notes, ...(v.priority ? { priority: v.priority } : {}) } }), "تم تحديث توجيه الشكوى.", render);
    } }, c.status === "classified" ? "فرز إلى قسم" : "إعادة توجيه") : null;
    replace(root, back("#/complaints", "كل الشكاوى"),
      h("div", { class: "page-head" }, h("h2", {}, "شكوى ", num(c.complaint_number)), pill(label("complaint", c.status), c.status)),
      c.needs_human_triage && c.status === "classified" ? h("p", { class: "notice" }, "هذه الشكوى لم يتأكد النظام من قسمها؛ تحتاج فرزًا بشريًا.") : null,
      h("div", { class: "grid2" },
        h("section", { class: "card" }, h("h3", {}, "البيانات"), dl([["التصنيف", c.category], ["الأولوية", pill(label("priority", c.priority), c.priority)], ["القسم", c.assigned_department], ["سبب التوجيه", c.routing_reason], ["العميل", c.customer_name], ["أُنشئت", when(c.created_at)]]), h("h4", {}, "نص الشكوى"), h("p", { class: "text-block" }, c.description)),
        h("section", { class: "card" }, h("h3", {}, "رد مقترح (مسودة)"), h("p", { class: "text-block" }, c.suggested_reply || "لا يوجد رد مقترح."), h("p", { class: "muted" }, "مسودة فقط؛ لا تُرسل للعميل دون مراجعة موظف."))),
      h("section", { class: "card" }, h("h3", {}, "الإجراءات"), h("div", { class: "actions" }, assign),
        decisionBar({ session, allowed: COMPLAINT_NEXT[c.status] || [], onDecide: (key, notes) => act(api(`/api/v1/banking/complaints/${id}/status`, { method: "POST", json: { status: key, notes } }), "تم تحديث حالة الشكوى.", render) })),
      explainPanel("complaint", id),
      h("section", { class: "card" }, h("h3", {}, "سجل الإجراءات"), table(["الوقت", "الإجراء", "بواسطة", "التفاصيل"], c.history.map((x) => [when(x.created_at), label("action", x.action), `${x.actor_username} (${label("role", x.actor_role)})`, x.details_ar]))));
  };
  await render();
}

/* ------------------------------------------------- cards and accounts */
const CARD_NEXT = { pending: [["under_review", "بدء المراجعة"], ["cancelled", "إلغاء"]], under_review: [["approved", "اعتماد"], ["rejected", "رفض"], ["cancelled", "إلغاء"]], approved: [["cancelled", "إلغاء"]] };
const ACCOUNT_NEXT = { pending: [["under_review", "بدء المراجعة"], ["needs_information", "طلب استكمال"]], under_review: [["needs_information", "طلب استكمال"], ["approved", "اعتماد"], ["rejected", "رفض"]], needs_information: [["under_review", "استئناف المراجعة"]] };

export async function cardDetail(root, session, id) {
  const render = async () => {
    replace(root, loading());
    const r = await api(`/api/v1/banking/card-issuance/${id}`);
    const issue = canReview(session) && r.status === "approved" ? h("button", { type: "button", class: "primary" }, "إصدار البطاقة (محاكاة)") : null;
    if (issue) issue.addEventListener("click", busy(issue, async () => {
      const ok = await confirmDialog({ title: "إصدار بطاقة محاكى", body: "عملية محاكاة فقط ولن تُصدر بطاقة حقيقية. تأكيد؟", confirmText: "إصدار" });
      if (ok.ok) await act(api(`/api/v1/banking/card-issuance/${id}/issue`, { method: "POST" }), "تم الإصدار المحاكى.", render);
    }));
    replace(root, back("#/cards", "كل طلبات البطاقات"), h("div", { class: "page-head" }, h("h2", {}, "طلب بطاقة ", num(r.request_number)), pill(label("card", r.status), r.status)),
      h("section", { class: "card" }, h("h3", {}, "البيانات"), dl([["العميل", r.customer_name], ["نوع البطاقة", label("cardType", r.card_type)], ["الحساب", r.account_ref], ["حالة الحساب", r.account_status], ["ملاحظات المراجعة", r.review_notes], ["آخر تحديث", when(r.updated_at)]]), h("p", { class: "muted" }, "محاكاة فقط.")),
      h("section", { class: "card" }, h("h3", {}, "الإجراءات"), issue, decisionBar({ session, allowed: CARD_NEXT[r.status] || [], danger: ["rejected", "cancelled"], onDecide: (key, notes) => act(api(`/api/v1/banking/card-issuance/${id}/review`, { method: "POST", json: { decision: key, notes } }), "تم تسجيل القرار.", render) })));
  };
  await render();
}
export async function accountDetail(root, session, id) {
  const render = async () => {
    replace(root, loading());
    const r = await api(`/api/v1/banking/account-opening/${id}`);
    replace(root, back("#/accounts", "كل طلبات الحسابات"), h("div", { class: "page-head" }, h("h2", {}, "طلب حساب ", num(r.request_number)), pill(label("account", r.status), r.status)),
      h("section", { class: "card" }, h("h3", {}, "البيانات"), dl([["العميل", r.customer_name], ["نوع الحساب", label("accountType", r.account_type)], ["الفرع", r.branch_name], ["ملاحظات المراجعة", r.review_notes], ["آخر تحديث", when(r.updated_at)]]), h("p", { class: "muted" }, "محاكاة فقط.")),
      h("section", { class: "card" }, h("h3", {}, "الإجراءات"), decisionBar({ session, allowed: ACCOUNT_NEXT[r.status] || [], danger: ["rejected"], onDecide: (key, notes) => act(api(`/api/v1/banking/account-opening/${id}/review`, { method: "POST", json: { decision: key, notes } }), "تم تسجيل القرار.", render) })));
  };
  await render();
}
export { decisionBar, act, dl, num, back, canReview };
