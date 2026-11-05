import { h, api, replace, when, label, pill, table, loading, errorBox, empty, help, toast, confirmDialog, busy, bytes, confidenceBar } from "./util.js";
import { explainPanel } from "./v_explain.js";
import { decisionBar, act, dl, num, back, canReview } from "./v_lists.js";

const APP_NEXT = {
  "قيد المراجعة": [["IN_REVIEW", "بدء المراجعة"], ["NEEDS_INFORMATION", "طلب استكمال بيانات"]],
  "تحت المراجعة": [["NEEDS_INFORMATION", "طلب استكمال بيانات"], ["APPROVED", "اعتماد"], ["REJECTED", "رفض"]],
  "مطلوب استكمال بيانات": [["IN_REVIEW", "استئناف المراجعة"]],
};
const DOC_DECISIONS = [["VALID", "مستوفٍ"], ["INVALID", "غير مطابق"], ["EXPIRED", "منتهي الصلاحية"], ["UNDER_REVIEW", "قيد الفحص"]];
const UPLOAD_OK = new Set(["غير مرفق", "تم الإرفاق", "قيد الفحص", "يحتاج إلى مراجعة"]);

function fieldsView(data) {
  return h("div", {},
    table(["الحقل", "القيمة", "الثقة", "تحتاج مراجعة؟", "السبب"], data.fields.map((f) => [
      label("field", f.field_name), h("span", { dir: "auto" }, f.value), confidenceBar(f.confidence),
      f.needs_review ? pill("نعم", "يحتاج إلى مراجعة") : "لا", f.review_reason || "—"])),
    data.missing_fields.length ? h("p", { class: "notice" }, `حقول لم تُستخرج: ${data.missing_fields.map((x) => label("field", x)).join("، ")}`) : null,
    data.checks.length ? h("h4", {}, "فحوصات المطابقة") : null,
    data.checks.length ? table(["الفحص", "النتيجة", "التفاصيل"], data.checks.map((c) => [c.title_ar, c.passed === null ? pill("غير محسوم", "غير مرفق") : c.passed ? pill("ناجح", "approved") : pill("لم ينجح", "rejected"), c.details_ar])) : null,
    h("p", { class: "muted" }, data.ai_note));
}

const openDocs = new Set(); // keeps a document panel open across re-renders (after upload/OCR/decision)

function documentCard(doc, session, reload) {
  const filesBox = h("div", {});
  const fieldsBox = h("div", {});
  const showFields = async (fileId) => {
    replace(fieldsBox, loading());
    try { replace(fieldsBox, fieldsView(await api(`/api/v1/kyc/documents/${doc.id}/files/${fileId}/fields`))); }
    catch (e) { if (e.name === "AuthError") throw e; replace(fieldsBox, errorBox(e.message)); }
  };
  const loadFiles = async () => {
    replace(filesBox, loading());
    try {
      const files = await api(`/api/v1/kyc/documents/${doc.id}/files`);
      replace(filesBox, files.length === 0 ? empty("لم يُرفع ملف بعد.") : table(["الملف", "الحجم", "القراءة", "رُفع بواسطة", ""], files.map((f) => {
        const read = h("button", { type: "button" }, f.ocr_status === "completed" ? "إعادة القراءة" : "قراءة المستند (OCR)");
        read.addEventListener("click", busy(read, async () => {
          try { const r = await api(`/api/v1/kyc/documents/${doc.id}/files/${f.id}/extract`, { method: "POST" }); toast(r.message); replace(fieldsBox, fieldsView(r)); await loadFiles2(); }
          catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); }
        }));
        const view = f.ocr_status === "completed" ? h("button", { type: "button", onclick: () => showFields(f.id) }, "عرض الحقول") : null;
        return [h("span", { dir: "auto" }, f.original_name), h("span", { dir: "ltr" }, bytes(f.size_bytes)), pill(label("ocr", f.ocr_status), f.ocr_status), f.uploaded_by, h("div", { class: "actions" }, view, canReview(session) ? read : null)];
      })));
    } catch (e) { if (e.name === "AuthError") throw e; replace(filesBox, errorBox(e.message)); }
  };
  const loadFiles2 = loadFiles;
  const fileInput = h("input", { type: "file", accept: ".png,.jpg,.jpeg,.pdf,image/png,image/jpeg,application/pdf", "aria-label": `ملف ${doc.document_type}` });
  const upload = h("button", { type: "button" }, "رفع الملف");
  upload.addEventListener("click", busy(upload, async () => {
    if (!fileInput.files.length) { toast("اختر ملفًا أولًا (PNG أو JPEG أو PDF).", "error"); return; }
    const form = new FormData(); form.append("file", fileInput.files[0]);
    try { const r = await api(`/api/v1/kyc/documents/${doc.id}/files`, { method: "POST", form }); toast(r.message); fileInput.value = ""; await reload(); }
    catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); }
  }));
  const uploader = canReview(session) && UPLOAD_OK.has(doc.status)
    ? h("div", { class: "upload" }, fileInput, upload, h("p", { class: "muted" }, "الملفات المسموحة: PNG أو JPEG أو PDF."))
    : null;
  const review = canReview(session) && doc.status !== "غير مرفق"
    ? decisionBar({ session, allowed: DOC_DECISIONS, danger: ["INVALID", "EXPIRED"], onDecide: (key, notes) => act(api(`/api/v1/kyc/documents/${doc.id}/review`, { method: "POST", json: { decision: key, notes } }), "تم تسجيل قرار المستند.", reload) })
    : null;
  const details = h("details", { open: openDocs.has(doc.id) }, h("summary", {}, "الملفات والقراءة الآلية والقرار"), uploader, filesBox, fieldsBox, review ? h("h4", {}, "قرار المراجع على المستند") : null, review);
  details.addEventListener("toggle", () => {
    if (details.open) { openDocs.add(doc.id); if (!filesBox.hasChildNodes()) loadFiles(); } else openDocs.delete(doc.id);
  });
  if (openDocs.has(doc.id)) loadFiles();
  return h("section", { class: "card doc" }, h("div", { class: "page-head" }, h("h3", {}, doc.document_type), pill(doc.status)),
    doc.reviewer_notes ? h("p", { class: "muted" }, `ملاحظة المراجع: ${doc.reviewer_notes}`) : null, details);
}

let shownApplication = null;
export async function kycDetail(root, session, id) {
  if (shownApplication !== id) { openDocs.clear(); shownApplication = id; }
  const render = async () => {
    replace(root, loading());
    const a = await api(`/api/v1/kyc/applications/${id}`);
    const nodes = [back("#/kyc", "كل طلبات اعرف عميلك"),
      h("div", { class: "page-head" }, h("h2", {}, "طلب ", num(a.application_number)), pill(a.status)),
      h("section", { class: "card" }, h("h3", {}, "بيانات الطلب"), dl([["العميل", a.customer_name], ["النوع", a.application_type], ["الفرع", a.branch_name], ["آخر 4 أرقام من الرقم القومي", a.national_id_last_four && h("span", { dir: "ltr" }, a.national_id_last_four)], ["ملاحظات", a.notes], ["آخر تحديث", when(a.updated_at)]])),
      h("section", { class: "card" }, h("h3", {}, "قرار الطلب"), help("قبل الاعتماد راجع كل مستند ونتائج التحقق أدناه."),
        decisionBar({ session, allowed: APP_NEXT[a.status] || [], danger: ["REJECTED"], onDecide: (key, notes) => act(api(`/api/v1/kyc/applications/${id}/review`, { method: "POST", json: { decision: key, notes } }), "تم تسجيل قرار الطلب.", render) })),
      h("h3", {}, "المستندات"), a.documents.map((d) => documentCard(d, session, render)),
      explainPanel("application", id),
      h("section", { class: "card" }, h("h3", {}, "نتائج التحقق"), table(["القاعدة", "النتيجة", "التفاصيل"], a.validation_results.map((v) => [v.title_ar, v.passed ? pill("ناجح", "approved") : pill(v.severity, "rejected"), v.details_ar]))),
      h("section", { class: "card" }, h("h3", {}, "سجل التدقيق"), table(["الوقت", "الإجراء", "بواسطة", "التفاصيل"], a.audit_events.map((e) => [when(e.created_at), label("action", e.action), `${e.actor_name || "النظام"} (${label("role", e.actor_type)})`, e.details_ar])))];
    replace(root, ...nodes);
  };
  await render();
}
