# TikTok Автопілот (Render)

Web (FastAPI + адмін-панель) + Worker (Playwright/Chromium керує tiktok.com **без офіційного API**) + PostgreSQL.

## ⚠️ Важливо знати
- Офіційного API для особистих повідомлень TikTok не існує, тому воркер керує сайтом як браузер. Це **суперечить правилам TikTok** — акаунт можуть обмежити.
- Селектори в `app/worker.py` (словник `SEL`) і розпізнавання 🔥 написані без доступу до TikTok — **їх треба звірити** з реальною версткою (F12 → Inspect) і поправити. Без цього надсилання не запрацює.
- Капчі/2FA обійти не можна: тому вхід через cookies вашої вже авторизованої сесії.
- Worker запускає Chromium: потрібно ≥1 ГБ RAM (Render Standard). Free-тарифи не підходять.
- **Vercel не підтримується**: serverless не запускає тривалий браузер. Лишається лише Render.

## 1. GitHub
```
git init && git add . && git commit -m "init"
git branch -M main && git remote add origin https://github.com/ВАШ_ЛОГІН/tiktok-autopilot.git && git push -u origin main
```
## 2. Render
1. dashboard.render.com → New → **Blueprint** → оберіть репозиторій → Apply.
2. Створяться `tt-web`, `tt-worker`, `tt-db`. У `tt-web` задайте **ADMIN_PASSWORD**.
3. Health check: `/health`. Build — Docker (`Dockerfile`), Start web — `uvicorn app.web:app`, Start worker — `python -m app.worker`.
4. Посилання на сайт: `tt-web` → вгорі `https://tt-web-xxxx.onrender.com`.

## 3. Робота
Відкрийте URL → пароль → Налаштування → підключіть акаунт → Автоматизація → Головна → **Запустити**.
Літера щодня після 00:01 (Europe/Kyiv). Пропущене завдання виконується, лише якщо запізнення < 6 год; дублі блокує таблиця `sent` та атомарне закріплення дня.

## 4. Як отримати cookies
Увійдіть у tiktok.com у браузері → розширення Cookie-Editor → Export → JSON → вставте в Налаштування. Cookies зберігаються в БД зашифрованими (ключ — SECRET_KEY). Коли сесія спливе, статус стане «Сесія недійсна» — повторіть.
