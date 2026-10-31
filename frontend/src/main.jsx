import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bell, Check, ChevronDown, ClipboardList, FileText, Home, Search,
  ShieldCheck, Settings, Upload, Camera, X, Clock3, Trash2, ExternalLink,
  Plus, RefreshCw, Pencil, Moon, Sun, Heart, FileCheck2, LogOut, UserRound, LogIn, Mail, Receipt, Wallet, Sparkles
} from "lucide-react";
import "./styles.css";

const API = (import.meta.env.VITE_API_URL || "https://billproof-starter.onrender.com").replace(/\/+$/, "");
const NAV_FILTERS = {
  "My Bills": ["All Documents", "Uploaded Bills", "Mail Bills", "Needs Review"],
  "Warranty Tracker": ["Active Warranties", "No Warranty"],
  "Reminders": ["Expiring Soon", "Expired"],
};
const NAV = [
  [Home, "Home"], [FileText, "My Bills"], [ShieldCheck, "Warranty Tracker"],
  [Bell, "Reminders"], [Settings, "Settings"]
];

// Sign-in is remembered with a token kept in this browser, because the
// login cookie alone is unreliable on phones: the website and the API are
// different sites, and many mobile browsers block cross-site cookies,
// which made signed-in users flip back to "Guest" after a refresh.
const TOKEN_KEY = "raseed_token";
function storedToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
function captureLoginToken() {
  // After Google sign-in the API returns the browser to this site with
  // the token in the URL fragment; store it and clean the address bar.
  if (!window.location.hash.includes("raseed_token=")) return;
  const match = window.location.hash.match(/raseed_token=([^&]+)/);
  if (match) {
    try { localStorage.setItem(TOKEN_KEY, decodeURIComponent(match[1])); } catch { /* private mode */ }
  }
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
}
// The last confirmed sign-in answer is also remembered, so a returning
// visitor sees their own greeting instantly on reload instead of a
// "checking" state flipping into place a second later. The fresh check
// still runs on every load and overwrites this if anything changed.
const AUTH_CACHE_KEY = "raseed_auth_cache";
function readAuthCache() {
  try {
    const raw = localStorage.getItem(AUTH_CACHE_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    return d && typeof d === "object" && "connected" in d ? d : null;
  } catch { return null; }
}
function writeAuthCache(d) {
  try {
    if (d) localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({
      connected: !!d.connected, auth_configured: !!d.auth_configured,
      email: d.email || null, profile: d.profile || null,
      profile_complete: !!d.profile_complete,
    }));
  } catch { /* ignore */ }
}
async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = storedToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return fetch(`${API}${path}`, { ...options, headers, credentials: "include" });
}
function formatDate(value) {
  if (!value) return "—";
  const d = new Date(`${value}T00:00:00`);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}
function money(value) {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(value);
}
function Status({ status }) {
  const cls = status === "Active" ? "active" : status === "Expiring Soon" ? "soon" : status === "Expired" ? "expired" : "none";
  return <span className={`status ${cls}`}>{status || "No Warranty"}</span>;
}

const GMAIL_LOGO = "/gmail-logo.png"; // bundled official Gmail icon — always loads, even on slow networks
// Frontend mirror of the backend company map — used only when a bill
// comes from an older backend without logo_url/company_about, so a
// logo still shows. The backend values always win when present.
const COMPANY_FALLBACK = {
  eatsure: ["eatsure.com", "Food delivery"], rapido: ["rapido.bike", "Bike taxi & rides"],
  croma: ["croma.com", "Electronics store"], samsung: ["samsung.com", "Electronics"],
  oneplus: ["oneplus.in", "Smartphones"], ajio: ["ajio.com", "Fashion shopping"],
  amazon: ["amazon.in", "Online shopping"], flipkart: ["flipkart.com", "Online shopping"],
  zomato: ["zomato.com", "Food delivery"], swiggy: ["swiggy.com", "Food delivery"],
  myntra: ["myntra.com", "Fashion shopping"], bigbasket: ["bigbasket.com", "Groceries"],
  blinkit: ["blinkit.com", "Quick groceries"], ola: ["olacabs.com", "Ride-hailing"],
  uber: ["uber.com", "Ride-hailing"], apple: ["apple.com", "Electronics"],
  xiaomi: ["mi.com", "Electronics"], mi: ["mi.com", "Electronics"],
  boat: ["boat-lifestyle.com", "Audio accessories"], reliance: ["reliancedigital.in", "Electronics store"],
  dmart: ["dmart.in", "Groceries & daily needs"], decathlon: ["decathlon.in", "Sports store"],
  makemytrip: ["makemytrip.com", "Travel booking"], irctc: ["irctc.co.in", "Train tickets"],
  nykaa: ["nykaa.com", "Beauty shopping"], zepto: ["zepto.com", "Quick groceries"],
  ikea: ["ikea.com", "Furniture"], "vijay sales": ["vijaysales.com", "Electronics store"],
};
// Company logos bundled with the site itself (frontend/public/logos),
// so the big companies' logos always load instantly; any other known
// company falls back to Google's favicon service, then to the default
// bill icon if that fails too.
const BUNDLED_LOGOS = new Set(["eatsure.com", "rapido.bike", "croma.com", "ajio.com", "amazon.in", "flipkart.com", "zomato.com", "swiggy.com", "samsung.com", "oneplus.in", "myntra.com", "bigbasket.com", "blinkit.com", "uber.com", "olacabs.com", "apple.com", "mi.com", "boat-lifestyle.com", "reliancedigital.in", "dmart.in", "decathlon.in", "makemytrip.com", "irctc.co.in", "nykaa.com", "zepto.com", "ikea.com", "vijaysales.com"]);
function companyInfo(bill) {
  const seller = String(bill?.seller || "").toLowerCase();
  let domain = bill?.company_domain || "", about = bill?.company_about || "";
  if (!domain || !about) {
    for (const key of Object.keys(COMPANY_FALLBACK).sort((a, b) => b.length - a.length)) {
      if (seller.includes(key)) {
        if (!domain) domain = COMPANY_FALLBACK[key][0];
        if (!about) about = COMPANY_FALLBACK[key][1];
        break;
      }
    }
  }
  const favicon = domain ? `https://www.google.com/s2/favicons?domain=${domain}&sz=128` : "";
  const logo = (domain && BUNDLED_LOGOS.has(domain)) ? `/logos/${domain}.png`
    : bill?.logo_url || favicon;
  const logoFallback = logo !== favicon ? favicon : "";
  const source = bill?.source || (String(bill?.source_key || "").startsWith("gmail:") ? "gmail" : "scan");
  return { domain, about, logo, logoFallback, source };
}
// A bill's company logo: the real company logo when the AI/reader
// identified the company, a basic bill (receipt) icon when it could
// not — never a blank space. A small corner badge shows how the bill
// arrived: Gmail logo for Gmail imports, a camera for scanned bills.
function BillLogo({ bill, hideBadge = false }) {
  const { logo, logoFallback, source } = companyInfo(bill);
  // Stage 0: best logo (bundled with the site). Stage 1: Google's
  // favicon service for that company. Stage 2: the default bill icon —
  // so a missing logo file never leaves a broken image.
  const [stage, setStage] = useState(0);
  useEffect(() => { setStage(0); }, [logo]);
  const src = stage === 0 ? logo : stage === 1 ? logoFallback : "";
  return (
    <span className="bill-logo-wrap">
      {src
        ? <img className="bill-logo" src={src} alt="" loading="lazy" onError={() => setStage(s => (s === 0 && logoFallback && logoFallback !== logo) ? 1 : 2)} />
        : <span className="bill-logo bill-logo-default"><Receipt size={22} /></span>}
      {!hideBadge && (source === "gmail"
        ? <span className="source-badge source-gmail" title="Imported from Gmail"><img src={GMAIL_LOGO} alt="Gmail" /></span>
        : <span className="source-badge source-scan" title="Scanned / uploaded bill"><Camera size={9} /></span>)}
    </span>
  );
}
function daysLeft(end) {
  if (!end) return null;
  const d = new Date(`${end}T00:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86400000);
}
const CATEGORIES = ["Other", "Food", "Travel", "Electronics", "Fashion", "Home & Kitchen", "Appliances", "Furniture", "Personal care", "Education", "Health", "Entertainment", "Bills & Utilities"];
const CAT_COLORS = {
  "Food": ["#ffeef0", "#e11d48"], "Travel": ["#e0f2fe", "#0284c7"], "Electronics": ["#efeaff", "#6d4aff"],
  "Fashion": ["#e0f2fe", "#2563eb"], "Home & Kitchen": ["#fef3c7", "#b45309"], "Appliances": ["#dcfce7", "#15803d"],
  "Furniture": ["#ffedd5", "#c2410c"], "Personal care": ["#fce7f3", "#be185d"], "Education": ["#e0e7ff", "#4338ca"], "Health": ["#ccfbf1", "#0f766e"], "Entertainment": ["#fae8ff", "#a21caf"], "Bills & Utilities": ["#fef9c3", "#a16207"], "Other": ["#f1eefc", "#6d4aff"],
};

function App() {
  const [bills, setBills] = useState([]);
  const [query, setQuery] = useState("");
  const [searchFocus, setSearchFocus] = useState(false);
  const [filter, setFilter] = useState("All Documents");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedBill, setSelectedBill] = useState(null);
  const [spendingOpen, setSpendingOpen] = useState(false);
  const [agentQ, setAgentQ] = useState("");
  const [agentAnswer, setAgentAnswer] = useState("");
  const [agentBills, setAgentBills] = useState([]);
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentHistory, setAgentHistory] = useState([]);
  const [notice, setNotice] = useState("");
  const [activeNav, setActiveNav] = useState("Home");
  const [dark, setDark] = useState(() => { try { return localStorage.getItem("raseed_dark") === "1"; } catch { return false; } });
  const [remindersEnabled, setRemindersEnabled] = useState(true);
  const [gmailError, setGmailError] = useState("");
  const [gmailStatus, setGmailStatus] = useState(null); // null = not checked yet
  const [gmailImporting, setGmailImporting] = useState(false);
  const [gmailResult, setGmailResult] = useState("");
  const [summaryBills, setSummaryBills] = useState([]);
  const [auth, setAuth] = useState(() => readAuthCache()); // null = first visit, still checking sign-in
  const [profileForm, setProfileForm] = useState({ name: "", purpose: "Personal bills", age: "" });
  const [profileError, setProfileError] = useState("");
  const [profileSaving, setProfileSaving] = useState(false);
  const guest = !!auth && auth.auth_configured && !auth.connected;
  const guestRef = React.useRef(false);
  guestRef.current = guest;
  const gmailPollingRef = React.useRef(false);
  // Until the sign-in check answers, greet neutrally: showing a name
  // here made it look as if the user was signed in, then "flip" to
  // Guest once the real answer arrived.
  const firstName = auth?.profile?.name ? auth.profile.name.split(" ")[0] : guest ? "Guest" : "friend";

  // While any popup is open, lock the page behind it: scrolling
  // over a bill popup must move the popup, never the list under it.
  const profilePopupOpen = Boolean(auth?.connected && !auth.profile_complete);
  useEffect(() => {
    if (!(selectedBill || uploadOpen || profilePopupOpen || spendingOpen)) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [selectedBill, uploadOpen, profilePopupOpen, spendingOpen]);

  async function loadBills() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ q: query, status: filter });
      const res = await apiFetch(`/api/bills?${params}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not load bills.");
      if (guestRef.current) { setBills([]); setSummaryBills([]); setError(""); return; }
      setBills(Array.isArray(data) ? data : []);
      setError("");
      // Unfiltered list for the dashboard summary — counts must not change
      // just because a search/filter is applied to the list below.
      try {
        const allRes = await apiFetch(`/api/bills`);
        const allData = await allRes.json();
        if (allRes.ok && Array.isArray(allData)) setSummaryBills(allData);
      } catch { /* summary is best-effort */ }
    } catch (e) {
      setError(`${e.message}. Check that the backend is running and its URL is correct.`);
    } finally { setLoading(false); }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.has("gmail_error")) {
      setGmailError(`Google sign-in did not complete: ${params.get("gmail_error")}`);
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  useEffect(() => {
    // Is Gmail connected for bill importing? Checked whenever the
    // signed-in user changes; drives the Connect / Import card below.
    if (!auth?.connected) { setGmailStatus(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch("/api/gmail/status");
        const d = await r.json();
        if (!cancelled && r.ok) {
          setGmailStatus(d);
          // If a scan is still running on the server (e.g. the page
          // was refreshed mid-scan), reconnect to its progress.
          if (d.connected) {
            try {
              const sr = await apiFetch("/api/gmail/scan/status");
              const sd = await sr.json();
              if (!cancelled && sr.ok && sd.state === "running") pollGmailScan();
            } catch { /* progress resume is best-effort */ }
          }
        }
      } catch { /* leave as unknown; the card still offers Connect */ }
    })();
    return () => { cancelled = true; };
  }, [auth?.connected]);

  useEffect(() => {
    // Who is signed in? Drives the login gate and the first-time questions.
    captureLoginToken();
    let cancelled = false;
    async function checkAuth(attempt) {
      try {
        const r = await apiFetch("/api/auth/me");
        const d = await r.json();
        if (cancelled) return;
        // A saved token the server no longer accepts is cleared, so the
        // site settles into honest Guest mode instead of flip-flopping.
        if (!d?.connected && storedToken()) {
          try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(AUTH_CACHE_KEY); } catch { /* ignore */ }
        }
        writeAuthCache(d);
        setAuth(d);
        if (d?.profile?.name) setProfileForm(f => ({ ...f, name: d.profile.name }));
      } catch (err) {
        if (cancelled) return;
        // The free server may be waking up; one retry before giving up,
        // so a slow first answer never throws a signed-in user to Guest.
        if (attempt === 0) { setTimeout(() => checkAuth(1), 2500); return; }
        setAuth({ connected: false, auth_configured: false, profile: null, profile_complete: false });
      }
    }
    checkAuth(0);
    return () => { cancelled = true; };
  }, []);

  async function saveProfile(e) {
    e.preventDefault();
    setProfileError("");
    if (!profileForm.name.trim()) { setProfileError("Please tell us your name."); return; }
    const age = parseInt(profileForm.age, 10);
    if (!age || age < 5 || age > 120) { setProfileError("Please enter your age."); return; }
    setProfileSaving(true);
    try {
      const res = await apiFetch("/api/auth/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: profileForm.name.trim(), purpose: profileForm.purpose, age }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not save your details.");
      writeAuthCache(data);
      setAuth(data);
      loadBills();
    } catch (err) {
      setProfileError(err.message);
    } finally { setProfileSaving(false); }
  }

  async function logout() {
    try { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(AUTH_CACHE_KEY); } catch { /* ignore */ }
    try { await apiFetch("/api/auth/logout", { method: "POST" }); } catch { /* reload anyway */ }
    window.location.reload();
  }

  async function pollGmailScan() {
    // The scan runs on the server in the background; this loop only
    // follows its progress. It keeps running after a page refresh too
    // (the status check below restarts it), so a refresh no longer
    // loses the scan or its result.
    if (gmailPollingRef.current) return;
    gmailPollingRef.current = true;
    setGmailImporting(true);
    let lastImported = -1;
    try {
      for (let i = 0; i < 240; i++) {
        await new Promise(r => setTimeout(r, 3000));
        let s;
        try {
          const r = await apiFetch("/api/gmail/scan/status");
          if (!r.ok) break;
          s = await r.json();
        } catch { continue; }
        if (s.state === "running") {
          setGmailResult(`Scanning your Gmail… checked ${s.checked} email${s.checked === 1 ? "" : "s"}, imported ${s.imported} new bill${s.imported === 1 ? "" : "s"} so far. New bills appear in your list below as they are found — no need to refresh.`);
          if (s.imported !== lastImported) {
            lastImported = s.imported;
            if (s.imported > 0) { setFilter("All Documents"); setQuery(""); await loadBills(); }
          }
          continue;
        }
        if (s.state === "done") {
          setGmailResult(`Checked ${s.checked} emails — imported ${s.imported} new bill${s.imported === 1 ? "" : "s"}${s.skipped ? `, skipped ${s.skipped} (already saved or not bills)` : ""}. ${s.note || ""}`);
          if (s.imported > 0) { setFilter("All Documents"); setQuery(""); await loadBills(); }
        } else if (s.state === "error") {
          if (s.status_code === 401) {
            setGmailStatus({ connected: false, email: null });
            setGmailResult("Your Gmail connection needs to be renewed — please connect Gmail again.");
          } else {
            setGmailResult(s.detail || "Gmail import failed.");
          }
        }
        break;
      }
    } finally {
      gmailPollingRef.current = false;
      setGmailImporting(false);
    }
  }

  async function importFromGmail() {
    setGmailResult("");
    try {
      const res = await apiFetch("/api/gmail/scan", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        setGmailStatus({ connected: false, email: null });
        setGmailResult("Your Gmail connection needs to be renewed — please connect Gmail again.");
        return;
      }
      if (!res.ok) throw new Error(data.detail || "Gmail import failed.");
      setGmailResult("Starting your Gmail scan…");
      await pollGmailScan();
    } catch (e) {
      setGmailResult(e.message);
    }
  }

  // The top Sync Gmail button always takes the user to My Bills, so
  // they watch the scan happening there — progress at the top of the
  // list and new bills appearing in it as they are imported.
  function syncGmail() {
    setActiveNav("My Bills");
    setFilter("All Documents");
    setQuery("");
    importFromGmail();
  }

  useEffect(() => {
    const timer = setTimeout(loadBills, 200);
    return () => clearTimeout(timer);
  }, [query, filter]);

  useEffect(() => {
    // Signed-out visitors see an empty vault — bills belong to an account.
    if (guest) { setBills([]); setSummaryBills([]); setLoading(false); }
  }, [guest]);

  function navigate(label) {
    setActiveNav(label);
    if (label === "Home" || label === "My Bills") setFilter("All Documents");
    if (label === "Warranty Tracker") setFilter("Active Warranties");
    if (label === "Reminders") setFilter("Expiring Soon");
  }

  async function uploadFile(file, form) {
    const body = new FormData();
    body.append("file", file);
    body.append("warranty_months", form.warrantyMonths || "0");
    body.append("category", form.category || "Other");
    body.append("product_override", form.product || "");
    body.append("seller_override", form.seller || "");
    body.append("purchase_date_override", form.purchaseDate || "");
    body.append("amount_override", form.amount || "");
    body.append("extracted_text_override", form.extractedText || "");
    const res = await apiFetch("/api/bills", { method: "POST", body });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Upload failed.");
    setNotice("Bill uploaded and saved successfully.");
    setUploadOpen(false); setFilter("All Documents"); setQuery("");
    await loadBills();
  }

  async function removeBill(bill) {
    if (!window.confirm(`Delete "${bill.product}" and its stored file?`)) return;
    try {
      const res = await apiFetch(`/api/bills/${bill.id}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not delete bill.");
      setSelectedBill(null); setNotice("Bill deleted."); await loadBills();
    } catch (e) { setError(e.message); }
  }

  async function updateBill(bill, fields) {
    const res = await apiFetch(`/api/bills/${bill.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(fields)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not update bill.");
    setSelectedBill(data); setNotice("Bill details updated."); await loadBills();
  }

  async function askAgent(e) {
    if (e) e.preventDefault();
    const message = agentQ.trim();
    if (!message || agentBusy) return;
    setAgentBusy(true); setAgentAnswer(""); setAgentBills([]);
    try {
      const res = await apiFetch("/api/agent/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, history: agentHistory }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "The agent could not answer.");
      setAgentAnswer(data.answer || "");
      setAgentBills(Array.isArray(data.bills) ? data.bills : []);
      setAgentHistory(h => [...h.slice(-2), { question: message, answer: String(data.answer || "").slice(0, 300) }]);
      if (data.tool === "check_all") await loadBills();
    } catch (err) { setAgentAnswer(`Sorry \u2014 ${err.message}`); }
    finally { setAgentBusy(false); }
  }

  const counts = useMemo(() => ({
    all: summaryBills.length,
    active: summaryBills.filter(b => b.warranty_status === "Active").length,
    soon: summaryBills.filter(b => b.warranty_status === "Expiring Soon").length,
    expired: summaryBills.filter(b => b.warranty_status === "Expired").length,
    noWarranty: summaryBills.filter(b => b.warranty_status === "No Warranty").length,
    review: summaryBills.filter(b => b.needs_review).length,
    totalSpent: summaryBills.reduce((sum, b) => sum + (Number(b.amount) || 0), 0)
  }), [summaryBills]);

  // Instant client-side filtering on top of the server results, so typing in
  // search visibly filters straight away (the old build appeared not to
  // filter at all while it waited on a cold/slow backend response).
  const visibleBills = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return bills;
    return bills.filter(b => [b.product, b.seller, b.category, b.original_filename]
      .map(v => String(v || "").toLowerCase()).join(" ").includes(needle));
  }, [bills, query]);
  // Google-style live suggestions under the search box, searched across
  // the full vault (summaryBills), not just the currently listed page.
  const searchMatches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    const pool = summaryBills.length ? summaryBills : bills;
    return pool.filter(b => [b.product, b.seller, b.category, b.original_filename, b.invoice_no, b.order_no]
      .map(v => String(v || "").toLowerCase()).join(" ").includes(needle)).slice(0, 6);
  }, [summaryBills, bills, query]);
  // Home (pastel template): exactly the 5 most recent bills in the left
  // column; warranty reminders and spending live in the right rail.
  const recentBills = useMemo(() => (summaryBills.length ? summaryBills : bills).slice(0, 5), [summaryBills, bills]);
  const warrantyBills = useMemo(() => (summaryBills.length ? summaryBills : bills)
    .filter(b => b.warranty_lifetime || b.warranty_end)
    .sort((a, b) => {
      const ea = daysLeft(a.warranty_end), eb = daysLeft(b.warranty_end);
      const ax = a.warranty_lifetime ? 0 : ea !== null && ea < 0 ? 1 : 0, bx = b.warranty_lifetime ? 0 : eb !== null && eb < 0 ? 1 : 0;
      return ax - bx || String(a.warranty_end || "9999").localeCompare(String(b.warranty_end || "9999"));
    }).slice(0, 3), [summaryBills, bills]);
  const DONUT_COLORS = ["#6d4aff", "#fb7185", "#38bdf8", "#c4b5fd", "#fbbf24", "#34d399", "#f97316", "#f472b6", "#a3e635", "#94a3b8", "#2dd4bf", "#e879f9", "#facc15"];
  const spending = useMemo(() => {
    const byCat = {};
    (summaryBills.length ? summaryBills : bills).forEach(b => { const c = b.category || "Other"; byCat[c] = (byCat[c] || 0) + (Number(b.amount) || 0); });
    return Object.entries(byCat).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  }, [summaryBills, bills]);
  const donutGradient = useMemo(() => {
    const total = spending.reduce((s, [, v]) => s + v, 0);
    if (!total) return "#efeaff";
    let acc = 0; const parts = spending.map(([, v], i) => { const start = (acc / total) * 100; acc += v; return `${DONUT_COLORS[i % DONUT_COLORS.length]} ${start}% ${(acc / total) * 100}%`; });
    return `conic-gradient(${parts.join(", ")})`;
  }, [spending]);
  const pageTitle = activeNav === "Home" ? (auth ? `Good evening, ${firstName}` : "Good evening") : activeNav;
  const pageSubtitle = activeNav === "Home" ? "Every purchase, safely kept in one place." :
    activeNav === "My Bills" ? "Find and manage all your purchase receipts." :
    activeNav === "Warranty Tracker" ? "Know what is covered and what needs attention." :
    activeNav === "Reminders" ? "Bills with warranties that need a little attention." : "Make Raseed work the way you like.";

  return (
    <div className={`app-shell ${dark ? "dark" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><ClipboardList size={25} strokeWidth={2.1} /></span>
          <span className="brand-name">रसीद</span>
        </div>
        <div className="brand-caption">YOUR PERSONAL BILL VAULT</div>
        <nav className="side-nav">
          {NAV.map(([Icon, label]) => (
            <button key={label} className={`nav-item ${activeNav === label ? "selected" : ""}`} onClick={() => navigate(label)}>
              <Icon size={19} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <p><b>Never lose a bill again!</b><br />You shop, we remember.</p>
        </div>
        <div className="sidebar-bottom"><ShieldCheck size={15} /> Your bills, in one safe place</div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="search-wrap">
            <div className="searchbox"><Search size={19} /><input value={query} onChange={e => setQuery(e.target.value)} onFocus={() => setSearchFocus(true)} onBlur={() => setTimeout(() => setSearchFocus(false), 150)} onKeyDown={e => { if (e.key === "Escape") setSearchFocus(false); }} placeholder="Search bills, products or brands..." />
              {query && <button aria-label="Clear search" onClick={() => setQuery("")}><X size={16} /></button>}
            </div>
            {searchFocus && query.trim() && (
              <div className="search-results">
                {searchMatches.length ? searchMatches.map(bill => (
                  <button key={bill.id} className="search-result" onMouseDown={e => { e.preventDefault(); setSelectedBill(bill); setQuery(""); setSearchFocus(false); }}>
                    <BillLogo bill={bill} hideBadge />
                    <span className="sr-main"><b>{bill.product || bill.original_filename || "Untitled bill"}</b><small>{bill.seller || bill.category || "Purchase"} · {formatDate(bill.purchase_date)}</small></span>
                    <span className="sr-amount">{money(bill.amount)}</span>
                  </button>
                )) : <div className="search-none">No bills match “{query.trim()}”</div>}
              </div>
            )}
          </div>
          <div className="top-actions">
            {auth && !guest && gmailStatus !== null && (gmailStatus.connected
              ? <button className="sync-gmail-btn" onClick={syncGmail} disabled={gmailImporting}><span className="gmail-logo-circle"><img src={GMAIL_LOGO} alt="Gmail" /></span>{gmailImporting ? "Syncing…" : "Sync Gmail"}</button>
              : <a className="sync-gmail-btn" href={`${API}/api/auth/gmail/connect`}><span className="gmail-logo-circle"><img src={GMAIL_LOGO} alt="Gmail" /></span>Connect Gmail</a>)}
            <button className="icon-button" title="Warranty reminders" onClick={() => navigate("Reminders")}><Bell size={20} />{remindersEnabled && counts.soon > 0 && <i />}</button>
            {auth === null
              ? <span style={{ display: "inline-block", width: 118, height: 40 }} />
              : guest
              ? <a className="user-chip" href={`${API}/api/auth/google`}><span className="avatar"><LogIn size={15} /></span><span>Login</span></a>
              : <button className="user-chip" onClick={() => navigate("Settings")}><span className="avatar">{firstName.charAt(0).toUpperCase()}</span><span>Hi, {firstName}</span><ChevronDown size={15} /></button>}
          </div>
        </header>

        {activeNav !== "Home" && (
          <section className="welcome">
            <div><div className="eyebrow">NAMASTE{auth ? `, ${firstName.toUpperCase()}` : ""}</div><h1>{pageTitle} <Heart className="heart" size={26} /></h1><p>{pageSubtitle}</p></div>
          </section>
        )}

        {notice && <div className="notice"><Check size={17} /><span>{notice}</span><button onClick={() => setNotice("")}><X size={16} /></button></div>}
        {error && <div className="alert-error">{error}<button onClick={() => { setError(""); loadBills(); }}>Try again</button></div>}
        {gmailError && <div className="alert-error">{gmailError}<button onClick={() => setGmailError("")}><X size={16} /></button></div>}
        {gmailResult && activeNav !== "My Bills" && <div className="notice"><Check size={17} /><span>{gmailResult}</span><button onClick={() => setGmailResult("")}><X size={16} /></button></div>}

        {activeNav === "Home" && (
          <div className="home-grid">
            <div className="home-left">
              <section className="hero-card">
                <div className="hero-copy">
                  <h1>{auth ? `Good evening, ${firstName}` : "Good evening"} 👋</h1>
                  <p>All your bills, warranties and purchases in one smart place.</p>
                  {auth !== null && (guest
                    ? <a className="hero-pill hero-pill-link" href={`${API}/api/auth/google`}><LogIn size={17} /> Login to see your bills <span className="hero-go">→</span></a>
                    : <button className="hero-pill" onClick={() => setUploadOpen(true)}><Upload size={16} /> Search or upload a bill… <span className="hero-go">→</span></button>)}
                  <span className="hero-hand">Scan, Save, Stay worry-free!</span>
                </div>
                <div className="hero-art"><div className="hero-phone"><span className="hero-phone-logo"><ClipboardList size={22} /></span><b>रसीद</b><span className="hero-check">✓</span></div></div>
              </section>

              <section className="summary-grid" aria-label="Your bill summary">
                <div className="summary-card"><span className="stat-tile" style={{ background: "#efeaff" }}>📄</span><div><small>Total Bills</small><b>{counts.all}</b><span>saved in your vault</span></div></div>
                <div className="summary-card"><span className="stat-tile" style={{ background: "#ffe4ec" }}>👛</span><div><small>Total Spent</small><b>{money(counts.totalSpent || null)}</b><span>across {counts.all} bills</span></div></div>
                <div className="summary-card"><span className="stat-tile" style={{ background: "#dcfce7" }}>🛡️</span><div><small>Active Warranties</small><b>{counts.active}</b><span>currently covered</span></div></div>
                <div className="summary-card"><span className="stat-tile" style={{ background: "#ffedd5" }}>⌛</span><div><small>Expiring Soon</small><b>{counts.soon}</b><span>in next 30 days</span></div></div>
              </section>

              <section className="panel home-bills">
                <div className="panel-heading"><div><h2>Recent Bills</h2></div><button className="text-button view-all" onClick={() => navigate("My Bills")}>View all</button></div>
                {guest ? <div className="empty-state"><div className="empty-icon"><FileText size={27} /></div><b>Login to see your bills</b><span>Your vault is personal — your bills appear here only after you login.</span><a className="primary-button" href={`${API}/api/auth/google`}><LogIn size={17} /> Login with Google</a></div>
                  : recentBills.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileText size={27} /></div><b>No bills here yet</b><span>Upload your first receipt and it will appear here.</span><button className="primary-button" onClick={() => setUploadOpen(true)}><Plus size={17} /> Add a bill</button></div>
                  : <div className="bill-list">{recentBills.map(bill => { const info = companyInfo(bill); const cc = CAT_COLORS[bill.category] || CAT_COLORS.Other; return (
                      <button className="bill-row home-row" key={bill.id} onClick={() => setSelectedBill(bill)}>
                        <BillLogo bill={bill} />
                        <span className="bill-main"><b>{bill.seller || bill.product || "Untitled bill"}</b><small>{bill.product || bill.category || "Purchase"}{info.about ? ` · ${info.about}` : ""}</small></span>
                        <span className="bill-date">{formatDate(bill.purchase_date)}</span>
                        <span className="bill-amt">{money(bill.amount)}</span>
                        <span className="cat-chip" style={{ background: cc[0], color: cc[1] }}>{bill.category || "Other"}</span>
                        <span className="row-arrow"><ChevronDown size={17} /></span>
                      </button>); })}
                    </div>}
              </section>
            </div>

            <div className="home-right">
              {guest
                ? <section className="upload-card"><span className="upload-cam">📷</span><b>Scan / Upload Bill</b><span>Login first — your bills are saved to your own vault.</span></section>
                : <button className="upload-card" onClick={() => setUploadOpen(true)}><span className="upload-cam">📷</span><b>Scan / Upload Bill</b><span>Drag &amp; drop or click to upload</span><small>Supports images, PDFs and screenshots</small></button>}
              <section className="panel rail-card">
                <div className="panel-heading"><div><h2>Warranty Reminders</h2></div><button className="text-button view-all" onClick={() => navigate("Reminders")}>View all</button></div>
                {warrantyBills.length === 0 ? <p className="rail-empty">No warranties yet — bills with a warranty will show here with their days left.</p>
                  : warrantyBills.map(bill => { const left = daysLeft(bill.warranty_end); return (
                    <button className="wrow" key={bill.id} onClick={() => setSelectedBill(bill)}>
                      <BillLogo bill={bill} hideBadge />
                      <span className="bill-main"><b>{bill.product || bill.seller || "Bill"}</b><small>{bill.warranty_lifetime ? "Lifetime warranty" : left !== null && left < 0 ? `Expired on ${formatDate(bill.warranty_end)}` : `Expires on ${formatDate(bill.warranty_end)}`}</small></span>
                      <span className={`days-pill ${bill.warranty_lifetime ? "green" : left !== null && left < 0 ? "pink" : left !== null && left <= 30 ? "pink" : "green"}`}>{bill.warranty_lifetime ? "Lifetime" : left === null ? "—" : left < 0 ? "Expired" : `${left} days left`}</span>
                    </button>); })}
              </section>
              <section className="panel rail-card">
                <div className="panel-heading"><div><h2>Your Spending</h2></div><span className="rail-select">All time</span></div>
                {spending.length === 0 ? <p className="rail-empty">No spending yet — add bills and your spending chart will appear here.</p>
                  : <div className="donut-wrap"><div className="donut" style={{ background: donutGradient }}><div className="donut-hole"><b>{money(counts.totalSpent || null)}</b><span>Total</span></div></div>
                    <div className="donut-legend">{spending.slice(0, 4).map(([cat, amt], i) => <div className="lrow" key={cat}><i style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} /><span className="lrow-name">{cat}</span><span className="lrow-pct">{counts.totalSpent ? Math.round((amt / counts.totalSpent) * 100) : 0}%</span><b>{money(amt)}</b></div>)}</div></div>}
                {spending.length > 4 && <button className="see-more-btn" onClick={() => setSpendingOpen(true)}>See more · {spending.length - 4} more {spending.length - 4 === 1 ? "category" : "categories"}</button>}
              </section>
            </div>
          </div>
        )}

        {activeNav === "Settings" && (
          <section className="panel settings-panel">
            <div className="panel-heading"><div><h2>Preferences</h2><p>Simple controls for your Raseed workspace.</p></div></div>
            <div className="setting-row"><div className="setting-symbol"><Bell size={19} /></div><div className="setting-copy"><b>Warranty reminders</b><span>Show bills whose warranty is expiring soon.</span></div><button className={`toggle ${remindersEnabled ? "on" : ""}`} onClick={() => { setRemindersEnabled(v => !v); setNotice(`Warranty reminders ${remindersEnabled ? "paused" : "enabled"}.`); }} aria-label="Toggle reminders"><span /></button></div>
            <div className="setting-row"><div className="setting-symbol"><Moon size={19} /></div><div className="setting-copy"><b>Appearance</b><span>Switch between light and dark themes.</span></div><button className="soft-button" onClick={() => { const next = !dark; setDark(next); try { localStorage.setItem("raseed_dark", next ? "1" : "0"); } catch { /* storage unavailable */ } }}>{dark ? <Sun size={16} /> : <Moon size={16} />}{dark ? "Light mode" : "Dark mode"}</button></div>
            <div className="setting-row"><div className="setting-symbol"><ShieldCheck size={19} /></div><div className="setting-copy"><b>File storage</b><span>Uploads are sent to your backend; Cloudinary credentials stay server-side.</span></div></div>
            {auth?.connected && (
              <div className="setting-row"><div className="setting-symbol"><Mail size={19} /></div><div className="setting-copy"><b>Gmail import</b><span>{gmailStatus?.connected ? `Connected (${gmailStatus.email}) — import invoice emails into your vault.` : "Connect Gmail to import invoice emails automatically."}</span></div>{gmailStatus?.connected ? <button className="soft-button" onClick={syncGmail} disabled={gmailImporting}>{gmailImporting ? "Scanning…" : "Import bills"}</button> : <a className="soft-button" href={`${API}/api/auth/gmail/connect`}>Connect Gmail</a>}</div>
            )}
            {auth?.connected && (
              <div className="setting-row"><div className="setting-symbol"><UserRound size={19} /></div><div className="setting-copy"><b>{auth.profile?.name || "Your account"}</b><span>{auth.email}{auth.profile?.purpose ? ` · ${auth.profile.purpose}` : ""}{auth.profile?.age ? ` · Age ${auth.profile.age}` : ""}</span></div><button className="soft-button" onClick={logout}><LogOut size={16} /> Log out</button></div>
            )}
          </section>
        )}

        {activeNav !== "Settings" && activeNav !== "Home" && (
          <section className="panel bills-panel">
            <div className="panel-heading">
              <div><div className="eyebrow">{activeNav === "Home" ? "YOUR COLLECTION" : "MANAGE YOUR RECEIPTS"}</div><h2>{activeNav === "Home" ? "Recently added bills" : activeNav === "My Bills" ? "My Bills" : activeNav === "Warranty Tracker" ? "Warranty overview" : "Upcoming warranty reminders"}</h2><p>{activeNav === "Warranty Tracker" ? "Warranty details are shown here, not on the home page." : activeNav === "Reminders" ? "Items marked expiring soon are listed first." : "Open a bill to view or update its details."}</p></div>
            </div>
            {activeNav === "My Bills" && (gmailImporting || gmailResult) && (
              <div className={`scan-banner ${gmailImporting ? "scanning" : "done"}`}>
                <span className="gmail-logo-circle"><img src={GMAIL_LOGO} alt="Gmail" /></span>
                {gmailImporting && <RefreshCw className="spin" size={17} />}
                <span>{gmailResult || "Scanning your Gmail…"}</span>
              </div>
            )}
            {activeNav === "My Bills" && !guest && (
              <div className="agent-card">
                <div className="agent-head"><Sparkles size={17} /><b>Ask Raseed</b><span>Your bill agent — it searches, totals and checks your vault for you.</span></div>
                <form className="agent-form" onSubmit={askAgent}>
                  <input value={agentQ} onChange={e => { setAgentQ(e.target.value); if (agentAnswer || agentBills.length) { setAgentAnswer(""); setAgentBills([]); } }} placeholder="Try: How much did I spend on food? · Which bills need review? · Check all my bills" />
                  <button className="primary-button" type="submit" disabled={agentBusy}>{agentBusy ? "Thinking\u2026" : "Ask"}</button>
                </form>
                {agentAnswer && <div className="agent-answer">{agentAnswer}</div>}
                {agentBills.length > 0 && <div className="agent-bills">{agentBills.map(b => <button key={b.id} className="agent-bill-chip" onClick={() => setSelectedBill(b)}>{b.product || b.seller || "Bill"}{b.amount ? ` \u00b7 ${money(b.amount)}` : ""}{b.needs_review ? " \u00b7 Needs review" : ""}</button>)}</div>}
              </div>
            )}
            <div className="list-toolbar">
              <div className="filter-tabs">{(NAV_FILTERS[activeNav] || []).map(f => <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f === "Needs Review" && counts.review ? `${f} (${counts.review})` : f}</button>)}</div>
              <button className="sort-button" onClick={loadBills}><RefreshCw size={15} /> Refresh</button>
            </div>
            {loading ? <div className="empty-state"><RefreshCw className="spin" size={24} /><b>Loading your bills…</b></div> :
              visibleBills.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileText size={27} /></div><b>{guest ? "Login to see your bills" : query ? "No matching bills" : filter !== "All Documents" ? "No bills in this view" : "No bills here yet"}</b><span>{guest ? "Your vault is personal — your bills appear here only after you login." : query ? "Try a different search." : filter !== "All Documents" ? `You have ${counts.all} bill${counts.all === 1 ? "" : "s"} saved, but none match “${filter}” right now.` : "Upload your first receipt and it will appear here."}</span>{guest ? <a className="primary-button" href={`${API}/api/auth/google`}><LogIn size={17} /> Login with Google</a> : filter !== "All Documents" || query ? <button className="primary-button" onClick={() => { setQuery(""); navigate("My Bills"); }}>Show all documents</button> : <button className="primary-button" onClick={() => setUploadOpen(true)}><Plus size={17} /> Add a bill</button>}</div> :
              <div className="bill-list">
                {visibleBills.map(bill => { const info = companyInfo(bill); return <button className="bill-row" key={bill.id} onClick={() => setSelectedBill(bill)}>
                  <BillLogo bill={bill} />
                  <span className="bill-main"><b>{bill.product || bill.original_filename || "Untitled bill"}{bill.needs_review ? <span className="review-chip">Needs review</span> : null}</b><small>{bill.seller || bill.category || "Purchase"}{info.about ? ` · ${info.about}` : ""} · {formatDate(bill.purchase_date)}</small></span>
                  <span className="bill-meta"><small>AMOUNT</small><b>{money(bill.amount)}</b></span>
                  <span className="bill-meta warranty-info"><small>WARRANTY ENDS</small><b>{bill.warranty_lifetime ? "Never (lifetime)" : formatDate(bill.warranty_end)}</b></span>
                  <Status status={bill.warranty_status} />
                  <span className="row-arrow"><ChevronDown size={17} /></span>
                </button>; })}
              </div>
            }
          </section>
        )}
        <footer className="page-footer">रसीद <span>•</span> HAR BILL SAMBHAAL KE</footer>
      </main>

      {auth?.connected && !auth.profile_complete && (
        <div className="modal-backdrop">
          <div className="modal auth-modal">
            <div className="brand auth-brand"><span className="brand-mark"><ClipboardList size={25} strokeWidth={2.1} /></span><span className="brand-name">रसीद</span></div>
            <h2 className="auth-heading">Welcome to Raseed</h2>
            <p className="auth-copy">First time here? Three quick questions and your vault is ready.</p>
            <form onSubmit={saveProfile} className="auth-form">
              <label className="field"><span>Your name</span>
                <input value={profileForm.name} onChange={e => setProfileForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Urvi Garg" autoFocus />
              </label>
              <label className="field"><span>What will you use Raseed for?</span>
                <select value={profileForm.purpose} onChange={e => setProfileForm(f => ({ ...f, purpose: e.target.value }))}>
                  <option>Personal bills</option>
                  <option>Family bills</option>
                  <option>Business / shop bills</option>
                  <option>Tracking warranties</option>
                  <option>A bit of everything</option>
                </select>
              </label>
              <label className="field"><span>Your age</span>
                <input type="number" min="5" max="120" value={profileForm.age} onChange={e => setProfileForm(f => ({ ...f, age: e.target.value }))} placeholder="e.g. 21" />
              </label>
              {profileError && <div className="error-box">{profileError}</div>}
              <button className="primary-button auth-submit" type="submit" disabled={profileSaving}>{profileSaving ? "Saving…" : "Start using Raseed"}</button>
            </form>
            <button className="auth-link" onClick={logout}>Sign in with a different account</button>
          </div>
        </div>
      )}
      {uploadOpen && <UploadModal onClose={() => setUploadOpen(false)} onUpload={uploadFile} />}
      {selectedBill && <BillModal bill={selectedBill} onClose={() => setSelectedBill(null)} onDelete={removeBill} onSave={updateBill} />}
      {spendingOpen && <div className="modal-backdrop" onMouseDown={e => e.target === e.currentTarget && setSpendingOpen(false)}><div className="modal spending-modal">
        <div className="modal-head"><div><div className="eyebrow">YOUR SPENDING</div><h2>All categories</h2><p>Every category in your vault, biggest first — nothing is hidden.</p></div><button className="icon-button" onClick={() => setSpendingOpen(false)}><X size={20} /></button></div>
        <div className="spending-modal-body">
          <div className="donut" style={{ background: donutGradient }}><div className="donut-hole"><b>{money(counts.totalSpent || null)}</b><span>Total</span></div></div>
          <div className="donut-legend full">{spending.map(([cat, amt], i) => <div className="lrow" key={cat}><i style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} /><span className="lrow-name">{cat}</span><span className="lrow-pct">{counts.totalSpent ? Math.round((amt / counts.totalSpent) * 100) : 0}%</span><b>{money(amt)}</b></div>)}</div>
        </div>
      </div></div>}
    </div>
  );
}

async function runClientOcr(file, onProgress) {
  // OCR in the browser (tesseract.js) so bill reading works even when the
  // backend host has no Tesseract installed. The extracted text is sent to
  // the backend, which parses product / seller / date / amount from it.
  const { createWorker } = await import("tesseract.js");
  const worker = await createWorker("eng", 1, {
    logger: m => { if (m.status === "recognizing text") onProgress(Math.round((m.progress || 0) * 100)); }
  });
  try {
    const { data } = await worker.recognize(file);
    return (data && data.text) || "";
  } finally {
    await worker.terminate();
  }
}

function UploadModal({ onClose, onUpload }) {
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({ product: "", seller: "", purchaseDate: "", amount: "", warrantyMonths: "0", category: "Other", extractedText: "" });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [ocrState, setOcrState] = useState(""); // "", "running", "done", "failed", "pdf"
  const [ocrProgress, setOcrProgress] = useState(0);

  async function handleFile(nextFile) {
    setFile(nextFile); setError("");
    setForm(old => ({ ...old, extractedText: "" }));
    if (!nextFile) { setOcrState(""); return; }
    if (nextFile.type === "application/pdf" || nextFile.name.toLowerCase().endsWith(".pdf")) {
      setOcrState("pdf"); // the backend extracts PDF text / runs OCR server-side
      return;
    }
    if (!nextFile.type.startsWith("image/")) { setOcrState(""); return; }
    setOcrState("running"); setOcrProgress(0);
    try {
      const text = await runClientOcr(nextFile, setOcrProgress);
      if (text && text.trim().length >= 20) {
        setForm(old => ({ ...old, extractedText: text }));
        setOcrState("done");
      } else {
        setOcrState("failed");
      }
    } catch {
      setOcrState("failed"); // upload still works; the backend will try its own OCR
    }
  }
  function change(key, value) { setForm(old => ({ ...old, [key]: value })); }
  async function submit(e) {
    e.preventDefault();
    if (!file) { setError("Please choose a receipt image or PDF first."); return; }
    setSaving(true); setError("");
    try { await onUpload(file, form); }
    catch (err) { setError(err.message); }
    finally { setSaving(false); }
  }
  return <div className="modal-backdrop" onMouseDown={e => e.target === e.currentTarget && onClose()}><form className="modal upload-modal" onSubmit={submit}>
    <div className="modal-head"><div><div className="eyebrow">ADD TO YOUR VAULT</div><h2>Scan / Upload a bill</h2><p>Choose a receipt image or PDF. The backend will extract what it can.</p></div><button type="button" className="icon-button" onClick={onClose}><X size={20} /></button></div>
    <label className="dropzone"><input type="file" accept="image/jpeg,image/png,image/webp,application/pdf,.jpg,.jpeg,.png,.webp,.pdf" onChange={e => { handleFile(e.target.files?.[0] || null); }} /><span className="upload-circle"><Upload size={22} /></span><b>{file ? file.name : "Choose a bill to upload"}</b><small>JPG, PNG, WEBP or PDF · up to 12 MB</small></label>
    {file && <div className="file-selected"><Check size={15} /> Ready to upload · {Math.max(1, Math.round(file.size / 1024))} KB</div>}
    {ocrState === "running" && <div className="file-selected">Reading your bill with OCR… {ocrProgress}%</div>}
    {ocrState === "done" && <div className="file-selected"><Check size={15} /> Bill text read by OCR — details will be filled in automatically. You can still correct them below.</div>}
    {ocrState === "failed" && <div className="file-selected">Couldn’t read text from this image here — the server will try, and you can fill the details in below.</div>}
    {ocrState === "pdf" && <div className="file-selected">PDF selected — its text will be extracted on the server when you upload.</div>}
    <div className="form-grid">
      <label>Product / item<input value={form.product} onChange={e => change("product", e.target.value)} placeholder="e.g. Wireless headphones" /></label>
      <label>Seller / brand<input value={form.seller} onChange={e => change("seller", e.target.value)} placeholder="e.g. Croma" /></label>
      <label>Purchase date<input type="date" value={form.purchaseDate} onChange={e => change("purchaseDate", e.target.value)} /></label>
      <label>Amount (₹)<input type="number" min="0" step="0.01" value={form.amount} onChange={e => change("amount", e.target.value)} placeholder="e.g. 2499" /></label>
      <label>Warranty duration<select value={form.warrantyMonths} onChange={e => change("warrantyMonths", e.target.value)}><option value="0">No warranty / unknown</option><option value="3">3 months</option><option value="6">6 months</option><option value="12">1 year</option><option value="24">2 years</option><option value="36">3 years</option><option value="60">5 years</option><option value="120">10 years</option></select></label>
      <label>Category<select value={form.category} onChange={e => change("category", e.target.value)}>{CATEGORIES.map(c => <option key={c}>{c}</option>)}</select></label>
    </div>
    <p className="ocr-note"><ShieldCheck size={15} /> OCR reads your bill (in your browser for photos, on the server for PDFs) and fills in product, seller, date and amount. Check the saved details against your original bill.</p>
    {error && <div className="inline-error">{error}</div>}
    <div className="modal-foot"><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button type="submit" className="primary-button" disabled={saving}>{saving ? "Uploading…" : <><Upload size={16} /> Upload bill</>}</button></div>
  </form></div>;
}

function BillModal({ bill, onClose, onDelete, onSave }) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [showText, setShowText] = useState(false);
  const [fileBusy, setFileBusy] = useState(false);
  async function viewFile() {
    const win = window.open("", "_blank");
    setFileBusy(true); setError("");
    try {
      const res = await apiFetch(`/api/bills/${bill.id}/file`);
      if (!res.ok) { let msg = "Could not open the bill file. Please try again."; try { const d = await res.json(); if (d && d.detail) msg = `Could not open the bill file — ${d.detail}`; } catch { /* keep generic */ } throw new Error(msg); }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      if (win) win.location = url; else window.location = url;
    } catch (e) { if (win) win.close(); setError(e.message); }
    finally { setFileBusy(false); }
  }
  const [form, setForm] = useState({ product: bill.product || "", seller: bill.seller || "", purchase_date: bill.purchase_date || "", amount: bill.amount ?? "", warranty_months: bill.warranty_months ?? 0, category: bill.category || "Other", invoice_no: bill.invoice_no || "", order_no: bill.order_no || "", warranty_lifetime: bill.warranty_lifetime ? 1 : 0 });
  function change(key, value) { setForm(old => ({ ...old, [key]: value })); }
  async function save() {
    setSaving(true); setError("");
    try {
      const payload = { ...form, amount: form.amount === "" ? null : Number(form.amount), warranty_months: Number(form.warranty_months) };
      await onSave(bill, payload); setEditing(false);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  return <div className="modal-backdrop" onMouseDown={e => e.target === e.currentTarget && onClose()}><div className="modal detail-modal">
    <div className="modal-head"><div><div className="eyebrow">YOUR SAVED RECEIPT</div><h2>{editing ? "Edit bill details" : bill.product || "Bill details"}</h2><p>{bill.seller || bill.original_filename || "Purchase record"}</p></div><button className="icon-button" onClick={onClose}><X size={20} /></button></div>
    <div className="detail-status"><Status status={bill.warranty_status} /><span>Added {formatDate((bill.created_at || "").slice(0,10))}</span></div>
    {bill.needs_review ? <div className="review-banner"><div><b>Needs review</b><span>{bill.review_reason || "The agent could not fully verify this bill \u2014 please check it once."}</span></div><button className="secondary-button" disabled={saving} onClick={async () => { setSaving(true); setError(""); try { await onSave(bill, { needs_review: 0 }); } catch (e) { setError(e.message); } finally { setSaving(false); } }}>Mark as correct</button></div> : null}
    {!editing && (() => { const info = companyInfo(bill); return <div className="company-strip"><BillLogo bill={bill} /><div><b>{bill.seller || "Unknown company"}</b><span>{info.about || "Company not identified — showing the default bill logo"}</span></div><span className="source-chip">{info.source === "gmail" ? "From Gmail" : "Scanned bill"}</span></div>; })()}
    {editing ? <div className="form-grid">
      <label>Product<input value={form.product} onChange={e => change("product", e.target.value)} /></label>
      <label>Seller<input value={form.seller} onChange={e => change("seller", e.target.value)} /></label>
      <label>Purchase date<input type="date" value={form.purchase_date} onChange={e => change("purchase_date", e.target.value)} /></label>
      <label>Amount (₹)<input type="number" min="0" step="0.01" value={form.amount} onChange={e => change("amount", e.target.value)} /></label>
      <label>Warranty months<input type="number" min="0" max="120" value={form.warranty_months} onChange={e => change("warranty_months", e.target.value)} /></label>
      <label>Category<input value={form.category} onChange={e => change("category", e.target.value)} /></label>
      <label>Lifetime warranty<input type="checkbox" checked={!!form.warranty_lifetime} onChange={e => change("warranty_lifetime", e.target.checked ? 1 : 0)} /></label>
      <label>Invoice number<input value={form.invoice_no} onChange={e => change("invoice_no", e.target.value)} placeholder="e.g. AF26GJ317452231" /></label>
      <label>Order number<input value={form.order_no} onChange={e => change("order_no", e.target.value)} placeholder="e.g. FN5954801329" /></label>
    </div> : <div className="detail-grid">
      <div><small>PRODUCT</small><b>{bill.product || "—"}</b></div><div><small>AMOUNT</small><b>{money(bill.amount)}</b></div>
      <div><small>SELLER</small><b>{bill.seller || "—"}</b></div><div><small>PURCHASE DATE</small><b>{formatDate(bill.purchase_date)}</b></div>
      <div><small>CATEGORY</small><b>{bill.category || "Other"}</b></div><div><small>RECEIVED ON</small><b>{formatDate(bill.received_date || (bill.created_at || "").slice(0,10))}</b></div>
      <div><small>WARRANTY DURATION</small><b>{bill.warranty_lifetime ? "Lifetime" : bill.warranty_months ? `${bill.warranty_months} months` : "Not specified"}</b></div><div><small>WARRANTY ENDS</small><b>{bill.warranty_lifetime ? "Never (lifetime)" : formatDate(bill.warranty_end)}</b></div>
      <div><small>INVOICE NUMBER</small><b>{bill.invoice_no || "—"}</b></div><div><small>ORDER NUMBER</small><b>{bill.order_no || "—"}</b></div>
    </div>}
    {bill.has_file
      ? <button className="file-link as-button" onClick={viewFile} disabled={fileBusy}><ExternalLink size={16} /> {fileBusy ? "Opening…" : "View original bill"}</button>
      : bill.extracted_text
        ? <button className="file-link as-button" onClick={() => setShowText(v => !v)}><FileText size={16} /> {showText ? "Hide bill text" : "View original bill"}</button>
        : <p className="no-file-note">No original file was saved for this bill — it was imported from the email's text.</p>}
    {showText && !bill.has_file && bill.extracted_text && <div className="bill-sheet"><div className="bill-sheet-head"><FileText size={14} /><span>ORIGINAL BILL · READ FROM THE EMAIL</span></div><pre>{bill.extracted_text}</pre></div>}
    {error && <div className="inline-error">{error}</div>}
    <div className="modal-foot"><button className="danger-button" onClick={() => onDelete(bill)}><Trash2 size={15} /> Delete</button><span className="modal-spacer" />
      {editing && <button className="secondary-button" onClick={() => setEditing(false)}>Cancel</button>}
      {editing ? <button className="primary-button" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save changes"}</button> : <button className="primary-button" onClick={() => setEditing(true)}><Pencil size={15} /> Edit details</button>}
    </div>
  </div></div>;
}

createRoot(document.getElementById("root")).render(<App />);

