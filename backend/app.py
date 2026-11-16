import os
import json
import hmac
import threading
from datetime import datetime, date, timedelta
from flask import Flask, render_template, jsonify, request
from medisync_engine import MediSyncEngine, DRUG_DEFAULTS

# Force Flask to find the exact folder where app.py lives on your computer
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)                                   # project root (parent of backend/)
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend", "templates")         # the HTML pages
DATABASE_DIR = os.path.join(ROOT_DIR, "database")                      # approved stock, datasets

app = Flask(__name__, template_folder=FRONTEND_DIR)
REQUESTS_FILE = os.path.join(BASE_DIR, "requests.json")
INPUT_DIR = os.path.join(BASE_DIR, "input_data")                 # base network data (never rewritten)
APPROVED_DIR = os.path.join(DATABASE_DIR, "approved_stock")          # company-approved stock updates, one file per facility
SUBMISSIONS_FILE = os.path.join(BASE_DIR, "stock_submissions.json")

# Key the company team types on the Stock Approvals page. Set your own before any real use:
#   Windows:  set MEDISYNC_ADMIN_KEY=your-secret      macOS/Linux:  export MEDISYNC_ADMIN_KEY=your-secret
ADMIN_KEY = os.environ.get("MEDISYNC_ADMIN_KEY", "medisync-admin")

# ---------------------------------------------------------------------------
# Clinic stock requests
# Clinics POST here; the network dashboard polls GET and marks them dispatched.
# Kept in memory and saved to requests.json so a server restart doesn't lose them.
# ---------------------------------------------------------------------------
_lock = threading.Lock()


def _load_requests():
    try:
        with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


REQUESTS = _load_requests()


def _save_requests():
    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(REQUESTS, f, indent=2)


def _next_id():
    nums = [int(r["id"].split("-")[1]) for r in REQUESTS if r.get("id", "").startswith("R-")]
    return f"R-{(max(nums) + 1) if nums else 1001}"


# ---------------------------------------------------------------------------
# Pages (the HTML files link to each other by file name, so serve those names)
# ---------------------------------------------------------------------------
@app.route('/')
@app.route('/MediSync.html')
def home():
    return render_template('MediSync.html')


@app.route('/clinic_portal.html')
def clinic_portal():
    return render_template('clinic_portal.html')


@app.route('/landing_page.html')
def landing_page():
    return render_template('landing_page.html')
@app.route('/donor_portal.html')
def donor_portal():
    return render_template('donor_portal.html')


@app.route('/stock_update.html')
def stock_update_page():
    return render_template('stock_update.html')


@app.route('/stock_review.html')
def stock_review_page():
    return render_template('stock_review.html')


# ---------------------------------------------------------------------------
# Data API
# ---------------------------------------------------------------------------
@app.route('/api/data')
def get_data():
    # Base data from input_data, with company-approved stock updates applied on top
    engine = MediSyncEngine(watch_dir=INPUT_DIR, override_dir=APPROVED_DIR)
    engine.scan_and_load()
    payload = engine.export_medi_sync_payload()

    return jsonify(payload)


@app.route('/api/requests', methods=['GET'])
def list_requests():
    """All requests, newest first. Optional ?fac=F2 to filter to one facility."""
    fac = request.args.get('fac')
    with _lock:
        rows = [r for r in REQUESTS if not fac or r["fac"] == fac]
    return jsonify(list(reversed(rows)))


@app.route('/api/requests', methods=['POST'])
def create_request():
    body = request.get_json(silent=True) or {}
    fac = str(body.get("fac", "")).strip()
    drug = str(body.get("drug", "")).strip()
    try:
        qty = int(body.get("qty", 0))
    except (TypeError, ValueError):
        qty = 0
    if not fac or not drug or qty <= 0:
        return jsonify({"error": "fac, drug and a positive qty are required"}), 400

    with _lock:
        req = {
            "id": _next_id(),
            "fac": fac,
            "clinic": str(body.get("clinic", fac)),
            "drug": drug,
            "drug_name": str(body.get("drug_name", drug)),
            "qty": qty,
            "emergency": bool(body.get("emergency", False)),
            "status": "pending",          # pending -> dispatched (or rejected)
            "filled": 0,
            "transfers": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        REQUESTS.append(req)
        _save_requests()
    print(f"[request] {req['id']}: {req['clinic']} needs {qty} x {req['drug_name']}"
          f"{' (EMERGENCY)' if req['emergency'] else ''}")
    return jsonify(req), 201


@app.route('/api/requests/<req_id>', methods=['PATCH', 'POST'])
def update_request(req_id):
    """Dashboard reports progress: {"status": "...", "filled": n, "transfer": "T-240"}."""
    body = request.get_json(silent=True) or {}
    with _lock:
        req = next((r for r in REQUESTS if r["id"] == req_id), None)
        if req is None:
            return jsonify({"error": "not found"}), 404
        # Donor consent: only allowed while the request is still open, so a
        # double click or a second donor can't release the same stock twice.
        if body.get("donor"):
            if req["status"] not in ("pending", "partial"):
                return jsonify({"error": "request is no longer open", "request": req}), 409
            req["donor"] = str(body["donor"])
            req["donor_name"] = str(body.get("donor_name", body["donor"]))
            try:
                req["donor_qty"] = int(body.get("donor_qty", 0))
            except (TypeError, ValueError):
                req["donor_qty"] = 0
            req["donor_transfer"] = str(body.get("transfer", ""))
        if body.get("status") in ("pending", "partial", "dispatched", "rejected"):
            req["status"] = body["status"]
        if "filled" in body:
            req["filled"] = int(body["filled"])
        if body.get("transfer") and body["transfer"] not in req["transfers"]:
            req["transfers"].append(body["transfer"])
        req["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_requests()
    return jsonify(req)


# ---------------------------------------------------------------------------
# Stock updates: hospitals submit through a form, the company approves or rejects.
# Nothing a hospital submits touches the live data until it is approved.
# ---------------------------------------------------------------------------
_stock_lock = threading.Lock()


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)   # atomic: a crash never leaves a half-written file


SUBMISSIONS = _load_json(SUBMISSIONS_FILE, [])


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _is_admin():
    key = request.headers.get("X-Admin-Key", "")
    return hmac.compare_digest(key.encode(), ADMIN_KEY.encode())


def _live_engine():
    engine = MediSyncEngine(watch_dir=INPUT_DIR, override_dir=APPROVED_DIR)
    engine.scan_and_load()
    return engine


def _current_stock(engine, fac):
    """The approved stock lines for one facility, in the same shape the form submits."""
    today = date.today()
    out = {}
    for p in engine.pairs:
        if p["fac"] != fac:
            continue
        d = engine.drugs.get(p["drug"], {})
        out[p["drug"]] = {
            "drug_id": p["drug"],
            "medicine": d.get("name", p["drug"]),
            "unit": d.get("unit", "units"),
            "daily_usage": p["use"],
            "growth_trend": p.get("g", 0.0),
            "batches": [{"batch_id": b["id"], "qty": b["qty"],
                         "expiry_date": (today + timedelta(days=int(b["exp"]))).isoformat()} for b in p["batches"]],
        }
    return dict(sorted(out.items(), key=lambda kv: kv[1]["medicine"].lower()))   # stable A–Z order in the form


def _catalogue(engine):
    cat = {k: {"id": k, "name": v["name"], "unit": v["unit"]} for k, v in DRUG_DEFAULTS.items()}
    for k, v in engine.drugs.items():
        cat[k] = {"id": k, "name": v["name"], "unit": v["unit"]}
    return cat


def _validate_items(raw_items, catalogue):
    """Returns (clean_items, error). Strict on purpose: bad stock data breaks matching for everyone."""
    if not isinstance(raw_items, list) or not raw_items:
        return None, "Add at least one medicine."
    if len(raw_items) > 50:
        return None, "At most 50 medicines per update."
    today = date.today()
    clean, seen = [], set()
    for i, it in enumerate(raw_items, 1):
        if not isinstance(it, dict):
            return None, f"Row {i} is not valid."
        drug = str(it.get("drug_id", "")).strip()
        if drug not in catalogue:
            return None, f"Row {i}: unknown medicine."
        name = catalogue[drug]["name"]
        if drug in seen:
            return None, f"{name} is listed twice. Put all its batches in one row."
        seen.add(drug)
        try:
            use = float(it.get("daily_usage"))
        except (TypeError, ValueError):
            return None, f"{name}: daily use must be a number."
        if not (0 < use <= 100000):
            return None, f"{name}: daily use must be more than 0."
        batches = it.get("batches")
        if not isinstance(batches, list) or not batches:
            return None, f"{name}: add at least one batch."
        if len(batches) > 20:
            return None, f"{name}: at most 20 batches."
        cb, bids = [], set()
        for j, b in enumerate(batches, 1):
            bid = str((b or {}).get("batch_id", "")).strip()
            if not bid or len(bid) > 40:
                return None, f"{name}, batch {j}: batch number is required (max 40 characters)."
            if bid in bids:
                return None, f"{name}: batch {bid} is entered twice."
            bids.add(bid)
            try:
                qty = float(b.get("qty"))
            except (TypeError, ValueError):
                return None, f"{name}, batch {bid}: quantity must be a number."
            if not (0 <= qty <= 10_000_000):
                return None, f"{name}, batch {bid}: quantity must be 0 or more."
            try:
                exp = datetime.strptime(str(b.get("expiry_date", "")), "%Y-%m-%d").date()
            except ValueError:
                return None, f"{name}, batch {bid}: expiry date must be a valid date."
            if exp <= today:
                return None, f"{name}, batch {bid}: already expired. Leave expired stock out."
            cb.append({"batch_id": bid, "qty": round(qty, 2), "expiry_date": exp.isoformat()})
        clean.append({"drug_id": drug, "medicine": name, "daily_usage": round(use, 2), "batches": cb})
    return clean, None


def _next_sub_id():
    nums = [int(s["id"].split("-")[1]) for s in SUBMISSIONS if s.get("id", "").startswith("S-")]
    return f"S-{(max(nums) + 1) if nums else 1001}"


def _public(sub):
    return {k: v for k, v in sub.items() if k != "reviewer"}


@app.route('/api/stock/<fac>', methods=['GET'])
def current_stock(fac):
    """What the form pre-fills: this facility's approved stock plus the medicine list."""
    engine = _live_engine()
    if fac not in engine.facs:
        return jsonify({"error": "unknown facility"}), 404
    return jsonify({
        "facility": engine.facs[fac],
        "items": list(_current_stock(engine, fac).values()),
        "catalogue": sorted(_catalogue(engine).values(), key=lambda d: d["name"]),
    })


@app.route('/api/facilities', methods=['GET'])
def facilities():
    engine = _live_engine()
    return jsonify([{"id": f["id"], "name": f["name"]} for f in engine.facs.values()])


@app.route('/api/stock-submissions', methods=['POST'])
def submit_stock():
    body = request.get_json(silent=True) or {}
    fac = str(body.get("fac", "")).strip()
    submitted_by = str(body.get("submitted_by", "")).strip()
    note = str(body.get("note", "")).strip()
    if not submitted_by or len(submitted_by) > 80:
        return jsonify({"error": "Enter the name of the person submitting (max 80 characters)."}), 400
    if len(note) > 500:
        return jsonify({"error": "Note is too long (max 500 characters)."}), 400

    engine = _live_engine()
    if fac not in engine.facs:
        return jsonify({"error": "Unknown facility."}), 400
    items, err = _validate_items(body.get("items"), _catalogue(engine))
    if err:
        return jsonify({"error": err}), 400

    with _stock_lock:
        open_sub = next((s for s in SUBMISSIONS if s["fac"] == fac and s["status"] == "pending"), None)
        if open_sub:
            return jsonify({"error": f"{open_sub['id']} is still waiting for review. "
                                     f"You can submit again once it is approved or rejected."}), 409
        sub = {
            "id": _next_sub_id(),
            "fac": fac,
            "facility": engine.facs[fac]["name"],
            "submitted_by": submitted_by,
            "note": note,
            "items": items,
            "status": "pending",            # pending -> approved | rejected
            "submitted_at": _now(),
            "reviewed_at": None,
            "review_note": "",
        }
        SUBMISSIONS.append(sub)
        _write_json(SUBMISSIONS_FILE, SUBMISSIONS)
    print(f"[stock] {sub['id']}: {sub['facility']} submitted {len(items)} medicine(s) for review")
    return jsonify(_public(sub)), 201


@app.route('/api/stock-submissions', methods=['GET'])
def list_stock_submissions():
    """A hospital's own submissions and their review status (?fac=F2)."""
    fac = request.args.get('fac')
    if not fac:
        return jsonify({"error": "fac is required"}), 400
    with _stock_lock:
        rows = [_public(s) for s in SUBMISSIONS if s["fac"] == fac]
    return jsonify(list(reversed(rows)))


@app.route('/api/stock-submissions/pending-count', methods=['GET'])
def pending_count():
    with _stock_lock:
        n = sum(1 for s in SUBMISSIONS if s["status"] == "pending")
    return jsonify({"pending": n})


@app.route('/api/admin/stock-submissions', methods=['GET'])
def admin_list_submissions():
    if not _is_admin():
        return jsonify({"error": "Wrong admin key."}), 401
    with _stock_lock:
        rows = [dict(s) for s in SUBMISSIONS]
    engine = _live_engine()
    for s in rows:
        if s["status"] == "pending":   # show what is live right now, so the reviewer sees a true before/after
            cur = _current_stock(engine, s["fac"])
            s["current"] = {it["drug_id"]: cur.get(it["drug_id"]) for it in s["items"]}
    return jsonify(list(reversed(rows)))


@app.route('/api/admin/stock-submissions/<sub_id>/<action>', methods=['POST'])
def admin_review(sub_id, action):
    if not _is_admin():
        return jsonify({"error": "Wrong admin key."}), 401
    if action not in ("approve", "reject"):
        return jsonify({"error": "unknown action"}), 404
    body = request.get_json(silent=True) or {}
    note = str(body.get("note", "")).strip()[:500]
    if action == "reject" and not note:
        return jsonify({"error": "Give the hospital a reason for rejecting."}), 400

    with _stock_lock:
        sub = next((s for s in SUBMISSIONS if s["id"] == sub_id), None)
        if sub is None:
            return jsonify({"error": "not found"}), 404
        if sub["status"] != "pending":
            return jsonify({"error": f"{sub_id} was already {sub['status']}."}), 409

        if action == "approve":
            engine = _live_engine()
            if sub["fac"] not in engine.facs:
                return jsonify({"error": "This facility is no longer in the network data."}), 409
            cur = _current_stock(engine, sub["fac"])
            os.makedirs(APPROVED_DIR, exist_ok=True)
            path = os.path.join(APPROVED_DIR, f"{sub['fac']}.json")
            approved = _load_json(path, {"fac": sub["fac"], "items": [], "history": []})
            by_drug = {it["drug_id"]: it for it in approved.get("items", [])}
            for it in sub["items"]:
                new = dict(it)
                # keep the demand trend the network already knows; the form does not ask for it
                new["growth_trend"] = (cur.get(it["drug_id"]) or {}).get("growth_trend", 0.0)
                by_drug[it["drug_id"]] = new
            approved["fac"] = sub["fac"]
            approved["items"] = list(by_drug.values())
            approved.setdefault("history", []).append(
                {"submission": sub_id, "approved_at": _now(), "medicines": [it["drug_id"] for it in sub["items"]]})
            _write_json(path, approved)

        sub["status"] = "approved" if action == "approve" else "rejected"
        sub["reviewed_at"] = _now()
        sub["review_note"] = note
        _write_json(SUBMISSIONS_FILE, SUBMISSIONS)
    print(f"[stock] {sub_id} {sub['status']}")
    return jsonify(sub)


@app.route('/api/data-version', methods=['GET'])
def data_version():
    """Changes whenever an update is approved, so an open dashboard knows to offer a reload."""
    with _stock_lock:
        done = [s for s in SUBMISSIONS if s["status"] == "approved"]
    last = max(done, key=lambda s: s["reviewed_at"]) if done else None
    return jsonify({"version": f"{len(done)}:{last['id'] if last else ''}",
                    "last": ({"id": last["id"], "facility": last["facility"]} if last else None)})


if __name__ == '__main__':
    print("Starting MediSync Live Server...")
    print("  Network dashboard: http://localhost:5000/")
    print("  Clinic portal:     http://localhost:5000/clinic_portal.html")
    print("  Stock update form: http://localhost:5000/stock_update.html?fac=F2")
    print("  Stock approvals:   http://localhost:5000/stock_review.html")
    if ADMIN_KEY == "medisync-admin":
        print("  WARNING: using the default admin key 'medisync-admin'. Set MEDISYNC_ADMIN_KEY before real use.")
    app.run(debug=True, port=5000)
