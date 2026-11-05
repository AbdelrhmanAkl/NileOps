import { h, api, replace, loading, errorBox, empty } from "./util.js";

/** "اشرح الحالة" panel: advisory explanation of an application or a complaint. */
export function explainPanel(kind, id) {
  const body = h("div", { class: "explain-body" });
  const run = async () => {
    replace(body, loading("جارٍ إعداد الشرح..."));
    try {
      const path = kind === "application" ? `/api/v1/explanations/applications/${id}` : `/api/v1/explanations/complaints/${id}`;
      const r = await api(path);
      replace(body,
        h("p", { class: "answer" }, r.summary),
        r.points.length ? h("h4", {}, "ما الذي يعنيه هذا؟") : null,
        r.points.length ? h("ul", {}, r.points.map((p) => h("li", {}, p))) : null,
        r.next_steps.length ? h("h4", {}, "الخطوات التالية المقترحة") : null,
        r.next_steps.length ? h("ol", {}, r.next_steps.map((p) => h("li", {}, p))) : null,
        r.citations.length ? h("h4", {}, "المصادر من السياسات") : null,
        r.citations.map((c) => h("blockquote", {}, h("strong", {}, `${c.title} (${c.policy_code} v${c.version})`), h("br"), c.excerpt)),
        r.note ? h("p", { class: "muted" }, r.note) : null,
        h("p", { class: "muted" }, r.advisory));
    } catch (error) {
      replace(body, errorBox(error.message));
      if (error.name === "AuthError") throw error;
    }
  };
  const wrap = h("section", { class: "card" }, h("h3", {}, "شرح الحالة (استرشادي)"),
    h("p", { class: "sub" }, "شرح بسيط لوضع هذا العنصر والخطوة التالية، من بيانات النظام وسياسات البنك فقط."),
    h("button", { type: "button", onclick: run }, "اشرح لي الحالة"), body);
  return wrap;
}
