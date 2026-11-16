MediSync - complete project (no Exasol)

MediSync/
  backend/
    app.py                   Flask server (run this)
    medisync_engine.py       Engine + SVM (scikit-learn SVC, RBF) surge prediction
    input_data/network_data.json   All hospitals/clinics (F1-F4) with stock and batches
    requests.json            Clinic requests
    stock_submissions.json   Stock update submissions
  frontend/templates/        MediSync.html, landing_page.html, clinic_portal.html,
                             donor_portal.html, stock_update.html, stock_review.html
  database/
    approved_stock/F2.json   Approved stock updates (one file per facility)
    medisync_data.json/.js   Feed written by the engine on every refresh
    generate_dataset.py      Makes medisync_historical_data.csv
    medisync_historical_data.csv
  requirements.txt

Run:
  pip install -r requirements.txt
  cd backend
  python app.py
  open http://127.0.0.1:5000
