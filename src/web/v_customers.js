/* Staff customer 360: one page with a customer's accounts, ledger, loans, requests and KYC state (read-only). */
import { h, api, replace, when, pill, table, empty, loading, label } from "./util.js";
import { listPage, dl, num, back } from "./v_lists.js";

const KYC = { approved: ["معتمد", "approved"], pending: ["قيد المراجعة", "needs_information"], none: ["لا يوجد طلب", "rejected"] };
const LOAN = { personal: "قرض شخصي", auto: "قرض سيارة", mortgage: "تمويل عقاري" };
const LOAN_STATUS = { active: "منتظم", overdue: "متأخر", closed: "مُسدَّد" };
const KIND_TX = { deposit: "إيداع", salary: "مرتب", withdrawal: "سحب", purchase: "شراء", transfer_out: "تحويل صادر", transfer_in: "تحويل وارد", fee: "رسوم", interest: "عائد", loan_installment: "قسط قرض" };
const CREDIT = new Set(["deposit", "salary", "transfer_in", "interest"]);
const REQ = { account: ["طلب حساب", "accounts", "account"], card: ["طلب بطاقة", "cards", "card"], complaint: ["شكوى", "complaints", "complaint"] };
const money = (n, cur) => `${Number(n).toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}`;
const kycPill = (s) => pill(KYC[s][0], KYC[s][1]);

export const customersList = listPage({
  title: "العملاء", intro: "ابحث بالاسم أو رقم العميل لفتح ملفه الكامل (حسابات، حركات، قروض، طلبات). بيانات اصطناعية.", path: "/api/v1/customers",
  columns: [["رقم العميل", (r) => num(r.customer_ref)], ["الاسم", (r) => r.full_name], ["الفرع", (r) => r.branch_name || "—"], ["المحافظة", (r) => r.governorate || "—"],
    ["حسابات نشطة", (r) => num(r.accounts_count)], ["التحقق KYC", (r) => kycPill(r.kyc_state)]],
  rowLink: (r) => `#/customers/${r.id}`,
});

export async function customerDetail(root, session, id) {
  replace(root, loading());
  const c = await api(`/api/v1/customers/${id}`);
  replace(root, back("#/customers", "كل العملاء"),
    h("div", { class: "page-head" }, h("h2", {}, c.full_name, " ", h("small", { dir: "ltr", class: "muted" }, c.customer_ref)), kycPill(c.kyc_state)),
    h("div", { class: "grid2" },
      h("section", { class: "card" }, h("h3", {}, "بيانات العميل"),
        dl([["الفرع", c.branch_name], ["المحافظة", c.governorate], ["تاريخ الميلاد", c.date_of_birth], ["إجمالي الأرصدة بالجنيه", h("b", { dir: "ltr" }, money(c.totals_egp, "EGP"))]]),
        c.kyc_state !== "approved" ? h("p", { class: "muted" }, "لا يمكن اعتماد فتح حساب جديد قبل اعتماد التحقق من الهوية (KYC).") : null),
      h("section", { class: "card" }, h("h3", {}, "الحسابات"), c.accounts.length === 0 ? empty("لا توجد حسابات.") :
        table(["رقم الحساب", "النوع", "الرصيد", "الحالة"], c.accounts.map((a) => [num(a.account_number || "—"), a.account_type === "savings" ? "توفير" : "جاري", h("b", { dir: "ltr" }, money(a.balance, a.currency)), pill(a.status === "active" ? "نشط" : a.status, a.status === "active" ? "approved" : "")]), { caption: "الحسابات" }))),
    c.loans.length ? h("section", { class: "card" }, h("h3", {}, "التمويلات"),
      table(["الرقم", "النوع", "أصل التمويل", "القسط الشهري", "المدة", "الحالة"], c.loans.map((l) => [num(l.loan_ref), LOAN[l.loan_type] || l.loan_type, h("span", { dir: "ltr" }, money(l.principal_amount, "EGP")), h("span", { dir: "ltr" }, money(l.monthly_installment, "EGP")), `${l.tenure_months} شهر`, pill(LOAN_STATUS[l.status] || l.status, l.status === "overdue" ? "rejected" : "approved")]), { caption: "التمويلات" })) : null,
    h("section", { class: "card" }, h("h3", {}, "آخر الحركات"), c.transactions.length === 0 ? empty("لا توجد حركات.") :
      table(["التاريخ", "الحساب", "البيان", "المبلغ", "الرصيد بعد الحركة"], c.transactions.map((t) => [when(t.posted_at), num(t.account_number || "—"), `${KIND_TX[t.kind] || t.kind} — ${t.description}`,
        h("span", { dir: "ltr" }, `${CREDIT.has(t.kind) ? "+" : "−"}${money(t.amount, t.currency)}`), h("span", { dir: "ltr" }, money(t.balance_after, t.currency))]), { caption: "آخر الحركات" })),
    h("section", { class: "card" }, h("h3", {}, "الطلبات والشكاوى"), c.requests.length === 0 ? empty("لا توجد طلبات.") :
      table(["النوع", "الرقم", "التفاصيل", "الحالة", "تاريخ", ""], c.requests.map((r) => [REQ[r.kind][0], num(r.number), (r.kind === "card" ? label("cardType", r.detail) : r.kind === "account" ? label("accountType", r.detail) : r.detail) || "—", pill(label(REQ[r.kind][2], r.status), r.status), when(r.created_at), h("a", { href: `#/${REQ[r.kind][1]}/${r.id}` }, "فتح")]), { caption: "الطلبات" })),
    h("p", { class: "muted" }, "عرض هذا الملف مسجَّل في سجل التدقيق. بيانات اصطناعية للمحاكاة فقط."));
}
