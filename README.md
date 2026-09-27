# BOQ & Store — Road Construction Control App

Django + PostgreSQL backend for the BOQ and Store modules described in
[`docs/requirements.md`](docs/requirements.md). This repo is being built
one step at a time from Section 8 of that spec.

**Current status: Step 1 of 10** — Django project, PostgreSQL, login.

## Setup (local development)

1. Clone the repo and enter it.
2. Create and activate a virtualenv:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Create a PostgreSQL database and user matching what you'll put in
   `.env`, e.g.:
   ```sql
   CREATE USER boq_user WITH PASSWORD 'your-password';
   CREATE DATABASE boq_store_db OWNER boq_user;
   ```
5. Copy `.env.example` to `.env` and fill in real values (a generated
   `SECRET_KEY`, your DB credentials).
6. Run migrations and create an admin user:
   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```
7. Run the dev server:
   ```bash
   python manage.py runserver
   ```
   Visit `http://127.0.0.1:8000/` — you'll be sent to `/login/`. Log in
   with the superuser you just created and you'll land on the home page
   with a logout button. `/admin/` is Django's built-in admin.

## Running tests

```bash
python manage.py test
```

## Project layout

- `config/` — Django project settings, root URLs, WSGI/ASGI entry
  points.
- `accounts/` — custom user model, login/logout views, home page, and
  the tests for all of it.
- `templates/` — project-wide templates (`base.html`,
  `registration/login.html`).
- `docs/requirements.md` — the full requirements spec this build follows.

## Why a custom user model on day one?

Section 2 of the spec gives each user a role per project. Django makes
it very costly to switch `AUTH_USER_MODEL` after other tables already
reference the built-in `User`, so `accounts.User` is created now (as a
plain, unmodified subclass of `AbstractUser`) even though the role
fields themselves are step 2's work, not step 1's.
