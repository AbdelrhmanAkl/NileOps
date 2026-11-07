/* Customer portal views: everything here is the customer's own data only. */
import { h, api, replace, when, pill, loading, errorBox, empty, toast, formDialog, confirmDialog, busy, help } from "./util.js";

const KIND = { account: "فتح حساب", card: "بطاقة", complaint: "شكوى", kyc: "توثيق بيانات" };
const KIND_TX = { deposit: "إيداع", salary: "مرتب", withdrawal: "سحب", purchase: "شراء", transfer_out: "تحويل صادر", transfer_in: "تحويل وارد", fee: "رسوم", interest: "عائد", loan_installment: "قسط قرض" };
const LOAN = { personal: "قرض شخصي", auto: "قرض سيارة", mortgage: "تمويل عقاري" };
const money = (n, cur) => `${Number(n).toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}`;
const tone = (s) => (["approved", "issued", "resolved", "closed", "active", "مكتمل وفق قواعد التحقق"].includes(s) ? "approved" : ["rejected", "cancelled", "غير مستوفٍ لقواعد التحقق"].includes(s) ? "rejected" : ["needs_information", "مطلوب استكمال بيانات", "classified"].includes(s) ? "needs_information" : "");

function requestRow(r) {
  const link = r.kind === "complaint" ? `#/my-complaint/${r.id}` : r.kind === "kyc" ? `#/my-kyc/${r.id}` : null;
  return h("div", { class: "req" },
    h("div", {}, h("span", { class: "k" }, `${KIND[r.kind]} — ${r.detail}`), " ", h("span", { dir: "ltr", class: "muted" }, r.number)),
    pill(r.status_title, tone(r.status)),
    h("div", { class: "m" }, r.status_message, " ", link ? h("a", { href: link }, "التفاصيل") : null, " ", h("span", {}, `· ${when(r.created_at)}`)));
}

async function run(promise, okText, reload) {
  try { await promise; toast(okText); await reload(); } catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); }
}

async function create(promise, okText, target) {
  try { const r = await promise; toast(okText); location.hash = target(r); } catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); }
}

export async function portalHome(root, session) {
  const render = async () => {
    replace(root, loading());
    const o = await api("/api/v1/portal/overview");
    const activeAccounts = o.accounts.filter((a) => a.status === "active");
    const tile = (ico, title, text, onclick) => h("button", { type: "button", class: "tile", onclick }, h("span", { class: "ico", "aria-hidden": "true" }, ico), h("b", {}, title), h("span", { class: "t" }, text));
    const all = [...o.account_requests, ...o.card_requests, ...o.complaints, ...o.kyc_applications].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    replace(root,
      h("section", { class: "card welcome" }, h("h2", {}, `أهلًا ${o.customer_name}`), h("p", {}, "من هنا تقدّم طلباتك وتتابعها وترفع مستنداتك. ", h("span", { dir: "ltr" }, o.customer_ref))),
      h("div", { class: "tiles" },
        tile("🪪", "توثيق بياناتي", "ارفع بطاقتك وإثبات عنوانك", () => create(api("/api/v1/portal/kyc-applications", { method: "POST" }), "تم إنشاء طلب التوثيق.", (k) => `#/my-kyc/${k.id}`)),
        tile("🏦", "فتح حساب", "حساب توفير أو جاري", async () => {
          const v = await formDialog({ title: "طلب فتح حساب (محاكاة)", submitText: "إرسال الطلب", fields: [
            { name: "account_type", label: "نوع الحساب", type: "select", options: [["savings", "حساب توفير"], ["current", "حساب جاري"]] },
            { name: "branch_name", label: "الفرع (اختياري)", required: false }] });
          if (v) await run(api("/api/v1/portal/account-requests", { method: "POST", json: { account_type: v.account_type, ...(v.branch_name ? { branch_name: v.branch_name } : {}) } }), "تم إرسال طلبك.", render);
        }),
        tile("💳", "طلب بطاقة", activeAccounts.length ? "لأحد حساباتك النشطة" : "تحتاج حسابًا نشطًا أولًا", async () => {
          if (!activeAccounts.length) { toast("لا يوجد لديك حساب نشط بعد. قدّم طلب فتح حساب أولًا وانتظر الموافقة.", "error"); return; }
          const v = await formDialog({ title: "طلب بطاقة (محاكاة)", submitText: "إرسال الطلب", fields: [
            { name: "account_id", label: "الحساب", type: "select", options: activeAccounts.map((a) => [a.id, `${a.account_ref} — ${a.account_type === "savings" ? "توفير" : "جاري"}`]) },
            { name: "card_type", label: "نوع البطاقة", type: "select", options: [["debit", "خصم مباشر"], ["prepaid", "مدفوعة مسبقًا"]] }] });
          if (v) await run(api("/api/v1/portal/card-requests", { method: "POST", json: { card_type: v.card_type, account_id: Number(v.account_id) } }), "تم إرسال طلبك.", render);
        }),
        tile("📣", "تقديم شكوى", "نوجّهها للقسم المختص", async () => {
          const v = await formDialog({ title: "تقديم شكوى", intro: "اشرح المشكلة بوضوح. لا تكتب كلمات مرور أو أرقام بطاقات.", submitText: "إرسال الشكوى",
            fields: [{ name: "description", label: "وصف الشكوى (10 أحرف على الأقل)", minlength: 10 }] });
          if (v) await create(api("/api/v1/portal/complaints", { method: "POST", json: { description: v.description } }), "تم استلام شكواك.", (c) => `#/my-complaint/${c.id}`);
        })),
      h("div", { class: "grid2" },
        h("section", { class: "card" }, h("h3", {}, "حساباتي"), o.accounts.length === 0 ? empty("ليس لديك حسابات بعد.") :
          o.accounts.map((a) => h("div", { class: "req" }, h("div", {}, h("span", { class: "k" }, a.account_type === "savings" ? "حساب توفير" : "حساب جاري"), " ", h("span", { dir: "ltr", class: "muted" }, a.account_number || a.account_ref)), pill(a.status === "active" ? "نشط" : a.status, tone(a.status)),
            h("div", { class: "m" }, h("b", { dir: "ltr" }, money(a.balance, a.currency)), " ", h("a", { href: `#/my-account/${a.id}` }, "كشف الحساب"))))),
        o.loans.length ? h("section", { class: "card" }, h("h3", {}, "التمويلات"), o.loans.map((l) => h("div", { class: "req" }, h("div", {}, h("span", { class: "k" }, LOAN[l.loan_type] || l.loan_type), " ", h("span", { dir: "ltr", class: "muted" }, l.loan_ref)), pill(l.status === "active" ? "منتظم" : l.status === "overdue" ? "متأخر" : "مُسدَّد", l.status === "overdue" ? "rejected" : "approved"),
          h("div", { class: "m" }, `القسط الشهري: `, h("b", { dir: "ltr" }, money(l.monthly_installment, "EGP")), ` · المدة ${l.tenure_months} شهر · أصل التمويل `, h("span", { dir: "ltr" }, money(l.principal_amount, "EGP")))))) : null,
        h("section", { class: "card" }, h("h3", {}, "طلباتي وشكاواي"), all.length === 0 ? empty("لم تقدّم أي طلب بعد. ابدأ من الأزرار بالأعلى.") : all.map(requestRow))),
      h("p", { class: "muted" }, "جميع العمليات محاكاة ولا تُنفَّذ أي عملية مالية حقيقية."));
  };
  await render();
}

export async function myComplaint(root, session, id) {
  replace(root, loading());
  const c = await api(`/api/v1/portal/complaints/${id}`);
  replace(root, h("a", { class: "back", href: "#/home" }, "→ الرئيسية"),
    h("div", { class: "page-head" }, h("h2", {}, "شكوى ", h("span", { dir: "ltr" }, c.number)), pill(c.status_title, tone(c.status))),
    h("div", { class: "grid2" },
      h("section", { class: "card" }, h("h3", {}, "حالة الشكوى"), h("p", {}, c.status_message),
        h("ol", { class: "steps" }, c.timeline.map((s) => h("li", { class: s.done ? "done" : "" }, s.title, s.at && s.done ? h("small", {}, when(s.at)) : null)))),
      h("section", { class: "card" }, h("h3", {}, "نص شكواك"), h("p", { class: "text-block" }, c.description))));
}

export async function myKyc(root, session, id) {
  const render = async () => {
    replace(root, loading());
    const k = await api(`/api/v1/portal/kyc-applications/${id}`);
    const done = k.documents.filter((d) => d.status !== "غير مرفق").length;
    replace(root, h("a", { class: "back", href: "#/home" }, "→ الرئيسية"),
      h("div", { class: "page-head" }, h("h2", {}, "توثيق بياناتي ", h("span", { dir: "ltr" }, k.number)), pill(k.status_title, tone(k.status))),
      help(`ارفع المستندات الثلاثة (صورة واضحة PNG أو JPEG أو ملف PDF). تم رفع ${done} من ${k.documents.length}. ${k.status_message}`),
      k.documents.map((d) => {
        const input = h("input", { type: "file", accept: ".png,.jpg,.jpeg,.pdf,image/png,image/jpeg,application/pdf", "aria-label": `ملف ${d.document_type}` });
        const btn = h("button", { type: "button", class: "primary" }, d.file_name ? "استبدال الملف" : "رفع الملف");
        btn.addEventListener("click", busy(btn, async () => {
          if (!input.files.length) { toast("اختر ملفًا أولًا.", "error"); return; }
          const form = new FormData(); form.append("file", input.files[0]);
          await run(api(`/api/v1/portal/kyc-applications/${id}/documents/${d.id}/files`, { method: "POST", form }), "تم رفع الملف.", render);
        }));
        return h("section", { class: "card" }, h("div", { class: "page-head" }, h("h3", {}, d.document_type), pill(d.status_text, d.status === "مستوفٍ مبدئيًا" ? "approved" : d.status === "غير مرفق" ? "" : "needs_information")),
          d.file_name ? h("p", { class: "muted" }, "آخر ملف: ", h("span", { dir: "auto" }, d.file_name)) : null,
          d.can_upload ? h("div", { class: "upload" }, input, btn) : h("p", { class: "muted" }, "لا يمكن رفع ملفات لهذا المستند الآن."));
      }));
  };
  await render();
}


export async function myAccount(root, session, id) {
  replace(root, loading());
  const [o, rows] = await Promise.all([api("/api/v1/portal/overview"), api(`/api/v1/portal/accounts/${id}/transactions?limit=100`)]);
  const a = o.accounts.find((x) => String(x.id) === String(id));
  replace(root, h("a", { class: "back", href: "#/home" }, "→ الرئيسية"),
    h("div", { class: "page-head" }, h("h2", {}, a && a.account_type === "savings" ? "حساب توفير " : "حساب جاري ", h("span", { dir: "ltr" }, a ? a.account_number || a.account_ref : "")),
      a ? h("b", { dir: "ltr" }, money(a.balance, a.currency)) : null),
    h("section", { class: "card" }, h("h3", {}, "آخر الحركات"), rows.length === 0 ? empty("لا توجد حركات.") :
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["التاريخ", "البيان", "النوع", "المبلغ", "الرصيد بعد الحركة"].map((t) => h("th", {}, t)))),
        h("tbody", {}, rows.map((t) => h("tr", {}, h("td", {}, when(t.posted_at)), h("td", {}, t.description), h("td", {}, KIND_TX[t.kind] || t.kind),
          h("td", { dir: "ltr" }, ["deposit", "salary", "transfer_in", "interest"].includes(t.kind) ? "+" : "−", money(t.amount, t.currency)), h("td", { dir: "ltr" }, money(t.balance_after, t.currency)))))))),
    h("p", { class: "muted" }, "بيانات اصطناعية للمحاكاة فقط."));
}
