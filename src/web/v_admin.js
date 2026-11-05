import { h, api, qs, replace, when, label, pill, table, pager, loading, errorBox, help, toast, confirmDialog, formDialog, busy, field, fmt, bars } from "./util.js";

const ROLES = [["viewer", "مشاهد"], ["reviewer", "مراجع"], ["admin", "مدير"]];

export async function usersView(root, session) {
  const render = async () => {
    replace(root, loading());
    const users = await api("/api/v1/admin/users");
    const run = async (promise, ok) => { try { await promise; toast(ok); await render(); } catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); } };
    const rows = users.map((u) => {
      const me = u.username === session.username;
      const role = h("select", { "aria-label": `دور ${u.username}`, disabled: me }, ROLES.map(([v, t]) => h("option", { value: v, selected: v === u.role }, t)));
      role.addEventListener("change", async () => {
        const r = await confirmDialog({ title: "تغيير الدور", body: `تغيير دور ${u.username} إلى ${label("role", role.value)}؟`, confirmText: "تغيير" });
        if (r.ok) await run(api(`/api/v1/admin/users/${u.id}`, { method: "PATCH", json: { role: role.value } }), "تم تغيير الدور."); else await render();
      });
      const toggle = h("button", { type: "button", disabled: me, onclick: async () => {
        const r = await confirmDialog({ title: u.is_active ? "إيقاف المستخدم" : "تفعيل المستخدم", body: u.username, danger: u.is_active, confirmText: u.is_active ? "إيقاف" : "تفعيل" });
        if (r.ok) await run(api(`/api/v1/admin/users/${u.id}`, { method: "PATCH", json: { is_active: !u.is_active } }), "تم التحديث.");
      } }, u.is_active ? "إيقاف" : "تفعيل");
      const reset = h("button", { type: "button", onclick: async () => {
        const v = await formDialog({ title: `إعادة تعيين كلمة مرور ${u.username}`, submitText: "تعيين", fields: [{ name: "pw", label: "كلمة المرور الجديدة", type: "password", dir: "ltr", minlength: 12, help: "12 حرفًا على الأقل، وتجمع 3 أنواع (حروف صغيرة وكبيرة وأرقام ورموز)." }] });
        if (v) await run(api(`/api/v1/admin/users/${u.id}/reset-password`, { method: "POST", json: { new_password: v.pw } }), "تم تغيير كلمة المرور.");
      } }, "كلمة مرور جديدة");
      return [h("span", { dir: "ltr" }, u.username, me ? " (أنت)" : ""), role, u.is_active ? pill("فعّال", "approved") : pill("موقوف", "rejected"), when(u.created_at), h("div", { class: "actions" }, toggle, reset)];
    });
    const add = h("button", { type: "button", class: "primary", onclick: async () => {
      const v = await formDialog({ title: "مستخدم جديد", submitText: "إنشاء", fields: [
        { name: "username", label: "اسم المستخدم", dir: "ltr", minlength: 3, help: "حروف إنجليزية وأرقام و _ . - فقط." },
        { name: "password", label: "كلمة المرور", type: "password", dir: "ltr", minlength: 12 },
        { name: "role", label: "الدور", type: "select", options: ROLES }] });
      if (v) await run(api("/api/v1/admin/users", { method: "POST", json: v }), "تم إنشاء المستخدم.");
    } }, "مستخدم جديد");
    replace(root, h("div", { class: "page-head" }, h("h2", {}, "المستخدمون"), add),
      help("المشاهد يقرأ فقط (مع إخفاء الرقم القومي والموبايل). المراجع يتخذ القرارات. المدير يدير المستخدمين ويرى سجل التدقيق. لا يمكنك تعديل حسابك أو إيقافه."),
      table(["المستخدم", "الدور", "الحالة", "أُنشئ", "إجراءات"], rows, { caption: "المستخدمون" }));
  };
  await render();
}

export async function auditView(root) {
  const state = { source: "all", page: 1 };
  const body = h("div", {});
  const load = async () => {
    replace(body, loading());
    try {
      const d = await api(`/api/v1/admin/audit-events${qs({ ...state, page_size: 25 })}`);
      replace(body, table(["الوقت", "المصدر", "الكيان", "الإجراء", "بواسطة", "التفاصيل"], d.items.map((x) => [when(x.at), x.source === "kyc" ? "اعرف عميلك" : "العمليات", `${label("entity", x.entity)} #${x.entity_id}`, label("action", x.action), x.actor || "النظام", x.details])), pager(d, (p) => { state.page = p; load(); }));
    } catch (e) { if (e.name === "AuthError") throw e; replace(body, errorBox(e.message)); }
  };
  const sel = h("select", { onchange: (e) => { state.source = e.target.value; state.page = 1; load(); } }, [["all", "الكل"], ["banking", "العمليات"], ["kyc", "اعرف عميلك"]].map(([v, t]) => h("option", { value: v }, t)));
  replace(root, h("h2", {}, "سجل التدقيق"), help("كل قرار بشري وإجراء إداري مسجّل هنا. لا يظهر فيه رقم قومي أو كلمة مرور."), field("المصدر", sel), body);
  await load();
}

export async function systemView(root) {
  replace(root, loading());
  const s = await api("/api/v1/admin/system");
  const yes = (v) => (v ? pill("متاح", "approved") : pill("غير متاح", "rejected"));
  const reload = h("button", { type: "button" }, "إعادة تحميل ملفات السياسات");
  reload.addEventListener("click", busy(reload, async () => {
    try { const r = await api("/api/v1/policies/reload", { method: "POST" }); toast(`أُضيف ${fmt(r.added.length)}، دون تغيير ${fmt(r.unchanged.length)}، تعارض ${fmt(r.conflicts.length)}.`, r.conflicts.length ? "error" : "ok"); }
    catch (e) { if (e.name === "AuthError") throw e; toast(e.message, "error"); }
  }));
  replace(root, h("h2", {}, "حالة النظام"),
    h("section", { class: "card" }, h("dl", { class: "dl" }, [["الإصدار", s.version], ["البيئة", s.environment], ["قاعدة البيانات", s.database], ["السياسات الفعّالة", fmt(s.active_policies)], ["نموذج اللغة", s.llm_provider === "none" ? "غير مفعّل (نص السياسة مباشرة)" : `${s.llm_provider} / ${s.llm_model || "-"}`]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
      h("dl", { class: "dl" }, [["قارئ المستندات (Tesseract)", yes(s.ocr_engine_installed)], ["قراءة PDF", yes(s.pdf_rasterizer_installed)]].flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))),
    bars("المستخدمون حسب الدور", null, s.users_by_role, (r) => label("role", r)),
    h("section", { class: "card" }, h("h3", {}, "السياسات"), h("p", { class: "sub" }, "بعد إضافة ملف سياسة جديد في المجلد اضغط لتحميله."), reload));
}
