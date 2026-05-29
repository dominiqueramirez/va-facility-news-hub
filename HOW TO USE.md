# VA Medical Facility News & Stories Scraper

## What It Does

Checks all 134 VA medical facility websites for **new stories and news releases** published within the past 7 days (configurable). Downloads each article as a clean `.md` file with images and produces a summary report.

## First-Time Setup

```powershell
cd "c:\Users\vacoramird\OneDrive - Department of Veterans Affairs\VS Code Apps\VA Medical Facility URLs"
pip install -r requirements.txt
```

## How to Run

### Default — past 7 days, all facilities
```powershell
python scraper.py
```

### Custom look-back window (e.g. 14 days)
```powershell
python scraper.py --days 14
```

### Test mode — only check first 3 facilities
```powershell
python scraper.py --test 3
```

### Combine options
```powershell
python scraper.py --days 14 --test 5
```

## Output Structure

Each run creates a date-stamped folder under `output/`:

```
output/
└── 2026-05-19/
    ├── _run-summary.md                  ← Open this first
    ├── stories/
    │   ├── 001_AL_birmingham-health-care_2026-05-14_story-slug.md
    │   ├── 002_GA_atlanta-health-care_2026-05-15_another-story.md
    │   └── images/
    │       ├── 001_img1.jpg
    │       └── 002_img1.png
    └── news-releases/
        ├── 001_CA_palo-alto_2026-05-18_headline-slug.md
        └── images/
            └── 001_img1.jpg
```

### Filename format
`{serial}_{state}_{facility-slug}_{publish-date}_{title-slug}.md`

- **Serial number** — keeps files ordered and unique
- **State abbreviation** — instantly see geographic origin
- **Facility slug** — which VA facility
- **Publish date** — when the article was published
- **Title slug** — human-readable at a glance

### Summary report (`_run-summary.md`)

A table showing what was found: facility name, article type, title (linked to the file), date, and image count. Start here to see the run results at a glance.

## How It Works

1. Reads facility URLs from `VA Medical Facility URLs.csv`
2. For each facility, fetches the listing pages and extracts **embedded JSON data** (VA.gov is a React app that pre-embeds all content as JSON in the page source)
3. Filters stories and news releases by date using the JSON timestamps — no need to visit individual pages just to check dates
4. For qualifying articles, fetches the article page JSON to get the full body content, author, and hero image
5. Downloads article content + images, converts HTML body to clean markdown
6. Writes a summary report (`_run-summary.md`)

## Notes

- Uses Python `requests` only (no browser, no Playwright needed — all data is extracted from embedded JSON)

---

## GitHub Pages Frontend

A static website that lets coworkers browse scraped articles and copy individual fields into WordPress.

### Build the site

After scraping, run the build script to generate the frontend data:

```powershell
python build_site.py
```

This creates/updates:
- `docs/data.json` — all article metadata + body text
- `docs/images/` — hero images copied from output folders

### Deploy to GitHub Pages

1. Push the repo to GitHub (including `docs/`)
2. In GitHub repo settings → **Pages** → set source to **Deploy from a branch** → pick **main** branch, folder **/docs**
3. The site will be live at `https://<username>.github.io/<repo-name>/`

### Test locally

```powershell
Set-Location docs; python -m http.server 8080
```
Then open `http://localhost:8080` in your browser.

### Full workflow for future scrapes

```powershell
python scraper.py           # 1. Scrape new content
python build_site.py        # 2. Rebuild the site data + copy images
git add docs/ ; git commit -m "Scrape $(Get-Date -Format yyyy-MM-dd)" ; git push
```

Then share the GitHub Pages URL with your coworker to review.

### Frontend features

- **Filter** by type (Story / News Release), state, scrape date, or image availability
- **Scrape date defaults to the latest run** — reviewers only see fresh content
- **Checkbox on every card** to select articles for VA News
- **Floating selection bar** — shows count + comma-separated IDs, with a Copy IDs button
- **Copy buttons** on every metadata field
- **Copy Body** — copy article text as plain text or HTML
- **Download** hero images with one click
- **Expandable full article** view with rendered markdown

---

## Publishing to VA News (news.va.gov)

### Setup (first time only)

Add your WordPress credentials to `.env` (never commit this file):

```
WP_USERNAME=your_wordpress_username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
WP_SITE_URL=https://news.va.gov
```

Generate the Application Password at: `https://news.va.gov/wp-admin/profile.php` → Application Passwords section.

### Workflow

1. **Coworker** reviews the GitHub Pages site, checks articles they want, clicks **Copy IDs**
2. **You** receive the comma-separated ID list (e.g. `20260519-A3F2, 20260519-B1C4`)
3. **You** run:

```powershell
python publish.py 20260519-A3F2, 20260519-B1C4, 20260519-D7E9
```

4. The script shows you exactly what will be published and asks you to type `yes` to confirm
5. It uploads hero images and creates **draft posts only** — nothing is auto-published
6. Jason reviews and publishes drafts at his leisure from the VA News WordPress dashboard

### Article IDs

Each article has a deterministic ID in the format `YYYYMMDD-XXXX` (e.g. `20260519-A3F2`).  
The same article always gets the same ID across all scrape runs (derived from its source URL + date).

### Safety

- `publish.py` will **never publish** without your explicit `yes` confirmation
- All posts are created as `draft` status — never auto-published
- Credentials are read from `.env` only — never hardcoded
- `.env` is in `.gitignore` and will never be committed
