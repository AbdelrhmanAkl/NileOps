/* NileOps dashboard v2: role-aware single-page app (ES modules, no inline code, textContent only). */
import { h, $, replace, api, getToken, setToken, AuthError, label, errorBox, loading, toast } from "./util.js";
import { overview } from "./v_overview.js";
import { kycList, cardsList, accountsList, complaintsList, complaintDetail, cardDetail, accountDetail } from "./v_lists.js";
import { kycDetail } from "./v_kyc.js";
import { policyView } from "./v_policy.js";
import { usersView, auditView, systemView } from "./v_admin.js";
import { portalHome, myComplaint, myKyc, myAccount } from "./v_portal.js";

const REFRESH_MS = 60000;
let session = null;
let timer = null;

const NAV = [
  { key: "overview", text: "نظرة عامة", roles: ["viewer", "reviewer", "admin"] },
  { key: "kyc", text: "اعرف عميلك", roles: ["viewer", "reviewer", "admin"] },
  { key: "complaints", text: "الشكاوى", roles: ["viewer", "reviewer", "admin"] },
  { key: "cards", text: "البطاقات", roles: ["viewer", "reviewer", "admin"] },
  { key: "accounts", text: "الحسابات", roles: ["viewer", "reviewer", "admin"] },
  { key: "policies", text: "مساعد السياسات", roles: ["viewer", "reviewer", "admin"] },
  { key: "users", text: "المستخدمون", roles: ["admin"], group: "admin" },
  { key: "audit", text: "سجل التدقيق", roles: ["admin"], group: "admin" },
  { key: "system", text: "حالة النظام", roles: ["admin"], group: "admin" },
  { key: "home", text: "الرئيسية", roles: ["customer"] },
  { key: "my-complaint", text: "شكوى", roles: ["customer"], hidden: true },
  { key: "my-kyc", text: "توثيق البيانات", roles: ["customer"], hidden: true },
  { key: "my-account", text: "كشف حساب", roles: ["customer"], hidden: true },
];
const LIST = { kyc: kycList, complaints: complaintsList, cards: cardsList, accounts: accountsList };
const DETAIL = { kyc: kycDetail, complaints: complaintDetail, cards: cardDetail, accounts: accountDetail, "my-complaint": myComplaint, "my-kyc": myKyc, "my-account": myAccount };
const SIMPLE = { home: portalHome, overview, policies: policyView, users: usersView, audit: auditView, system: systemView };
const ROLE_NOTE = {
  viewer: "وضع المشاهدة: قراءة فقط، ويُخفى الرقم القومي والموبايل.",
  reviewer: "وضع المراجع: تتخذ قرارات المراجعة بنفسك، وكل قرار يُسجَّل باسمك.",
  admin: "وضع المدير: كل الصلاحيات، بالإضافة إلى المستخدمين والتدقيق.",
  customer: "بوابة العميل: ترى طلباتك أنت فقط.",
};

function parseHash() {
  const [path, query = ""] = location.hash.replace(/^#\/?/, "").split("?");
  const [route = "", id] = path.split("/");
  return { route: route || (session && session.role === "customer" ? "home" : "overview"), id: id && /^\d+$/.test(id) ? Number(id) : null, params: new URLSearchParams(query) };
}

function buildNav(current) {
  const items = NAV.filter((n) => n.roles.includes(session.role) && !n.hidden);
  replace($("nav"), items.map((n, i) => [
    n.group === "admin" && items[i - 1]?.group !== "admin" ? h("span", { class: "nav-sep", role: "separator" }, "الإدارة") : null,
    h("a", { href: `#/${n.key}`, class: n.key === current ? "active" : "", "aria-current": n.key === current ? "page" : null }, n.text),
  ]));
}

async function render() {
  if (!session) return;
  const { route, id, params } = parseHash();
  const view = $("view");
  const allowed = NAV.find((n) => n.key === route && n.roles.includes(session.role));
  if (!allowed) { location.hash = session.role === "customer" ? "#/home" : "#/overview"; return; }
  buildNav(DETAIL[route] && allowed.hidden ? "home" : route);
  document.title = `NileOps | ${allowed.text}`;
  try {
    if (id && DETAIL[route]) await DETAIL[route](view, session, id);
    else if (LIST[route]) await LIST[route](view, session, params);
    else await SIMPLE[route](view, session);
    view.focus({ preventScroll: true });
  } catch (error) {
    if (error instanceof AuthError) return logout(error.message);
    replace(view, errorBox(error.message || "حدث خطأ غير متوقع."), h("button", { type: "button", onclick: render }, "إعادة المحاولة"));
  }
}

function logout(message) {
  clearInterval(timer);
  setToken(null);
  session = null;
  $("app").hidden = true; $("session").hidden = true; $("login").hidden = false; $("nav").replaceChildren();
  showAuthTab("login");
  const box = $("login-error");
  box.textContent = message || ""; box.hidden = !message;
  $("username").focus();
}

async function start() {
  try {
    const me = await api("/api/v1/auth/me");
    session = { username: me.username, role: me.role };
  } catch (error) { return logout(error instanceof AuthError ? "" : error.message); }
  $("login").hidden = true; $("app").hidden = false; $("session").hidden = false;
  $("who").textContent = `${session.username} — ${label("role", session.role)}`;
  $("role-note").textContent = ROLE_NOTE[session.role] || "";
  if (!location.hash || location.hash === "#/") location.hash = session.role === "customer" ? "#/home" : "#/overview";
  await render();
  clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden && /^#\/(overview|home)?$/.test(location.hash)) render(); }, REFRESH_MS);
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter; button.disabled = true;
  const box = $("login-error"); box.hidden = true;
  try {
    const body = new URLSearchParams({ username: $("username").value.trim(), password: $("password").value });
    const response = await fetch("/api/v1/auth/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
    if (response.status === 429) throw new Error("محاولات كثيرة. انتظر بضع دقائق ثم حاول مرة أخرى.");
    if (!response.ok) throw new Error("اسم المستخدم أو كلمة المرور غير صحيحة.");
    setToken((await response.json()).access_token);
    $("password").value = "";
    await start();
  } catch (error) { box.textContent = error.message || "تعذر الاتصال بالخادم."; box.hidden = false; }
  finally { button.disabled = false; }
});
function showAuthTab(tab) {
  const login = tab === "login";
  $("login-form").hidden = !login; $("register-form").hidden = login;
  $("tab-login").setAttribute("aria-selected", String(login)); $("tab-register").setAttribute("aria-selected", String(!login));
  $("login-error").hidden = true;
}
$("tab-login").addEventListener("click", () => showAuthTab("login"));
$("tab-register").addEventListener("click", () => showAuthTab("register"));

async function signIn(username, password) {
  const body = new URLSearchParams({ username, password });
  const response = await fetch("/api/v1/auth/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
  if (response.status === 429) throw new Error("محاولات كثيرة. انتظر بضع دقائق ثم حاول مرة أخرى.");
  if (!response.ok) throw new Error("اسم المستخدم أو كلمة المرور غير صحيحة.");
  setToken((await response.json()).access_token);
  await start();
}

$("register-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter; const box = $("login-error"); box.hidden = true;
  if ($("reg-pass").value !== $("reg-pass2").value) { box.textContent = "كلمتا المرور غير متطابقتين."; box.hidden = false; return; }
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/portal/register", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ full_name: $("reg-name").value.trim(), username: $("reg-user").value.trim(), password: $("reg-pass").value }) });
    if (!response.ok) {
      let detail = null; try { detail = (await response.json()).detail; } catch { /* not JSON */ }
      throw new Error(typeof detail === "string" && /[\u0600-\u06FF]/.test(detail) ? detail : "تعذر إنشاء الحساب. تحقق من البيانات.");
    }
    const username = $("reg-user").value.trim(); const password = $("reg-pass").value;
    $("reg-pass").value = ""; $("reg-pass2").value = "";
    await signIn(username, password);
  } catch (error) { box.textContent = error.message || "تعذر الاتصال بالخادم."; box.hidden = false; }
  finally { button.disabled = false; }
});
$("logout").addEventListener("click", () => logout(""));
$("refresh").addEventListener("click", render);
window.addEventListener("hashchange", render);
if (getToken()) start(); else logout("");
