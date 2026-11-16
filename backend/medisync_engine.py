import os
import glob
import json
import math
from datetime import datetime, date
from typing import Dict, List, Any, Optional
import numpy as np
from sklearn.svm import SVC

_BASE = os.path.abspath(os.path.dirname(__file__))
_DB = os.path.join(os.path.dirname(_BASE), "database")
WATCH_DIR = os.path.join(_BASE, "input_data")
OUTPUT_JSON = os.path.join(_DB, "medisync_data.json")
OUTPUT_JS = os.path.join(_DB, "medisync_data.js")

RESERVE_DAYS = 5
TARGET_DAYS = 10
KEEP_DAYS = 12

DRUG_DEFAULTS = {
    "amox": {"name": "Amoxicillin 500 mg", "unit": "caps", "cls": "Essential", "cold": False, "base": 120, "price": 12.50},
    "ors":  {"name": "ORS sachets", "unit": "sachets", "cls": "Essential", "cold": False, "base": 60, "price": 4.00},
    "para": {"name": "Paracetamol 500 mg", "unit": "tabs", "cls": "Essential", "cold": False, "base": 200, "price": 2.50},
    "insu": {"name": "Insulin glargine", "unit": "vials", "cls": "Life-saving", "cold": True, "base": 6, "price": 350.00},
    "oxy":  {"name": "Oxytocin 10 IU", "unit": "amps", "cls": "Life-saving", "cold": True, "base": 8, "price": 45.00},
    "ceft": {"name": "Ceftriaxone 1 g", "unit": "vials", "cls": "Life-saving", "cold": False, "base": 25, "price": 85.00},
    "salb": {"name": "Salbutamol inhaler", "unit": "inhalers", "cls": "Essential", "cold": False, "base": 5, "price": 120.00},
    "sal":  {"name": "Normal saline 500 ml", "unit": "bags", "cls": "Essential", "cold": False, "base": 40, "price": 35.00},
    "arte": {"name": "Artesunate inj.", "unit": "vials", "cls": "Life-saving", "cold": False, "base": 4, "price": 210.00},
    "met":  {"name": "Metformin 500 mg", "unit": "tabs", "cls": "Chronic", "cold": False, "base": 150, "price": 3.00}
}


def parse_date_to_days(exp_val: Any) -> int:
    """Converts a date string (YYYY-MM-DD) or numeric day count into days from now."""
    if isinstance(exp_val, (int, float)):
        return max(0, int(exp_val))
    
    if isinstance(exp_val, str):
        cleaned = exp_val.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(cleaned, fmt).date()
                delta = (dt - date.today()).days
                return max(0, delta)
            except ValueError:
                continue
    return 30
def predict_surge_risk(history_array):
    """Trains a rapid SVM on synthetic patterns and predicts if the input matches a demand surge."""
    # 1. Generate training data representing 'Normal' vs 'Outbreak' 14-day patterns
    normal_data = [np.random.normal(loc=1.0, scale=0.1, size=14) for _ in range(50)]
    surge_data = [np.concatenate([np.random.normal(1.0, 0.1, 9), np.random.normal(1.8, 0.3, 5)]) for _ in range(50)]
    
    X = np.vstack((normal_data, surge_data))
    y = np.array([0] * 50 + [1] * 50) # 0 = Safe, 1 = Surge Risk
    
    # 2. Train the Support Vector Machine
    svm_model = SVC(kernel='rbf', probability=True)
    svm_model.fit(X, y)
    
    # 3. Format the live hospital data and predict
    input_data = np.array(history_array)
    mean_val = np.mean(input_data) if np.mean(input_data) > 0 else 1
    scaled_input = input_data / mean_val
    
    prediction = svm_model.predict([scaled_input])[0]
    confidence = svm_model.predict_proba([scaled_input])[0][1]
    
    return bool(prediction), round(confidence * 100, 1)



class MediSyncEngine:
    def __init__(self, watch_dir: str = WATCH_DIR, override_dir: Optional[str] = None):
        self.watch_dir = watch_dir
        # Folder of company-approved stock updates (one <facility_id>.json each).
        # Kept OUTSIDE watch_dir so the base files are never rewritten.
        self.override_dir = override_dir
        self.facs: Dict[str, Dict[str, Any]] = {}
        self.drugs: Dict[str, Dict[str, Any]] = {}
        self.pairs: List[Dict[str, Any]] = []
        self.distance_matrix: Dict[str, Dict[str, float]] = {}

    def scan_and_load(self) -> List[str]:
        """Detects and parses all JSON files in the target directory."""
        if not os.path.exists(self.watch_dir):
            os.makedirs(self.watch_dir, exist_ok=True)
            print(f"Created watch directory: {self.watch_dir}")
            return []

        pattern = os.path.join(self.watch_dir, "**", "*.json")
        json_files = glob.glob(pattern, recursive=True)
        print(f"Detected {len(json_files)} JSON file(s). Processing...")

        for file_path in json_files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._ingest_document(data, file_path)
            except Exception as e:
                print(f"Error parsing {file_path}: {e}")

        self._apply_overrides()
        return json_files

    def _apply_overrides(self):
        """Replaces stock lines with company-approved updates submitted through the Stock Update form.
        Only the medicines listed in an update are replaced; the facility's other medicines stay as they are."""
        if not self.override_dir or not os.path.isdir(self.override_dir):
            return
        for file_path in sorted(glob.glob(os.path.join(self.override_dir, "*.json"))):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                fac_id = data.get("fac")
                if fac_id not in self.facs:
                    print(f"Skipping approved update {file_path}: facility {fac_id!r} not in base data")
                    continue
                items = data.get("items", [])
                drug_ids = {item.get("drug_id") for item in items}
                self.pairs = [p for p in self.pairs if not (p["fac"] == fac_id and p["drug"] in drug_ids)]
                for item in items:
                    self._process_inventory_item(fac_id, item)
            except Exception as e:
                print(f"Error applying approved update {file_path}: {e}")

    def _slugify(self, text: str) -> str:
        return "".join(c.lower() for c in text if c.isalnum())[:8]

    def _ingest_document(self, data: Any, file_path: str):
        """Dispatches data depending on whether it is a single hospital or an array."""
        if isinstance(data, list):
            for item in data:
                self._process_hospital_payload(item)
        elif isinstance(data, dict):
            if "hospitals" in data and isinstance(data["hospitals"], list):
                for item in data["hospitals"]:
                    self._process_hospital_payload(item)
            else:
                self._process_hospital_payload(data)

    def _process_hospital_payload(self, record: Dict[str, Any]):
        h_info = record.get("hospital", record)
        h_name = h_info.get("name", "Unknown Facility")
        h_id = h_info.get("id") or ("F_" + self._slugify(h_name))
        
        # Geolocation or fallback grid mapping
        coords = h_info.get("coordinates", {})
        x_coord = coords.get("x", 0.5)
        y_coord = coords.get("y", 0.5)

        if h_id not in self.facs:
            self.facs[h_id] = {
                "id": h_id,
                "name": h_name,
                "short": h_info.get("short", h_name.split()[0]),
                "type": h_info.get("type", "General Hospital"),
                "beds": int(h_info.get("beds", 50)),
                "x": x_coord,
                "y": y_coord
            }

        if "distances_km" in h_info and isinstance(h_info["distances_km"], dict):
            self.distance_matrix[h_id] = h_info["distances_km"]

        # Parse stock and batches
        inv_list = record.get("inventory", record.get("items", record.get("stock", [])))
        for item in inv_list:
            self._process_inventory_item(h_id, item)

    def _process_inventory_item(self, fac_id: str, item: Dict[str, Any]):
        d_name = item.get("medicine", item.get("drug", item.get("name", "Unknown Medicine")))
        d_id = item.get("drug_id", self._slugify(d_name))

        if d_id not in self.drugs:
            default_meta = DRUG_DEFAULTS.get(d_id, {})
            self.drugs[d_id] = {
                "id": d_id,
                "name": d_name,
                "unit": item.get("unit", default_meta.get("unit", "units")),
                "cls": item.get("class", default_meta.get("cls", "Essential")),
                "cold": bool(item.get("cold_chain", default_meta.get("cold", False))),
                "base": float(item.get("base_usage", default_meta.get("base", 20.0))),
                "price": float(default_meta.get("price", 10.0)) # <-- Adds financial tracking
            }
        daily_use = float(item.get("daily_usage", item.get("use", item.get("burn_rate", 10.0))))
        batches_raw = item.get("batches", [])

        batches = []
        if batches_raw:
            for b_idx, b in enumerate(batches_raw):
                b_id = str(b.get("batch_id", b.get("id", f"B_{fac_id}_{d_id}_{b_idx+1}")))
                qty = float(b.get("qty", b.get("stock", b.get("quantity", 0))))
                exp_days = parse_date_to_days(b.get("expiry_date", b.get("expiry", b.get("exp", 90))))
                batches.append({"id": b_id, "qty": round(qty, 2), "exp": exp_days})
        else:
            # Flattened single-stock entries
            tot_qty = float(item.get("stock", item.get("quantity", item.get("on_hand", 0))))
            exp_days = parse_date_to_days(item.get("expiry", item.get("expiry_date", 90)))
            batches.append({"id": f"B_{fac_id}_{d_id}_1", "qty": round(tot_qty, 2), "exp": exp_days})

        growth_trend = float(item.get("growth_trend", item.get("surge_rate", 0.0)))
        
        # Build 14-day consumption history
        history = [round(daily_use * (1 + (i - 13) * growth_trend), 1) for i in range(14)]
        
        # --- AI INTEGRATION ---
        # Run the history array through the Support Vector Machine
        is_surge, risk_confidence = predict_surge_risk(history)

        self.pairs.append({
            "fac": fac_id,
            "drug": d_id,
            "base": daily_use,
            "use": daily_use,
            "g": growth_trend,
            "hist": history,
            "ai_surge_risk": is_surge,
            "ai_confidence": f"{risk_confidence}%",
            "batches": batches,
            "phase": 0
        })

    def export_medi_sync_payload(self) -> Dict[str, Any]:
        """Assembles the payload formatted for MediSync.html frontend."""
        payload = {
            "meta": {
                "generated_at": datetime.now().isoformat(),
                "reserve_days": RESERVE_DAYS,
                "target_days": TARGET_DAYS,
                "keep_days": KEEP_DAYS
            },
            "DRUGS": list(self.drugs.values()),
            "FACS": list(self.facs.values()),
            "pairs": self.pairs,
            "distances": self.distance_matrix
        }

        # Write to JSON
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Generated JSON feed: {OUTPUT_JSON}")

        # Write as standalone JS variable for direct file:// or local inclusion
        with open(OUTPUT_JS, "w", encoding="utf-8") as f:
            f.write(f"window.MEDISYNC_EXTERNAL_DATA = {json.dumps(payload, indent=2)};\n")
        print(f"Generated JS data file: {OUTPUT_JS}")

        return payload


if __name__ == "__main__":
    engine = MediSyncEngine()
    engine.scan_and_load()
    engine.export_medi_sync_payload()