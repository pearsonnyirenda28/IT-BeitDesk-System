# BeitDesk — Municipality of Beitbridge IT Help Desk

**BeitDesk** is the official IT Help Desk and VoIP Tracking System for the
Municipality of Beitbridge. Built with Django, it provides centralised ticket
management, VoIP call logging, role-based access control, and a full audit trail.

> **Motto:** Ambition · Perseverance · Success  
> **Developer:** Bl@de_zar | Pearson Nyirenda

---

## Table of Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Setup — Windows (Development)](#setup--windows-development)
4. [Setup — Linux / Production](#setup--linux--production)
5. [PostgreSQL Configuration](#postgresql-configuration)
6. [Environment Variables (.env)](#environment-variables-env)
7. [Running the System](#running-the-system)
8. [First-Time Login](#first-time-login)
9. [User Roles & Permissions](#user-roles--permissions)
10. [VoIP / LAN Configuration](#voip--lan-configuration)
11. [Pushing to GitHub](#pushing-to-github)
12. [Troubleshooting](#troubleshooting)

---

## Features

| Module | Capabilities |
|---|---|
| **Tickets** | Create, track, comment, status control; priorities; categories; source tracking |
| **VoIP** | Caller ID logging, call duration, live board, caller directory |
| **Dashboard** | Live stats, charts, active calls, agent workload, audit feed |
| **Reports** | Period reports (7/30/90/365 days), agent performance, priority/source breakdown |
| **Audit Trail** | Immutable log — every login, change, assignment, and call logged with IP |
| **User Management** | Create/deactivate users, reset passwords, promote to admin, role management |

---

## Requirements

| Software | Version | Notes |
|---|---|---|
| Python | 3.11, 3.12, or 3.14 | 3.14 requires Django 5.2+ |
| Django | 5.2+ | See `requirements.txt` |
| PostgreSQL | 16+ (recommended) | SQLite works for dev/testing |
| Git | Any | For version control |

---

## Setup — Windows (Development)

### 1. Clone or extract the project

```cmd
cd C:\Users\YourName\Documents
git clone https://github.com/your-username/beitdesk.git
cd beitdesk
```

Or extract the `.7z` archive, then `cd` into the `nexus_helpdesk` folder.

### 2. Create a virtual environment

```cmd
python -m venv venv
venv\Scripts\activate.bat
```

Your prompt should show `(venv)` when active. If you see an error on
`Activate.ps1` in PowerShell, use `activate.bat` in Command Prompt instead.

### 3. Install dependencies

```cmd
pip install -r requirements.txt --timeout 120
```

If PyPI times out on your network, use the Tsinghua mirror:

```cmd
pip install -r requirements.txt --timeout 120 --index-url https://pypi.tuna.tsinghua.edu.cn/simple/
```

### 4. Create and configure `.env`

Copy the example file and edit it:

```cmd
copy .env.example .env
notepad .env
```

At minimum set `SECRET_KEY` (generate one — see below) and your database details.

**Generate a secret key:**
```cmd
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 5. Create the PostgreSQL database

Open `psql` as the postgres superuser:

```cmd
psql -U postgres
```

Run these SQL commands:

```sql
CREATE DATABASE beitdesk;
CREATE USER beitdesk_user WITH PASSWORD 'StrongPassword123!';
GRANT ALL PRIVILEGES ON DATABASE beitdesk TO beitdesk_user;
ALTER DATABASE beitdesk OWNER TO beitdesk_user;
\q
```

### 6. Run migrations

```cmd
python manage.py migrate
```

### 7. Load demo data (optional)

```cmd
python manage.py seed_data
```

Creates 5 staff users, 8 categories, 15 tickets, 20 VoIP call records.

### 8. Start the server

```cmd
python manage.py runserver
```

Open **http://127.0.0.1:8000** — login: `admin` / `admin123!`

---

## Setup — Linux / Production

### 1. Clone and set up environment

```bash
git clone https://github.com/your-username/beitdesk.git
cd beitdesk
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Install PostgreSQL

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib python3-dev libpq-dev
```

### 3. Create the database

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE beitdesk;
CREATE USER beitdesk_user WITH PASSWORD 'StrongPassword123!';
ALTER ROLE beitdesk_user SET client_encoding TO 'utf8';
ALTER ROLE beitdesk_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE beitdesk_user SET timezone TO 'Africa/Harare';
GRANT ALL PRIVILEGES ON DATABASE beitdesk TO beitdesk_user;
ALTER DATABASE beitdesk OWNER TO beitdesk_user;
\q
```

### 4. Configure `.env` and migrate

```bash
cp .env.example .env
nano .env           # set SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS
python manage.py migrate
python manage.py collectstatic --noinput
```

### 5. Run with Gunicorn

```bash
gunicorn nexus_helpdesk.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --timeout 120 \
    --daemon
```

### 6. Nginx reverse proxy (recommended)

```nginx
server {
    listen 80;
    server_name helpdesk.beitbridge.gov.zw;

    location /static/ {
        alias /path/to/beitdesk/staticfiles/;
    }

    location /media/ {
        alias /path/to/beitdesk/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

---

## PostgreSQL Configuration

The database connection is controlled by environment variables in `.env`.
Update `nexus_helpdesk/settings.py` if you want to hardcode values instead:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'beitdesk',
        'USER': 'beitdesk_user',
        'PASSWORD': 'StrongPassword123!',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### Recommended PostgreSQL settings for production

```sql
-- Run as postgres superuser
ALTER SYSTEM SET max_connections = '100';
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '768MB';
SELECT pg_reload_conf();
```

---

## Environment Variables (.env)

```ini
# ── Django ────────────────────────────────────────
SECRET_KEY=your-generated-secret-key-here
DEBUG=True                        # Set to False in production

# ── Database ──────────────────────────────────────
DB_ENGINE=django.db.backends.postgresql
DB_NAME=beitdesk
DB_USER=beitdesk_user
DB_PASSWORD=StrongPassword123!
DB_HOST=localhost
DB_PORT=5432

# ── Email (optional) ──────────────────────────────
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=helpdesk@beitbridge.gov.zw
EMAIL_HOST_PASSWORD=your-app-password

# ── VoIP / Asterisk AMI (optional) ────────────────
ASTERISK_HOST=192.168.1.10
ASTERISK_PORT=5038
ASTERISK_USERNAME=beitdesk
ASTERISK_PASSWORD=ami-password

# ── Time Zone ─────────────────────────────────────
TIME_ZONE=Africa/Harare
```

---

## Running the System

### Development

```cmd
venv\Scripts\activate.bat      # Windows
source venv/bin/activate        # Linux / macOS

python manage.py runserver
```

Open **http://127.0.0.1:8000**

### Useful management commands

```cmd
# Apply database migrations after updates
python manage.py migrate

# Load demo data (tickets, calls, users)
python manage.py seed_data

# Collect static files (required before production deploy)
python manage.py collectstatic --noinput

# Create a superuser manually
python manage.py createsuperuser

# Generate a new secret key
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## First-Time Login

| Credential | Value |
|---|---|
| URL | http://127.0.0.1:8000 |
| Username | `admin` |
| Password | `admin123!` |

**Change the admin password immediately after first login:**
Go to the top-right profile icon → My Profile → Change Password.

### Create your first IT staff user

1. Go to **Analytics → Manage Users → Add User**
2. Enter username, name, email, and a secure password
3. Tick **Grant IT Staff access**
4. Click **Create User**

---

## User Roles & Permissions

| Permission | Admin | IT Staff | User |
|---|---|---|---|
| View all tickets | ✔ | ✔ | ✘ (own only) |
| Create tickets | ✔ | ✔ | ✔ |
| Change ticket status | ✔ | ✔ | ✘ |
| Assign ticket to agent | ✔ | ✘ | ✘ |
| Take unassigned ticket | ✔ | ✔ | ✘ |
| Log / view VoIP calls | ✔ | ✔ | ✔ |
| View full audit trail | ✔ (all) | ✔ (own) | ✘ |
| Manage users | ✔ (all) | ✔ (regular only) | ✘ |
| Create / promote users | ✔ | ✘ | ✘ |
| Promote to Administrator | ✔ | ✘ | ✘ |
| Manage other staff | ✔ | ✘ | ✘ |
| Edit own profile | ✔ | ✔ | ✔ |
| Access admin panel | ✔ | ✔ | ✘ |

### Promoting a user to Administrator

1. Go to **Analytics → Manage Users**
2. Click the gear icon next to the user
3. In the **Account Status** sidebar card, click **Promote to Administrator**
4. Confirm the prompt — the user now has full system access

---

## VoIP / LAN Configuration

### Manual logging (always available)

Click **Log Call** in the top bar. Enter caller number, name, duration, and notes.
Optionally create a linked ticket from the same form.

### Live calls via Asterisk on LAN

**Step 1 — Enable AMI on the PBX**

Edit `/etc/asterisk/manager.conf`:

```ini
[general]
enabled = yes
port = 5038
bindaddr = 0.0.0.0

[beitdesk]
secret = your_ami_password
read = call,cdr,agent
write = call
permit = 192.168.1.0/255.255.255.0
```

Restart Asterisk: `sudo systemctl restart asterisk`

**Step 2 — Set .env values**

```ini
ASTERISK_HOST=192.168.1.10
ASTERISK_PORT=5038
ASTERISK_USERNAME=beitdesk
ASTERISK_PASSWORD=your_ami_password
```

**Step 3 — Add VoIP Provider in Admin**

Go to `/admin/` → **Voip → Voip providers → Add**.
Set Host to your PBX IP, Port to 5060, Protocol to SIP. Tick Is active.

**Step 4 — Register extensions**

Go to `/admin/` → **Voip → Extensions → Add**.
Map each extension number (e.g. 4001) to the IT staff user who uses it.

**Step 5 — Test**

Make a call to a registered extension. Check **VoIP → Live Board** — it
should appear within seconds. See Admin Panel → How to Use for firewall tips.

---

## Pushing to GitHub

```bash
# First time
git init
git add .
git commit -m "Initial BeitDesk commit"
git branch -M main
git remote add origin https://github.com/your-username/beitdesk.git
git push -u origin main

# Subsequent updates
git add .
git commit -m "describe your changes"
git push
```

### What to exclude from git (already in `.gitignore`)

```
.env            # Never commit secrets
*.pyc
__pycache__/
nexusdesk.db    # SQLite dev database
staticfiles/    # Generated by collectstatic
venv/           # Virtual environment
*.log
```

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `No module named 'django'` | venv not active | Run `venv\Scripts\activate.bat` |
| `CommandError: ALLOWED_HOSTS` | DEBUG=False with no hosts set | Set `ALLOWED_HOSTS = ['*']` for dev |
| `pg_config not found` | PostgreSQL not in PATH | Add `C:\Program Files\PostgreSQL\18\bin` to PATH |
| `psycopg2-binary` build fails | Python 3.14 + old psycopg2 | Use `psycopg2-binary==2.9.12` or newer |
| `OperationalError: no such table` | Migrations not run | Run `python manage.py migrate` |
| CSRF 403 error | Stale session after login | Close all tabs, log in fresh |
| Static files 404 | `collectstatic` not run | Run `python manage.py collectstatic` |
| VoIP calls not appearing on board | PBX not connected or firewall | Check AMI credentials and open port 5038 |
| `VariableDoesNotExist` on ticket pages | Old template files | Re-extract the latest `.7z` replacing all files |

---

## Project Structure

```
nexus_helpdesk/
├── nexus_helpdesk/         # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── helpdesk/               # Ticket management
│   ├── models.py           # Ticket, Category, AuditLog
│   ├── views.py
│   └── management/commands/seed_data.py
├── voip/                   # VoIP call tracking
│   ├── models.py           # VoIPCall, Extension, CallerProfile
│   └── views.py
├── accounts/               # Auth, user management
│   ├── views.py
│   └── urls.py
├── templates/
│   ├── base.html           # Sidebar + topbar (Beitbridge branding)
│   ├── admin/
│   │   ├── base_site.html  # Custom admin branding
│   │   ├── about.html      # About BeitDesk page
│   │   └── howto.html      # How to Use + LAN VoIP setup
│   ├── helpdesk/
│   ├── voip/
│   └── accounts/
├── static/
│   └── img/
│       ├── beitbridge_logo.jpg
│       └── favicon.svg
├── requirements.txt
├── .env.example
└── manage.py
```

---

*BeitDesk v1.0 — Municipality of Beitbridge IT Department*  
*Ambition · Perseverance · Success*
