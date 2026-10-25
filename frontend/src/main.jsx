import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bell, Check, ChevronDown, ClipboardList, FileText, Home, Search,
  ShieldCheck, Settings, Upload, Camera, X, Clock3, Trash2, ExternalLink,
  Plus, RefreshCw, Pencil, Moon, Sun, Heart, FileCheck2
} from "lucide-react";
import "./styles.css";

const API = (import.meta.env.VITE_API_URL || "https://billproof-starter.onrender.com").replace(/\/+$/, "");
const FILTERS = ["All Documents", "Active Warranties", "Expiring Soon", "No Warranty"];
const NAV = [
  [Home, "Home"], [FileText, "My Bills"], [ShieldCheck, "Warranty Tracker"],
  [Bell, "Reminders"], [Settings, "Settings"]
];

async function apiFetch(path, options = {}) {
  return fetch(`${API}${path}`, { ...options, credentials: "include" });
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

function App() {
  const [bills, setBills] = useState([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All Documents");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedBill, setSelectedBill] = useState(null);
  const [notice, setNotice] = useState("");
  const [activeNav, setActiveNav] = useState("Home");
  const [dark, setDark] = useState(false);
  const [remindersEnabled, setRemindersEnabled] = useState(true);
  const [gmailError, setGmailError] = useState("");
  const [summaryBills, setSummaryBills] = useState([]);

  async function loadBills() {
    setLoading(true);
    try {
      const params = new URLSearchParams({ q: query, status: filter });
      const res = await apiFetch(`/api/bills?${params}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not load bills.");
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
    const timer = setTimeout(loadBills, 200);
    return () => clearTimeout(timer);
  }, [query, filter]);

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

  const counts = useMemo(() => ({
    all: summaryBills.length,
    active: summaryBills.filter(b => b.warranty_status === "Active").length,
    soon: summaryBills.filter(b => b.warranty_status === "Expiring Soon").length,
    expired: summaryBills.filter(b => b.warranty_status === "Expired").length,
    noWarranty: summaryBills.filter(b => b.warranty_status === "No Warranty").length,
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
  const pageTitle = activeNav === "Home" ? "Good evening, Urvi" : activeNav;
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
          <div className="paper-art"><FileText size={36} /><FileCheck2 size={30} /></div>
          <p>Har bill sambhaal ke.<br />Every receipt, remembered.</p>
        </div>
        <div className="sidebar-bottom"><ShieldCheck size={15} /> Your bills, in one safe place</div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="searchbox"><Search size={19} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search bills, products or brands..." />
            {query && <button aria-label="Clear search" onClick={() => setQuery("")}><X size={16} /></button>}
          </div>
          <div className="top-actions">
            <button className="icon-button" title="Warranty reminders" onClick={() => navigate("Reminders")}><Bell size={20} />{remindersEnabled && counts.soon > 0 && <i />}</button>
            <button className="user-chip" onClick={() => navigate("Settings")}><span className="avatar">U</span><span>Hi, Urvi</span><ChevronDown size={15} /></button>
          </div>
        </header>

        <section className="welcome">
          <div><div className="eyebrow">NAMASTE, URVI</div><h1>{pageTitle} <Heart className="heart" size={26} /></h1><p>{pageSubtitle}</p></div>
          <button className="primary-button" onClick={() => setUploadOpen(true)}><Camera size={18} /> Scan / Upload Bill</button>
        </section>

        {notice && <div className="notice"><Check size={17} /><span>{notice}</span><button onClick={() => setNotice("")}><X size={16} /></button></div>}
        {error && <div className="alert-error">{error}<button onClick={() => { setError(""); loadBills(); }}>Try again</button></div>}
        {gmailError && <div className="alert-error">{gmailError}<button onClick={() => setGmailError("")}><X size={16} /></button></div>}

        {activeNav === "Home" && (
          <>
            <section className="summary-grid" aria-label="Your bill summary">
              <div className="summary-card"><small>TOTAL BILLS</small><b>{counts.all}</b><span>saved in your vault</span></div>
              <div className="summary-card"><small>TOTAL SPENT</small><b>{money(counts.totalSpent || null)}</b><span>across saved bills with an amount</span></div>
              <div className="summary-card"><small>ACTIVE WARRANTIES</small><b>{counts.active}</b><span>currently covered</span></div>
              <div className="summary-card"><small>EXPIRING SOON</small><b>{counts.soon}</b><span>ending in the next 30 days</span></div>
            </section>

            <section className="intro-card">
              <div className="intro-icon"><FileCheck2 size={24} /></div>
              <div className="intro-copy"><h2>Your receipts, all together</h2><p>Upload a bill once. Find it whenever you need it.</p></div>
              <button className="soft-button" onClick={() => navigate("My Bills")}>View my bills <ExternalLink size={15} /></button>
              <div className="intro-foot">Keep your purchase records together and review each saved receipt whenever you need it.</div>
            </section>

          </>
        )}

        {activeNav === "Settings" && (
          <section className="panel settings-panel">
            <div className="panel-heading"><div><h2>Preferences</h2><p>Simple controls for your Raseed workspace.</p></div></div>
            <div className="setting-row"><div className="setting-symbol"><Bell size={19} /></div><div className="setting-copy"><b>Warranty reminders</b><span>Show bills whose warranty is expiring soon.</span></div><button className={`toggle ${remindersEnabled ? "on" : ""}`} onClick={() => { setRemindersEnabled(v => !v); setNotice(`Warranty reminders ${remindersEnabled ? "paused" : "enabled"}.`); }} aria-label="Toggle reminders"><span /></button></div>
            <div className="setting-row"><div className="setting-symbol"><Moon size={19} /></div><div className="setting-copy"><b>Appearance</b><span>Switch between light and dark themes.</span></div><button className="soft-button" onClick={() => setDark(v => !v)}>{dark ? <Sun size={16} /> : <Moon size={16} />}{dark ? "Light mode" : "Dark mode"}</button></div>
            <div className="setting-row"><div className="setting-symbol"><ShieldCheck size={19} /></div><div className="setting-copy"><b>File storage</b><span>Uploads are sent to your backend; Cloudinary credentials stay server-side.</span></div></div>
          </section>
        )}

        {activeNav !== "Settings" && (
          <section className="panel bills-panel">
            <div className="panel-heading">
              <div><div className="eyebrow">{activeNav === "Home" ? "YOUR COLLECTION" : "MANAGE YOUR RECEIPTS"}</div><h2>{activeNav === "Home" ? "Recently added bills" : activeNav === "My Bills" ? "My Bills" : activeNav === "Warranty Tracker" ? "Warranty overview" : "Upcoming warranty reminders"}</h2><p>{activeNav === "Warranty Tracker" ? "Warranty details are shown here, not on the home page." : activeNav === "Reminders" ? "Items marked expiring soon are listed first." : "Open a bill to view or update its details."}</p></div>
            </div>
            <div className="list-toolbar">
              <div className="filter-tabs">{FILTERS.map(f => <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>{f}</button>)}</div>
              <button className="sort-button" onClick={loadBills}><RefreshCw size={15} /> Refresh</button>
            </div>
            {loading ? <div className="empty-state"><RefreshCw className="spin" size={24} /><b>Loading your bills…</b></div> :
              visibleBills.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileText size={27} /></div><b>{query ? "No matching bills" : filter !== "All Documents" ? "No bills in this view" : "No bills here yet"}</b><span>{query ? "Try a different search." : filter !== "All Documents" ? `You have ${counts.all} bill${counts.all === 1 ? "" : "s"} saved, but none match “${filter}” right now.` : "Upload your first receipt and it will appear here."}</span>{filter !== "All Documents" || query ? <button className="primary-button" onClick={() => { setFilter("All Documents"); setQuery(""); }}>Show all documents</button> : <button className="primary-button" onClick={() => setUploadOpen(true)}><Plus size={17} /> Add a bill</button>}</div> :
              <div className="bill-list">
                {visibleBills.map(bill => <button className="bill-row" key={bill.id} onClick={() => setSelectedBill(bill)}>
                  <span className="receipt-thumb"><FileText size={22} /><small>₹</small></span>
                  <span className="bill-main"><b>{bill.product || bill.original_filename || "Untitled bill"}</b><small>{bill.seller || bill.category || "Purchase"} · {formatDate(bill.purchase_date)}</small></span>
                  <span className="bill-meta"><small>AMOUNT</small><b>{money(bill.amount)}</b></span>
                  <span className="bill-meta warranty-info"><small>WARRANTY ENDS</small><b>{formatDate(bill.warranty_end)}</b></span>
                  <Status status={bill.warranty_status} />
                  <span className="row-arrow"><ChevronDown size={17} /></span>
                </button>)}
              </div>
            }
          </section>
        )}
        <footer className="page-footer">रसीद <span>•</span> HAR BILL SAMBHAAL KE</footer>
      </main>

      {uploadOpen && <UploadModal onClose={() => setUploadOpen(false)} onUpload={uploadFile} />}
      {selectedBill && <BillModal bill={selectedBill} onClose={() => setSelectedBill(null)} onDelete={removeBill} onSave={updateBill} />}
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
      <label>Category<select value={form.category} onChange={e => change("category", e.target.value)}>{["Other","Electronics","Home & Kitchen","Fashion","Appliances","Furniture","Personal care","Travel"].map(c => <option key={c}>{c}</option>)}</select></label>
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
  const [form, setForm] = useState({ product: bill.product || "", seller: bill.seller || "", purchase_date: bill.purchase_date || "", amount: bill.amount ?? "", warranty_months: bill.warranty_months ?? 0, category: bill.category || "Other" });
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
    {editing ? <div className="form-grid">
      <label>Product<input value={form.product} onChange={e => change("product", e.target.value)} /></label>
      <label>Seller<input value={form.seller} onChange={e => change("seller", e.target.value)} /></label>
      <label>Purchase date<input type="date" value={form.purchase_date} onChange={e => change("purchase_date", e.target.value)} /></label>
      <label>Amount (₹)<input type="number" min="0" step="0.01" value={form.amount} onChange={e => change("amount", e.target.value)} /></label>
      <label>Warranty months<input type="number" min="0" max="120" value={form.warranty_months} onChange={e => change("warranty_months", e.target.value)} /></label>
      <label>Category<input value={form.category} onChange={e => change("category", e.target.value)} /></label>
    </div> : <div className="detail-grid">
      <div><small>PRODUCT</small><b>{bill.product || "—"}</b></div><div><small>AMOUNT</small><b>{money(bill.amount)}</b></div>
      <div><small>SELLER</small><b>{bill.seller || "—"}</b></div><div><small>PURCHASE DATE</small><b>{formatDate(bill.purchase_date)}</b></div>
      <div><small>WARRANTY DURATION</small><b>{bill.warranty_months ? `${bill.warranty_months} months` : "Not specified"}</b></div><div><small>WARRANTY ENDS</small><b>{formatDate(bill.warranty_end)}</b></div>
    </div>}
    {bill.cloudinary_url && <a className="file-link" href={bill.cloudinary_url} target="_blank" rel="noreferrer"><ExternalLink size={16} /> Open original bill</a>}
    {error && <div className="inline-error">{error}</div>}
    <div className="modal-foot"><button className="danger-button" onClick={() => onDelete(bill)}><Trash2 size={15} /> Delete</button><span className="modal-spacer" />
      {editing && <button className="secondary-button" onClick={() => setEditing(false)}>Cancel</button>}
      {editing ? <button className="primary-button" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save changes"}</button> : <button className="primary-button" onClick={() => setEditing(true)}><Pencil size={15} /> Edit details</button>}
    </div>
  </div></div>;
}

createRoot(document.getElementById("root")).render(<App />);

