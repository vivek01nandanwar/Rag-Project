# 🏪 Bablu ki dukaan — General Store manager (Chunk 1)

React (Vite) + Django REST. Groq AI agent comes in the next chunk.

## Run the backend
```bash
cd backend
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations store
python manage.py migrate
python manage.py seed_demo          # demo products, customers, suppliers
python manage.py runserver          # http://localhost:8000/api/
```

## Run the frontend
```bash
cd frontend
npm install
npm run dev                         # http://localhost:5173
```

## Tables
| Table | Purpose |
|---|---|
| Item | product details + **available stock** (`current_stock`) |
| StockMovement | log of every stock change |
| Purchase / PurchaseItem | stock bought from suppliers. Extra columns: `pending_to_supplier`, `refund_due_from_supplier` |
| Sale / SaleItem | goods sold. Columns: `pending_from_customer`, `refund_due_to_customer` |
| Payment | later settlement against a sale or purchase |
| Customer, Supplier, Category | people and grouping |

All money/stock rules are in `backend/store/services.py` (the AI agent will reuse them as tools).

# Update: login + Munshi ji (AI assistant)

## 1. Copy the files
Copy the `backend` and `frontend` folders of this zip over your project (say "merge / replace" when asked).
Your `db.sqlite3`, `venv`, `node_modules` and `store/migrations` stay untouched, so no data is lost.

Files REPLACED: backend/config/settings.py, backend/config/urls.py, backend/store/urls.py,
                backend/requirements.txt, frontend/src/App.jsx, frontend/src/api.js, frontend/src/main.jsx
Files NEW:      backend/store/auth.py, backend/assistant/*, backend/.env.example,
                frontend/src/assistant.css, frontend/src/pages/{Login,Assistant,Extras}.jsx

## 2. Backend setup (venv active)
    pip install -r requirements.txt
    copy .env.example .env          (Windows)   /   cp .env.example .env   (Mac/Linux)
    -> open .env, set DJANGO_SECRET_KEY and GROQ_API_KEY
    python manage.py makemigrations assistant
    python manage.py migrate
    python manage.py createsuperuser      <- this is your shop login
    python manage.py runserver

## 3. Frontend
    npm run dev      (no new npm packages needed)


# Update: Returns + Total Profit + Stock edit/category

1. Copy the `backend` and `frontend` folders of this zip over your project (merge / replace).
2. Backend (venv active):
       python manage.py makemigrations store
       python manage.py migrate
       python manage.py backfill_cost_price     <- run ONCE (gives old sales a buying price so profit works)
       python manage.py runserver
3. Frontend: npm run dev   (no new packages)
