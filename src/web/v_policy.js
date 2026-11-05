import { h, api, replace, field, busy, toast, errorBox, loading, table, empty, help } from "./util.js";

const EXAMPLES = ["كم مدة تسليم البطاقة الجديدة بعد الاعتماد؟", "ما الرسوم السنوية للبطاقة؟", "ما المستندات المطلوبة لفتح حساب؟", "كم يوم للرد على الشكوى؟"];

export async function policyView(root) {
  const result = h("div", { "aria-live": "polite" });
  const box = h("textarea", { rows: "3", maxlength: "500", minlength: "5", required: true, placeholder: "اكتب سؤالك عن سياسة البنك" });
  const btn = h("button", { type: "submit", class: "primary" }, "اسأل");
  const ask = async (question) => {
    replace(result, loading("جارٍ البحث في السياسات..."));
    try {
      const r = await api("/api/v1/policies/ask", { method: "POST", json: { question } });
      replace(result, h("section", { class: r.status === "answered" ? "card" : "card abstain" },
        r.status === "answered" ? h("p", { class: "answer" }, r.answer) : h("p", { class: "answer" }, r.answer),
        r.status === "abstained" ? help("لم أجد سندًا في السياسات المعتمدة، فلن أخمّن. اسأل المشرف أو أعد صياغة السؤال.") : null,
        r.citations.length ? h("h4", {}, "المصادر") : null,
        r.citations.map((c) => h("blockquote", {}, h("strong", {}, `[${c.number}] ${c.title} — ${c.heading} (${c.policy_code} v${c.version})`), h("br"), c.excerpt)),
        r.note ? h("p", { class: "muted" }, r.note) : null, h("p", { class: "muted" }, r.advisory)));
    } catch (e) { if (e.name === "AuthError") throw e; replace(result, errorBox(e.message)); }
  };
  const form = h("form", { class: "card", onsubmit: busy(btn, async (e) => { await ask(box.value.trim()); }) },
    field("اسأل عن سياسة البنك", box, "لا تكتب بيانات عميل حقيقية. الإجابة من السياسات المعتمدة فقط، وتمتنع لو لم تجد سندًا."),
    h("div", { class: "chips" }, EXAMPLES.map((x) => h("button", { type: "button", class: "chip", onclick: () => { box.value = x; ask(x); } }, x))), btn);
  const list = h("section", { class: "card" }, h("h3", {}, "السياسات المعتمدة"), loading());
  replace(root, h("h2", {}, "مساعد السياسات"), form, result, list);
  try {
    const items = await api("/api/v1/policies");
    replace(list, h("h3", {}, "السياسات المعتمدة"), table(["الكود", "العنوان", "الإصدار", "القسم"], items.map((p) => [h("span", { dir: "ltr" }, p.policy_code), p.title, `v${p.version}`, p.department || "—"])));
  } catch (e) { if (e.name === "AuthError") throw e; replace(list, errorBox(e.message)); }
}
