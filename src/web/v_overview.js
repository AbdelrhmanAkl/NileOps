import { h, api, replace, fmt, when, label, pill, bars, table, empty, loading, errorBox, help } from "./util.js";

const GREETING = {
  viewer: "أنت في وضع المشاهدة: تستطيع متابعة الأرقام والطلبات وقراءة الشروح، دون تعديل أي شيء.",
  reviewer: "هذه الأعمال المفتوحة التي تنتظر مراجعتك. افتح أي عنصر لاتخاذ القرار.",
  admin: "نظرة كاملة على التشغيل، ومن القائمة تدير المستخدمين وتراجع سجل التدقيق.",
};

const kpi = (value, text, attention) =>
  h("div", { class: attention && value > 0 ? "kpi attention" : "kpi" }, h("div", { class: "value" }, fmt(value)), h("div", { class: "label" }, text));
const link = (href, text) => h("a", { href }, text);

export async function overview(root, session) {
  replace(root, loading());
  const s = await api("/api/v1/dashboard/summary");
  const k = s.kpis;
  const q = s.queues;
  const queueCard = (title, rows, href, cols) => h("section", { class: "card" }, h("h3", {}, title),
    table(["الرقم", ...cols.map((c) => c[0]), ""], rows.map((r) => [h("span", { dir: "ltr" }, r.number), ...cols.map((c) => c[1](r)), link(`#/${href}/${r.id}`, session.role === "viewer" ? "عرض" : "فتح")])));
  replace(root,
    h("h2", {}, "نظرة عامة"), help(GREETING[session.role] || ""),
    h("div", { class: "kpis" },
      kpi(k.open_reviews, "عناصر مفتوحة تنتظر قرارًا"), kpi(k.urgent_open_complaints, "شكاوى عاجلة مفتوحة", true),
      kpi(k.ocr_fields_need_review, "حقول مستخرجة تحتاج مراجعة", true), kpi(k.active_policies, "سياسات فعّالة")),
    h("div", { class: "grid2" },
      queueCard("شكاوى مفتوحة (الأعلى أولوية أولًا)", q.complaints, "complaints", [["الأولوية", (r) => pill(label("priority", r.priority), r.priority)], ["الحالة", (r) => label("complaint", r.status)]]),
      queueCard("طلبات اعرف عميلك المفتوحة", q.applications, "kyc", [["الحالة", (r) => r.status]]),
      queueCard("طلبات البطاقات المفتوحة", q.cards, "cards", [["الحالة", (r) => label("card", r.status)]]),
      queueCard("طلبات الحسابات المفتوحة", q.accounts, "accounts", [["الحالة", (r) => label("account", r.status)]])),
    h("div", { class: "grid2" },
      bars("الشكاوى حسب الحالة", `${fmt(s.complaints.total)} شكوى`, s.complaints.by_status, (x) => label("complaint", x)),
      bars("الشكاوى حسب الأولوية", null, s.complaints.by_priority, (x) => label("priority", x)),
      bars("طلبات البطاقات", `${fmt(s.card_requests.total)} طلب`, s.card_requests.by_status, (x) => label("card", x)),
      bars("طلبات فتح الحساب", `${fmt(s.account_requests.total)} طلب`, s.account_requests.by_status, (x) => label("account", x)),
      bars("طلبات اعرف عميلك", `${fmt(s.applications.total)} طلب`, s.applications.by_status),
      bars("حالة المستندات", null, s.documents.by_status)),
    h("section", { class: "card" }, h("h3", {}, "آخر الأنشطة"), h("p", { class: "sub" }, "بدون أسماء أشخاص أو ملاحظات."),
      table(["الوقت", "النوع", "الإجراء", "بواسطة"], s.recent_activity.map((a) => [when(a.at), label("entity", a.entity), label("action", a.action), label("role", a.actor_role)]))),
    h("p", { class: "muted" }, `آخر تحديث: ${when(s.generated_at)}`));
}
