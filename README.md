# ពងទាប្រៃបេកខេរី — Bakery Tracker

A small, self-hosted web app to track ingredient stock, customer orders, and
income for ពងទាប្រៃបេកខេរី. Built for two users (no complicated
accounts/roles), works fully in **Khmer** (with an English toggle for anyone
else maintaining it), and is mobile-first — most days this will be run from
a phone at the counter, not a desktop.

## What's new in this update

Based on feedback from actually using the app day-to-day:

- **Simpler mobile navigation.** A fixed bottom bar (Dashboard, Materials,
  Orders, Products, Reports) replaces the old scrolling row of top tabs on
  phones — 5 big thumb-friendly buttons instead of small tabs you had to
  scroll sideways to find.
- **Fewer taps to log an order.** The Orders page now shows your products as
  tappable cards — tap once to add one, tap again to add more. A running
  order summary at the bottom lets you adjust quantities with +/− buttons.
  Customer name/phone/address are tucked behind an optional "Add customer
  info" toggle since most orders are walk-ins and don't need them.
- **One consistent font everywhere** (Kantumruy Pro), in both Khmer and
  English, instead of three different typefaces that didn't match each
  other from one part of the screen to the next.
- **Clearer charts.** Bar labels are now horizontal instead of sideways
  text, bars are sized for phone screens, and numbers stay above each bar.
- **Correct time.** Every timestamp and "today" calculation is now pinned to
  **Asia/Phnom_Penh**, not the server's own clock (Render's servers run on
  UTC, which is 7 hours off — this is what caused times to look wrong).
- **Login actually stays logged in.** There's a "Keep me signed in" checkbox
  (checked by default) on the login page — sessions now last 30 days
  instead of disappearing the moment the browser or PWA is closed.
- **Payment status.** Every order starts as **Pending**. Tap the badge next
  to an order (on the Orders list or on its invoice) to mark it **Paid** —
  no form, just a tap, and it flips back if tapped again by mistake.

## What it does

- **Materials** — track ingredients: name, a unit picked from a dropdown
  (kg, g, l, ml, pcs, pack, box), stock quantity, and cost per unit in
  either dollars or Riel (enter one — the other is calculated live as you
  type, so a typo is obvious right away instead of showing up later as a
  huge wrong total). Materials are flagged "Out of stock" once quantity
  hits zero. Stock is deducted automatically whenever a material is used in
  an order. Each material has three simple actions: **Restock** (optionally
  entering the total you paid, so cost-per-unit recalculates as a weighted
  average), **Edit** (name, unit, cost, plus where you buy it and the
  seller's contact), and **History** (a log of every restock, adjustment,
  and order that touched this material's stock).
- **Products** — a simple list of what you're producing (e.g. "នំបុ័ងសូកូឡា")
  with a price in dollars or Riel, and a *recipe* expressed the way a baker
  thinks about it: "1kg of flour makes 20 breads" — you enter the batch
  amount and how many it yields, and the app works out the exact amount
  used per bread automatically. Each product also shows its cost per unit
  and profit margin.
- **Orders** — tap products to build an order quickly (one or several at
  once), with optional customer name, phone, address, and note tucked
  behind a toggle. Stock for every ingredient involved is deducted in real
  time based on each product's recipe, and the order won't save if there
  isn't enough stock — it tells you exactly what's short. Every order
  starts **Pending**; tap its badge to mark it **Paid** once the customer
  pays. Orders can be **edited** afterward — the app correctly reverses the
  original stock deduction and reapplies the new one, so stock always stays
  accurate. The Orders page also shows a running total of recent orders.
- **Invoices (as an image)** — every order has an invoice with your logo,
  styled like a standard printed invoice book (buyer info, itemized goods
  table, total in $ and ៛, a payment QR code if you've uploaded one, buyer/
  seller signature lines). It's rendered as an actual image you can
  download, share straight to a customer's Telegram, Messenger, WhatsApp —
  any app on your phone — print, or send automatically to your own shop's
  Telegram. See "Sending invoices to customers" below.
- **Settings** — upload your bank's payment QR code (e.g. ABA KHQR) once,
  and it appears on every invoice so customers can scan to pay directly.
  Tucked behind the ⚙ icon in the top right, since it's a set-once page.
- **Dashboard** — today's income, this week's income, out-of-stock
  materials, recent orders (with payment status), and a filterable orders
  chart (7/30/90 days).
- **Reports** — income by day (chart), income by product (chart and table),
  material cost used, other expenses (in $ or ៛), and net profit over a
  chosen period.
- **Khmer / English** — the whole interface is in Khmer by default (in the
  Kantumruy Pro font, used consistently everywhere), with a ខ្មែរ / EN
  switch in the top right of every page.
- **Mobile-first** — a fixed bottom navigation bar on phones, tap-to-add
  order entry, and a layout that's designed to be used one-handed at the
  counter, not just adapted from a desktop layout.

## Two ways to run this app

**Option A — Supabase + Render (cloud, free, works on mobile from anywhere).**
This is now the recommended path: your data lives in a free Supabase
Postgres database (which genuinely persists — this was the missing piece in
free hosting before), the app itself runs on Render's free tier, and you
and your sister each log in with your own account. No hardware to keep on.

**Option B — Self-hosted with Docker.** Runs entirely on your own
hardware (a Raspberry Pi, an old laptop) with a bundled Postgres database.
Genuinely free forever with no recurring cost, and your data never leaves
your own disk. Still uses Supabase, but only for the login system — the
data itself stays local. Good if you'd rather not have your business data
on someone else's server at all.

Both use the same codebase — you're just choosing where the database and
the app itself live.

---

## Option A: Deploy to Supabase + Render

### 1. Create your Supabase project (free)

1. Go to [supabase.com](https://supabase.com) and sign up (free, no credit
   card needed for the free tier).
2. Create a new project. Pick a strong database password and save it
   somewhere — you'll need it in a moment.
3. Once it's ready, open **SQL Editor** (left sidebar) → **New query**,
   paste in the entire contents of this project's `schema.sql` file, and
   run it. This creates all the tables.
4. Go to **Project Settings > Database > Connection string > URI**. Copy
   it — this is your `DATABASE_URL`. Replace `[YOUR-PASSWORD]` in it with
   the database password from step 2.
5. Go to **Project Settings > API**. Copy the **Project URL** (this is
   `SUPABASE_URL`) and the **anon / public key** (this is
   `SUPABASE_ANON_KEY`).

### 2. Create your two accounts

1. In Supabase, go to **Authentication > Users > Add user**.
2. Create one for yourself and one for your sister — email + password each.
   Turn on "Auto Confirm User" so you don't need to click an email link.
3. There's no public sign-up page in the app on purpose — only accounts
   created here can log in. Add more later the same way if needed.

### 3. Deploy the app to Render (free)

Render deploys from a GitHub repository, so first put this code on GitHub:

1. Create a free [GitHub](https://github.com) account if you don't have one.
2. Create a new repository, and upload this whole folder to it (GitHub's
   web uploader works fine — drag the files in, no command line needed).
3. Go to [render.com](https://render.com), sign up free, and choose
   **New > Web Service**, then connect the GitHub repo you just created.
4. Set:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
5. Under **Environment**, add these variables:
   ```
   DATABASE_URL       = (from Supabase step 1.4)
   SUPABASE_URL        = (from Supabase step 1.5)
   SUPABASE_ANON_KEY   = (from Supabase step 1.5)
   SECRET_KEY          = any long random string
   SHOP_NAME            = ពងទាប្រៃបេកខេរី
   KHR_PER_USD          = 4100
   ```
   (Telegram and SHOP_PHONE are optional — see further down.)
6. Click **Create Web Service**. First deploy takes a few minutes.

You'll get a free address like `https://your-app-name.onrender.com` —
that's your bakery app, reachable from anywhere, including your phone.

**About the free tier's sleep behavior:** Render's free web services fall
asleep after ~15 minutes with no visits, and take ~30–50 seconds to wake up
on the next visit. This doesn't affect your data (that's safely in
Supabase) — it just means the very first load after a quiet spell is slow.
For a bakery checked a few times a day, this is a minor, honest trade-off
for $0/month.

### 4. Custom domain (optional)

A genuinely free custom domain isn't really available anymore (the old
free-TLD services are unreliable or shut down). Two honest options:

- **Skip it** — your free `onrender.com` address works fine and costs
  nothing.
- **Buy one** — a domain from a reputable registrar (Namecheap, Porkbun,
  Cloudflare) runs about $10–15/year. Once you have it, Render's own
  **Settings > Custom Domains** (free) lets you point it at your app — you
  don't need Vercel or Netlify for this part; those host different kinds
  of apps, not this one.

---

## Option B: Self-hosted with Docker

1. Install Docker on the machine you'll run this on (a Raspberry Pi, an old
   laptop, or a small VPS all work fine).
2. Copy this whole folder onto that machine.
3. You still need Supabase for login — follow steps 1 and 2 under Option A
   above (create the project, run `schema.sql` isn't needed here since
   Docker creates its own tables automatically, but you do still need the
   Auth part: create your two user accounts, and grab `SUPABASE_URL` and
   `SUPABASE_ANON_KEY`).
4. Open `docker-compose.yml` and fill in `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
   and a random `SECRET_KEY`. Leave `DATABASE_URL` as-is — it already points
   at the Postgres container bundled in this file.
5. From inside the folder, run:
   ```bash
   docker compose up -d --build
   ```
6. Open `http://<the-machine's-address>:5000` in a browser and log in.

Your data lives in the `db` container's own Docker volume, which persists
across restarts and reboots. Back it up with `./backup.sh` (see below).

## Accessing it from your phone

It's already a **web app** — no separate build needed. Open your Render
URL (or your self-hosted address) in your phone's browser and use the menu
to **"Add to Home Screen"**. It'll get its own icon and open full-screen,
like an installed app, with the bottom navigation bar for getting around.

If self-hosting (Option B) and you want to reach it from outside your home
network, [Tailscale](https://tailscale.com/) (free for personal use) is the
simplest way — install it on the host machine and your phone, log both into
the same account, then open `http://<tailscale-machine-name>:5000`.

## Sending invoices to customers

Every order has an **Invoice** link (on the Orders page, next to each order).
Opening it gives a clean invoice — styled like a standard printed invoice
book, with buyer info, an itemized table, and a total — plus a **Paid /
Pending** badge at the top you can tap to update.

The invoice is rendered as an actual **image** (captured from what's on
screen, so Khmer text always looks right), with four buttons:

- **Share** — opens your phone's normal share sheet, the same one you'd get
  sharing a photo, and shares the invoice **as an image** where the browser
  supports it (falls back to text if not). Pick Telegram, Messenger,
  WhatsApp, Viber, SMS — whatever the customer uses. This works immediately,
  on any phone, with **no setup at all**.
- **Download image** — saves the invoice as a PNG file, which you can then
  attach anywhere manually (useful on a desktop browser, where there's no
  share sheet — like Chrome on Windows).
- **Copy text** — copies a plain-text version of the invoice.
- **Print** — for a paper copy.

This covers "send an invoice via Telegram or Messenger" for the vast
majority of cases — just tap Share and pick the app, or on a Windows PC,
tap Download image and drag the file into whatever app you're using.

### Optional: Telegram Bot (automatic image sending)

If you want the app to send the invoice **image** straight to your own
Telegram automatically — no download/share step needed — you can set up a
free Telegram bot. This is optional; the app works fully without it, since
the buttons above (especially Download image on Windows) don't need it.

1. In Telegram, message **[@BotFather](https://t.me/BotFather)** and send
   `/newbot`. Follow the prompts. BotFather will give you a **bot token**
   like `123456789:AAExampleTokenHere`.
2. Message your new bot directly (search its username, press Start), or add
   it to a group/channel you use for records.
3. Get the **chat ID**: after messaging the bot, open this URL in a browser
   (with your token): `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   — you'll see a `"chat":{"id":...}` value; that number is the chat ID.
4. Open `docker-compose.yml` and fill in:
   ```yaml
   - SHOP_NAME=ពងទាប្រៃបេកខេរី
   - SHOP_PHONE=012 345 678
   - TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenHere
   - TELEGRAM_CHAT_ID=987654321
   ```
5. Restart: `docker compose up -d --build`. A "Send image to shop Telegram"
   button will now appear on every invoice — tapping it sends the invoice
   image directly, no download step needed.

## Adding a payment QR code (ABA KHQR, etc.)

To let customers scan and pay directly from the invoice:

1. Open your banking app (e.g. ABA Mobile) and find the screen that shows
   your personal QR code for receiving payments.
2. Take a screenshot of it (or use the app's "Download" / "Share" option to
   save the QR image).
3. In the bakery app, go to **Settings** (top right, next to Reports).
4. Choose the image file and click **Upload QR**.

That's it — the QR code now appears on every invoice, with a "Scan to pay"
label underneath. You can replace or delete it any time from the same
Settings page. It's stored in the database itself, so it's included in any
Supabase/Postgres backup.

## Changing the logo

The logo shown in the nav bar, login page, and every invoice is a single
file: `static/img/logo.png`. To use a different image, just replace that
file with your own PNG (square images work best) and restart the app.

## Backing up your data

**If you're using Supabase** (Option A, or Option B's login system): Supabase
backs up your database automatically. For extra peace of mind, you can also
export a copy yourself anytime from the Supabase dashboard: **Database >
Backups**, or **SQL Editor** and run a manual export.

**If you're self-hosting Postgres** (Option B's `db` container): use the
included backup script, which runs `pg_dump` inside the container:

```bash
chmod +x backup.sh
crontab -e
# add this line to back up every night at 10pm:
0 22 * * * /full/path/to/bakery-app/backup.sh
```

This copies a timestamped `.sql` snapshot into `backups/` and keeps the
most recent 30. To restore one: `docker exec -i bakery-db psql -U bakery bakery < backups/bakery-2026-01-01_2200.sql`.

## Changing settings later

**Render:** edit environment variables under your service's **Environment**
tab, then **Manual Deploy > Deploy latest commit** (or it redeploys
automatically on the next push if connected to GitHub).

**Docker self-host:** edit any value in `docker-compose.yml` and restart:

```bash
docker compose up -d --build
```

## Project structure

```
bakery-app/
├── app.py                 # Flask app: routes + business logic
├── schema.sql              # Database structure (materials, products, orders...)
├── templates/               # HTML pages
├── static/css/main.css       # Styling
├── static/img/logo.png        # Your logo (nav, login, invoices)
├── backups/                    # Where backup.sh puts snapshots (self-host only)
├── backup.sh                     # Backup script (self-host only)
├── requirements.txt
├── Dockerfile
└── docker-compose.yml             # Self-host only; not used for Render
```

## Notes on the numbers

- **Recipes use a "yield" model.** On the Products page, for each material
  you enter an amount and how many units it produces — e.g. batch = 1kg,
  yield = 20 breads. The app stores the per-bread amount (0.05kg here) and
  uses that for every order automatically. Update this any time your sister
  changes a recipe; it recalculates immediately.
- **Cost/unit and margin** shown on each product are calculated live from its
  recipe and the current cost-per-unit of each material.
- **Material cost (used)** on the Reports page is calculated from what was
  actually deducted for orders (via each product's recipe) — not from
  restocking. It's an estimate of ingredient cost per order, not total
  spend on ingredients.
- **Net profit** = income − material cost (used) − other logged expenses.
- **Payment status**: every order starts **Pending**. Tap its badge (on the
  Orders list, dashboard, or the invoice page) to flip it to **Paid** — tap
  again if you tapped by mistake. This is independent of stock/income
  numbers; income is recorded when the order is logged either way, so
  Reports reflect what was sold, not what's been collected yet.
- An order can include several different products at once (e.g. 5 chocolate
  breads + 10 plain breads in one order) — stock for shared ingredients like
  flour is deducted correctly across all lines.
- Deleting an order removes the income record but does **not** put stock back
  automatically (in case the stock was used for something else in the
  meantime) — restock manually on the Materials page if needed.
- **Editing an order**: click "Edit" next to any order to change products,
  quantities, or customer info. Behind the scenes, the app first reverses
  the stock deduction from the original order, then re-validates and
  re-deducts stock for the new version — so if the edit would run you out
  of an ingredient, it's blocked with the same shortage message as a new
  order, and nothing changes until it's valid.
- **Cost in $ or ៛ everywhere**: on Materials, Products (selling price), and
  Reports (expenses), enter a number in whichever currency you know —
  dollars or Riel — and the app fills in the other automatically using the
  exchange rate set in `KHR_PER_USD` (`docker-compose.yml`). All internal
  calculations use dollars; Riel is shown for convenience. As you type, a
  small preview shows the converted amount immediately — if you meant to
  type ៛2,000 but accidentally typed it into the $ field, you'll see
  "≈ ៛8,200,000" right away instead of only noticing after saving.
- **Restocking with a weighted-average cost**: when you restock a material,
  you can optionally fill in the total amount you paid for that batch. The
  app then recalculates cost-per-unit as a weighted average of your old
  stock's cost and the new batch's cost — e.g. restocking 50kg of flour at
  a new price blends with whatever flour you already had, rather than just
  overwriting the old cost outright.
- **Dashboard chart**: shows order totals per day for the last 7, 30, or 90
  days (pick from the dropdown) — a quick way to see if business is
  trending up or down without opening the full Reports page.
- **Time zone**: every "today", timestamp, and chart date is calculated in
  **Asia/Phnom_Penh**, regardless of where the server itself is physically
  hosted (Render's free tier runs on UTC servers).
- **Staying logged in**: the "Keep me signed in" checkbox on the login page
  (checked by default) keeps you signed in for 30 days. Uncheck it on a
  shared/public device if you don't want that.
- The default language is Khmer. Switch anytime with the ខ្មែរ / EN toggle
  top-right; your choice is remembered for that browser session.
- **Out of stock**: materials no longer use a manually-set "reorder
  threshold" — a material is simply flagged "Out of stock" once its
  quantity reaches zero, calculated automatically from restocks and orders.
