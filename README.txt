# 🏥 MediSync

**An inter-hospital medicine matchmaker that predicts shortages before they happen and moves surplus stock to where it's needed.**

Hospitals and clinics often run out of critical medicines while a nearby facility sits on surplus that is about to expire. MediSync connects them: it tracks stock across a network, uses machine learning to predict demand surges, and matches shortages with available surplus.

> Built by **The Syntax Surgeons** for PySustain-2026.

---

## ✨ Features

- 🔮 **Surge prediction**: an SVM model (scikit-learn `SVC`, RBF kernel) learns from 14-day consumption history to flag likely shortages early
- 🔗 **Smart matching**: pairs facilities that are short on a medicine with facilities holding surplus
- ⏳ **Expiry awareness**: highlights batches that are expiring soon so they get used, not wasted
- 🏥 **Clinic portal**: clinics raise medicine requests
- 💊 **Donor portal**: facilities offer surplus stock
- ✅ **Stock update and review workflow**: facilities submit updates, and reviewers approve them before they go live
- 📊 **Live dashboard**: network overview, inventory, critical shortages, surplus and predicted shortages in one place

---

## 🧭 How it works

```
 Facilities submit stock ──► Review & approval ──► Approved stock database
                                                          │
 Clinics raise requests ──────────────┐                   ▼
                                      ▼          ┌─────────────────┐
                              ┌──────────────┐   │  MediSync Engine │
                              │ Flask server │◄──│  SVM prediction  │
                              │   (app.py)   │   │  + matching      │
                              └──────┬───────┘   └─────────────────┘
                                     ▼
                        Dashboard & portals (browser)
```

1. Facilities (F1–F4: hospitals and clinics) submit stock updates with batch details.
2. Updates are reviewed and approved, then saved per facility.
3. On every refresh, the engine reads stock, requests and consumption history, predicts surges, and writes a fresh data feed.
4. The dashboard and portals display shortages, surplus and suggested transfers.

---

## 🚀 Quick start

**Requirements:** Python 3.8+

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
cd backend
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

---

## 📁 Project structure

```
MediSync/
├── backend/
│   ├── app.py                    # Flask server (run this)
│   ├── medisync_engine.py        # Engine + SVM surge prediction
│   ├── requests.json             # Clinic requests
│   ├── stock_submissions.json    # Pending stock update submissions
│   └── input_data/
│       └── network_data.json     # All hospitals/clinics (F1–F4) with stock and batches
│
├── frontend/
│   └── templates/
│       ├── landing_page.html     # Entry page
│       ├── MediSync.html         # Main dashboard
│       ├── clinic_portal.html    # Clinics request medicines
│       ├── donor_portal.html     # Facilities offer surplus
│       ├── stock_update.html     # Submit stock updates
│       └── stock_review.html     # Review and approve updates
│
├── database/
│   ├── approved_stock/           # Approved updates, one file per facility (e.g. F2.json)
│   ├── medisync_data.json / .js  # Feed written by the engine on every refresh
│   ├── generate_dataset.py       # Generates the historical dataset
│   └── medisync_historical_data.csv  # Training data (consumption history)
│
└── requirements.txt
```

---

## 🧠 The prediction model

| Item | Detail |
|---|---|
| Algorithm | Support Vector Machine (`sklearn.svm.SVC`) |
| Kernel | RBF |
| Input | 14-day medicine consumption history per facility |
| Output | Surge / shortage prediction per medicine |
| Training data | `database/medisync_historical_data.csv` (create or refresh it with `python database/generate_dataset.py`) |

---

## 🛠️ Tech stack

- **Backend:** Python, Flask
- **Machine learning:** scikit-learn (SVM)
- **Frontend:** HTML, CSS, JavaScript
- **Storage:** JSON files and CSV (no external database needed)

---

## 👥 Team

**The Syntax Surgeons**: add member names here.

---

## 📄 License

Add your license here (for example MIT).
