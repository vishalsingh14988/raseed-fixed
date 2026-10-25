# Raseed — runnable starter

A minimal, document-first Raseed prototype with a dashboard inspired by the supplied reference:
- React + Vite frontend
- FastAPI backend
- Cloudinary upload/storage for bill files (credentials stay on the backend)
- SQLite database for extracted bill metadata
- OCR with Tesseract when installed, plus basic text extraction/heuristics
- Search, warranty filters, upload/scan entry point, bill list, and warranty status

## Corrections applied (Oct 2026)
- **Branding:** the product is consistently **Raseed** (browser title and API name were still "BillProof").
- **OCR actually fills bill details:**
  - Fixed a bug where selectable PDF text was never tried when Tesseract was unavailable on the host.
  - The browser now runs OCR on uploaded photos with **tesseract.js** and sends the text to the backend, so extraction works even when the server has no Tesseract.
  - `/api/health` now reports OCR status under `ocr`. The backend code can also use **RapidOCR** as an extra fallback if that package is installed (see the note in `backend/requirements.txt` — it is not installed by default because it adds hundreds of MB).
  - Smarter parsing: labelled totals are preferred, otherwise the largest amount; sellers are also detected from the filename; a helpful filename (e.g. "Flipkart Samsung Earbuds Tax Invoice") yields the product ("Samsung Earbuds") instead of a raw numeric filename.
  - For the server-side Tesseract path, deploy the backend as a **Docker** service (see `render.yaml` at the repo root, using `backend/Dockerfile`). A plain Python Render service does not have Tesseract.
- **Search:** now also matches the original filename, and the list filters instantly in the browser while the server responds.
- **Empty states:** filtered views (Warranty Tracker / Reminders / Active Warranties) no longer say "upload your first receipt" when bills exist — they explain the filter and offer "Show all documents".
- **Dashboard summary:** Total bills, Total spent, Active warranties and Expiring soon cards (calculated from the unfiltered bill list).

## Important product limitations (read this)
- **Cloudinary is genuinely used** for bill file uploads. The backend uploads each file to your Cloudinary account and saves the returned secure URL.
- **Gmail auto-import is not enabled by default.** It needs Google OAuth credentials and a consent flow. This starter keeps upload as the working path; Gmail API integration is the next step described below.
- **A website cannot silently read SMS from a phone.** SMS import requires an Android app with explicit SMS permission (and Google Play has strict SMS-permission policies). Do not pretend a browser app can do this.
- The included OCR extraction is a prototype, not production-grade warranty understanding. Always let users review/edit extracted details.

## 1. Prerequisites
Install:
- Node.js 20+
- Python 3.10+
- A Cloudinary account
- (Optional but recommended) Tesseract OCR installed on your computer

## 2. Configure Cloudinary
In your Cloudinary dashboard, copy Cloud Name, API Key, and API Secret.

Create `backend/.env` from `backend/.env.example`:
```env
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret
```
Never put the API secret in the React `.env` file or commit `.env` to Git.

## 3. Start the backend
```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
API docs: http://127.0.0.1:8000/docs

If Cloudinary variables are missing, the backend starts, but upload returns a clear configuration error.

## 4. Start the frontend
Open a second terminal:
```bash
cd frontend
npm install
npm run dev
```
Open the local URL Vite prints (usually http://localhost:5173).

## 5. Test it
1. Click **Scan / Upload Bill**.
2. Choose a JPG, PNG, or PDF invoice.
3. The backend uploads it to Cloudinary, attempts text extraction, and saves a record in SQLite.
4. Review the detected fields and correct them if needed.
5. Search by product/seller and use the All Documents / Active Warranties / Expiring Soon / No Warranty filters.

## What is stored where?
- Original file: Cloudinary (`billproof/bills/...`)
- Bill metadata and Cloudinary URL: local SQLite (`backend/billproof.db`)
- In production, use PostgreSQL instead of SQLite and set Cloudinary access/security policies intentionally.

## Automatic Gmail import (next implementation step)
1. Create a Google Cloud project and enable the Gmail API.
2. Configure the OAuth consent screen and create a **Web application** OAuth client.
3. Add `http://localhost:8000/auth/google/callback` as an authorized redirect URI.
4. Store `credentials.json` securely on the backend; never put client secrets in frontend code.
5. Request the narrowest practical scope and explain the access clearly to users.
6. Query recent messages with invoice-like subjects/senders, download candidate PDF attachments, run the same bill processing pipeline, and deduplicate by Gmail message ID + attachment ID.
7. Provide a visible Connect Gmail / Disconnect Gmail control and let users delete imported data.

Gmail OAuth is intentionally not faked in this starter. A real integration needs credentials, consent, token storage, refresh-token handling, and deletion/disconnect behavior.

## Automatic SMS import (Android only)
Implement as a separate Android app/module (Kotlin/Flutter/React Native) with explicit permission and a clear opt-in. Filter locally first and only send likely bill/transaction messages to the backend. The web dashboard can display the resulting records, but it cannot read SMS itself.

## Before a real launch
- Add authentication and per-user data isolation.
- Use PostgreSQL and migrations.
- Add Gmail OAuth, secure token storage, and source disconnection.
- Add user review/edit screens for extracted fields.
- Add deduplication, audit logs, retention/deletion controls, and tests.
- Configure Cloudinary folders, upload limits, allowed formats, and access policy.
- Use a durable scheduler (e.g. Celery/Redis or managed jobs) for warranty reminders; don't rely on an in-process timer.
- Add HTTPS and production secrets management.
