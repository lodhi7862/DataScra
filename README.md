# JewelLeads

JewelLeads is a local web app for finding jewellery shops, saving their contact details, and exporting them to Excel. It runs entirely on your computer.

- Shop search uses the free OpenStreetMap services [Nominatim](https://nominatim.openstreetmap.org) and [Overpass](https://overpass-api.de). There is no API key and nothing to pay.
- Facebook is not scraped. A button opens Facebook's own page search, and you can paste text you copied yourself.
- Leads are stored in `leads.db` (SQLite) in this folder when `DATABASE_URL` is not set. The tables are created the first time the app starts. Set `DATABASE_URL` to use PostgreSQL instead, which is what the hosted app on Render uses.

## Setup on Windows

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/). During setup, tick **Add python.exe to PATH**.

2. Open PowerShell in this folder (`g:\Lodhi\ScrapProject`) and create a virtual environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

If `py` is not recognised, use `python` instead of `py -3`.

3. Start the app:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

After the virtual environment is activated (`.\.venv\Scripts\Activate.ps1`), the same command is:

```powershell
uvicorn app.main:app --reload
```

If PowerShell blocks the activate script, the `python -m uvicorn` form above does not need activation.

4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The page loads Inter, Tailwind CSS, and Chart.js from public CDNs. Those are not API keys. The app still works for data entry if a CDN is blocked, but the layout looks best with them available. Searching for shops needs an internet connection.

## Quick test

1. Open **Find Shops**.
2. City: `Rawalpindi`. Leave the **Jewellers** chip selected.
3. Click **Search**. The first lookup can take up to a minute. The same search is cached for 24 hours.
4. Click **Save to My Leads** on a shop, then open **My Leads**.
5. Click **Download Excel**.

Run the unit tests with:

```powershell
.\.venv\Scripts\python -m pytest
```

## What the search includes

Inside the city's OpenStreetMap boundary (or a bounding box when Nominatim only has a point for that city):

- `shop=jewelry` and `shop=jewellery`
- `craft=goldsmith` and `craft=jeweller`
- shops and craft places whose name matches the keyword (chips or text you type)

An optional area such as `Saddar` keeps only results whose address, suburb, or city text contains that word. Shops with no address tags will not match an area filter.

Please keep searches occasional. Nominatim is limited to one request per second, and results are cached so a repeat does not call it again.

## Lead statuses

New, Contacted, Interested, Demo Scheduled, Demo Done, Customer, Not Interested, Wrong Number.

Phone numbers are stored in international form. With the default country code `92`, `0300-1234567` becomes `+923001234567` and is treated as WhatsApp-capable. Landlines such as `051-2345678` are kept but not marked as WhatsApp. Change the country code in **Settings**.

Duplicates are merged by phone number first, then by a similar name within about 200 metres. A lead you already edited keeps its status, notes, and follow-up date; empty contact fields are filled in.

## Data and attribution

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via Nominatim and the Overpass API. Use it under the Open Database License.

## Sign-in

Leave `APP_PASSWORD` unset while you are working on this computer and the app opens directly. Set `APP_PASSWORD` and `SECRET_KEY` when the app is reachable by anyone else. Copy `.env.example` to `.env` and fill those in, then start the app from a shell that has the variables:

```powershell
$env:APP_PASSWORD = "choose-a-long-password"
$env:SECRET_KEY = "generate-a-random-string"
.\.venv\Scripts\python -m app
```

Open [http://127.0.0.1:8000/login](http://127.0.0.1:8000/login), sign in, and use **Log out** in the top bar when you are done. The port comes from `PORT` when that variable is set (Render sets it). Locally it stays on 8000.

## Deploy on Render (free) with Neon

The hosted app uses a free Neon Postgres database and a free Render web service. You still do not need a paid maps API.

### 1. Put the code on GitHub

1. Create a new empty repository on GitHub (private is a good choice, because this app holds phone numbers).
2. In this folder:

```powershell
git init
git add .
git commit -m "Prepare JewelLeads for Render"
git branch -M main
git remote add origin https://github.com/YOUR_USER/jewelleads.git
git push -u origin main
```

`.env` and `leads.db` are listed in `.gitignore`. Do not commit them.

### 2. Create the Neon database

1. Sign up at [neon.tech](https://neon.tech) and create a project. The free plan is enough.
2. Open the connection string and copy the **pooled** URI. It looks like `postgresql://user:password@host/neondb?sslmode=require`.
3. If `sslmode=require` is missing, add it. The app also adds it when the URL does not already contain `sslmode`.

### 3. Create the Render service

1. Sign up at [render.com](https://render.com) and choose **New +** → **Blueprint** (or **Web Service**).
2. Connect the GitHub repository. Render reads `render.yaml` and creates a free Python web service.
3. When Render asks for environment values, set:
   - `DATABASE_URL`: the Neon connection string from step 2
   - `APP_PASSWORD`: a long password you will type on the login page
   - `SECRET_KEY`: leave the generated value Render creates
4. Deploy. The start command is `python -m app`, which listens on Render's `PORT`. Render calls `/health` to check that the process is up.
5. The first start creates the tables in Neon. Open the Render URL, sign in with `APP_PASSWORD`, then search a city and save a lead.

The free Render service sleeps after a period with no visits. The first request after that can take half a minute while it wakes up.

Local development does not need Neon. With `DATABASE_URL` unset, the same code keeps using `leads.db`.
