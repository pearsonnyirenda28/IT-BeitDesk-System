# BeitDesk — IT Help Desk & VoIP Tracking System (Municipality of Beitbridge)

[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-4.2-green)](https://www.djangoproject.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-336791)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**BeitDesk** is the official IT Help Desk and Support Management System for the **Municipality of Beitbridge**. 
It provides a centralised platform for logging, tracking, and resolving IT support tickets, while also monitoring VoIP call activity for the IT department.

---

## Features

| Module | Capabilities |
|--------|--------------|
| **Ticket Management** | Create, assign, prioritise, resolve; SLA tracking; overdue alerts |
| **VoIP Call Tracking** | Caller ID, caller name, call duration (talk/ring/hold), live call board |
| **Caller Directory** | Known caller profiles, call history per number, VIP/blocked flags |
| **Live Dashboard** | Real-time stats, charts, active call counters, agent workload |
| **Reports** | Period reports, agent performance, priority/source breakdown |
| **Audit Trail** | Immutable log of every action — logins, status changes, assignments |
| **Role-based Access** | Superuser / IT Staff / User roles |

---

## Technology Stack

- **Backend**: Django 4.2 + Python 3.10–3.12
- **Database**: PostgreSQL 14+ (recommended) / SQLite (dev)
- **Server**: Gunicorn + Nginx (production)
- **VoIP**: SIP / Asterisk webhook integration
- **Frontend**: Bootstrap 5, Chart.js, FontAwesome

---

## Quick Start (Development)

### 1. Prerequisites
- Python 3.10, 3.11, or 3.12
- pip
- Git

### 2. Clone and set up virtual environment

**Windows (PowerShell):**
```powershell
git clone https://github.com/pearsonnyirenda28/BeitDesk.git
cd BeitDesk

python -m venv venv
.\venv\Scripts\Activate.ps1
# If blocked by execution policy, run once:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser