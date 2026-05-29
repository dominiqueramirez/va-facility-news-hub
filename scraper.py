#!/usr/bin/env python3
"""
VA Medical Facility News & Stories Scraper
==========================================
Scrapes news releases and stories from VA medical facility websites,
filters to articles published within a configurable window (default 7 days),
downloads them as markdown files with images, and produces a summary report.

Uses embedded JSON data from VA.gov React pages — no browser/Playwright needed.

Usage:
    python scraper.py                  # Default: past 7 days, all facilities
    python scraper.py --days 14        # Past 14 days
    python scraper.py --test 3         # Only check the first 3 facilities
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from markdownify import markdownify as md


# ── Configuration ────────────────────────────────────────────────────────────

REQUEST_DELAY = 0.5        # Seconds between requests (respectful rate limiting)
IMAGE_DELAY = 0.2          # Lighter delay for image downloads
TIMEOUT = 30               # Request timeout in seconds
MAX_RETRIES = 2            # Retry failed requests this many times
USER_AGENT = "VA-Facility-News-Scraper/1.0 (internal-comms-monitoring)"

STATE_ABBREV = {
    'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
    'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
    'Florida': 'FL', 'Georgia': 'GA', 'Hawaii/Pacific': 'HI', 'Idaho': 'ID',
    'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
    'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
    'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
    'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
    'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
    'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
    'Oregon': 'OR', 'Pennsylvania': 'PA', 'Puerto Rico': 'PR',
    'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD',
    'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT',
    'Virginia': 'VA', 'Washington': 'WA', 'Washington DC': 'DC',
    'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY',
    'Philippines': 'PH',
}

BASE_URL = 'https://www.va.gov'


# ── Utilities ────────────────────────────────────────────────────────────────

def parse_iso_date(text):
    """Parse an ISO 8601 date string into a naive datetime (UTC)."""
    if not text:
        return None
    try:
        # Handle formats like "2026-05-12T21:32:30+00:00"
        text = text.replace('Z', '+00:00')
        dt = datetime.fromisoformat(text)
        # Convert to UTC naive for comparison
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def slugify(text, max_length=60):
    """Convert text to a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')
    return text[:max_length].rstrip('-')


def facility_slug(url):
    """Extract facility slug from URL, e.g. 'birmingham-health-care'."""
    parts = urlparse(url).path.strip('/').split('/')
    return parts[0] if parts else 'unknown'


def extract_page_json(html_text):
    """
    Extract the embedded Next.js / React page data from a VA.gov HTML page.
    VA.gov embeds all content as JSON in a <script> tag.
    """
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html_text, re.DOTALL)
    for script in reversed(scripts):  # The data script is usually the last one
        script = script.strip()
        if script.startswith('{"props":'):
            try:
                data = json.loads(script)
                return data['props']['pageProps']['serializedResource']['data']
            except (json.JSONDecodeError, KeyError):
                continue
    return None


# ── Scraper ──────────────────────────────────────────────────────────────────

class VAScraper:
    def __init__(self, csv_path, output_base, days=7):
        self.csv_path = Path(csv_path)
        self.days = days
        self.cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) - timedelta(days=days)
        self.run_date = datetime.now().strftime('%Y-%m-%d')
        self.output_dir = Path(output_base) / self.run_date

        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})

        # Counters
        self.serial = {'stories': 0, 'news-releases': 0}
        self.results = []
        self.errors = []
        self.facilities_checked = 0
        self.pages_fetched = 0

    # ── HTTP helpers ─────────────────────────────────────────────────────

    def fetch(self, url):
        """GET a URL with rate-limiting and retries."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                time.sleep(REQUEST_DELAY)
                resp = self.session.get(url, timeout=TIMEOUT)
                resp.encoding = 'utf-8'   # VA.gov is UTF-8 but doesn't declare it in headers
                self.pages_fetched += 1
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)

    def fetch_image(self, url):
        """Download image bytes; returns None on failure."""
        try:
            time.sleep(IMAGE_DELAY)
            resp = self.session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.content
        except Exception:
            return None

    # ── Main loop ────────────────────────────────────────────────────────

    def run(self, limit=None):
        """Entry point. Set limit to process only the first N facilities."""
        facilities = self._load_csv()
        if limit:
            facilities = facilities[:limit]
        total = len(facilities)

        for subdir in ['stories', 'stories/images', 'news-releases', 'news-releases/images']:
            (self.output_dir / subdir).mkdir(parents=True, exist_ok=True)

        print(f"VA Medical Facility News & Stories Scraper")
        print(f"{'=' * 55}")
        print(f"Run date:    {self.run_date}")
        print(f"Window:      Past {self.days} days (since {self.cutoff.strftime('%B %d, %Y')})")
        print(f"Facilities:  {total}")
        print(f"Output:      {self.output_dir}")
        print(f"{'=' * 55}\n")

        for i, fac in enumerate(facilities):
            name = fac.get('Facility Name', '?')
            state = fac.get('State', '?')
            print(f"[{i + 1:3d}/{total}] {name} ({state})")
            try:
                self._process_facility(fac)
            except Exception as e:
                msg = f"  ERROR processing facility: {e}"
                print(msg)
                self.errors.append({'facility': name, 'error': str(e)})
            self.facilities_checked += 1

        self._write_summary()
        print(f"\n{'=' * 55}")
        print(f"Done!  {len(self.results)} articles found across {self.facilities_checked} facilities.")
        print(f"Pages fetched: {self.pages_fetched}")
        if self.errors:
            print(f"Errors: {len(self.errors)}")
        print(f"Output: {self.output_dir}")

    # ── CSV ──────────────────────────────────────────────────────────────

    def _load_csv(self):
        with open(self.csv_path, 'r', encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))

    # ── Per-facility processing ──────────────────────────────────────────

    def _process_facility(self, fac):
        nr_url = (fac.get('News Releases URL') or '').strip()
        if nr_url:
            try:
                self._scrape_listing(fac, nr_url, 'news-releases')
            except Exception as e:
                print(f"    ERROR (news-releases): {e}")
                self.errors.append({'facility': fac.get('Facility Name'), 'error': f"news-releases: {e}"})

        stories_url = (fac.get('Stories URL') or '').strip()
        if stories_url:
            try:
                self._scrape_listing(fac, stories_url, 'stories')
            except Exception as e:
                print(f"    ERROR (stories): {e}")
                self.errors.append({'facility': fac.get('Facility Name'), 'error': f"stories: {e}"})

    # ── Scrape a listing page (works for both stories and news-releases) ─

    def _scrape_listing(self, fac, listing_url, content_type):
        """
        Fetch listing page, extract embedded JSON, filter by date,
        then fetch and save qualifying articles.
        """
        resp = self.fetch(listing_url)
        page_data = extract_page_json(resp.text)

        if not page_data:
            print(f"    WARN  Could not extract JSON from {content_type} listing")
            return

        # Get the items list — key name matches content type
        items = page_data.get(content_type, page_data.get('stories', []))
        if not items:
            print(f"    No {content_type} listed")
            return

        found = False
        for item in items:
            # Determine the publication date
            date_field = item.get('releaseDate') or item.get('lastUpdated') or item.get('date', '')
            pub_date = parse_iso_date(date_field)

            if pub_date is None:
                continue
            if pub_date < self.cutoff:
                break  # Items are newest-first → stop early

            title = item.get('title', 'Untitled')
            link = item.get('link', '')
            if not link:
                continue

            full_url = BASE_URL + link
            found = True

            try:
                self._download_and_save_from_json(fac, content_type, full_url, pub_date)
            except Exception as e:
                print(f"    ERROR downloading '{title}': {e}")

        if not found:
            print(f"    No recent {content_type}")

    # ── Download & save using JSON data from individual page ─────────────

    def _download_and_save_from_json(self, fac, content_type, url, listing_date):
        """Fetch an individual article page, extract JSON data, save as markdown."""
        resp = self.fetch(url)
        article = extract_page_json(resp.text)

        if not article:
            print(f"    WARN  Could not extract article JSON from {url}")
            return

        title = article.get('title', 'Untitled')

        # Use the most specific date available
        pub_date = (
            parse_iso_date(article.get('date'))
            or parse_iso_date(article.get('releaseDate'))
            or parse_iso_date(article.get('lastUpdated'))
            or listing_date
        )

        # Author
        author_data = article.get('author')
        author_name = None
        author_desc = None
        if isinstance(author_data, dict):
            author_name = author_data.get('title', '')
            author_desc = author_data.get('field_description', '')

        # SEO meta description
        meta_description = ''
        metatags = article.get('metatags', [])
        for tag in metatags:
            attrs = tag.get('attributes', {})
            if attrs.get('name') == 'description' or attrs.get('property') == 'og:description':
                meta_description = attrs.get('content', '')
                break

        # Body content (HTML) — stories use 'bodyContent', news releases use 'fullText'
        body_html = article.get('bodyContent') or article.get('fullText') or ''
        intro_text = article.get('introText', '')

        # Hero image
        hero_image_url = None
        hero_image_alt = ''
        image_data = article.get('image')
        if isinstance(image_data, dict):
            links = image_data.get('links', {})
            # Prefer 2:1 large, fall back to others
            for size_key in ['2_1_large', '2_1_medium', '1_1_square_large', '3_2_medium_thumbnail']:
                if size_key in links:
                    hero_image_url = links[size_key].get('href', '')
                    meta = links[size_key].get('meta', {})
                    hero_image_alt = meta.get('linkParams', {}).get('alt', '')
                    break

        # Build file
        state = fac.get('State', 'Unknown')
        state_abbr = STATE_ABBREV.get(state, state[:2].upper())
        fac_slug_str = facility_slug(fac.get('Main Facility URL', ''))

        self.serial[content_type] += 1
        serial = self.serial[content_type]
        date_str = pub_date.strftime('%Y-%m-%d')
        title_slug = slugify(title)

        filename = f"{serial:03d}_{state_abbr}_{fac_slug_str}_{date_str}_{title_slug}.md"
        images_dir = self.output_dir / content_type / 'images'
        img_count = 0

        # Download hero image
        hero_md = ''
        if hero_image_url:
            ext = Path(urlparse(hero_image_url).path).suffix.lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                ext = '.jpg'
            hero_name = f"{serial:03d}_hero{ext}"
            hero_data = self.fetch_image(hero_image_url)
            if hero_data:
                (images_dir / hero_name).write_bytes(hero_data)
                img_count += 1
                hero_md = f'![{hero_image_alt}](images/{hero_name})\n\n'

        # Process inline images in body HTML and convert to markdown
        body_md, inline_imgs = self._process_body(body_html, images_dir, serial, url)
        img_count += inline_imgs

        # Author line
        author_line = ''
        if author_name:
            parts = [author_name]
            if author_desc:
                parts.append(author_desc)
            author_line = ', '.join(parts)

        # Assemble markdown file
        lines = [
            '---',
            f'title: "{title}"',
            f'facility: "{fac.get("Facility Name", "")}"',
            f'state: "{state}"',
            f'type: "{content_type}"',
            f'date: "{date_str}"',
            f'author: "{author_line or "Unknown"}"',
            f'meta_description: "{meta_description}"',
            f'hero_image_url: "{hero_image_url or ""}"',
            f'source_url: "{url}"',
            f'scraped_date: "{self.run_date}"',
            '---',
            '',
            f'# {title}',
            '',
            hero_md,
        ]
        if author_line:
            lines.append(f'*By {author_line}*')
            lines.append('')
        lines.append(f'*Published: {pub_date.strftime("%B %d, %Y")}*')
        lines.append('')
        lines.append(f'*Source: [{fac.get("Facility Name", "")}]({url})*')
        lines.append('')
        lines.append('---')
        lines.append('')
        if intro_text:
            lines.append(f'> {intro_text}')
            lines.append('')
        lines.append(body_md)

        filepath = self.output_dir / content_type / filename
        filepath.write_text('\n'.join(lines), encoding='utf-8')

        print(f"    OK    [{content_type}] {title} ({date_str})")

        self.results.append({
            'facility': fac.get('Facility Name', ''),
            'state': state,
            'type': content_type,
            'title': title,
            'date': date_str,
            'file': str(filepath.relative_to(self.output_dir)),
            'url': url,
            'images': img_count,
        })

    # ── Body content processing ──────────────────────────────────────────

    def _process_body(self, body_html, images_dir, serial, base_url):
        """
        Process HTML body content: download inline images, convert to markdown.
        Returns (markdown_text, image_count).
        """
        if not body_html:
            return '', 0

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body_html, 'html.parser')
        downloaded = 0

        for i, img in enumerate(soup.find_all('img'), 1):
            src = img.get('src', '')
            if not src:
                continue

            full_url = urljoin(base_url, src)

            ext = Path(urlparse(full_url).path).suffix.lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg'):
                ext = '.jpg'
            local_name = f"{serial:03d}_img{i}{ext}"
            local_path = images_dir / local_name

            data = self.fetch_image(full_url)
            if data:
                local_path.write_bytes(data)
                downloaded += 1
                alt = img.get('alt', '')
                img['src'] = f"images/{local_name}"
                img['alt'] = alt

        body_md = md(str(soup), heading_style='ATX', strip=['script', 'style'])
        # Clean up excessive blank lines
        body_md = re.sub(r'\n{4,}', '\n\n\n', body_md)
        return body_md, downloaded

    # ── Summary report ───────────────────────────────────────────────────

    def _write_summary(self):
        path = self.output_dir / '_run-summary.md'
        stories = [r for r in self.results if r['type'] == 'stories']
        releases = [r for r in self.results if r['type'] == 'news-releases']
        fac_set = {r['facility'] for r in self.results}
        total_imgs = sum(r['images'] for r in self.results)

        lines = [
            '# VA Facility News Scraper — Run Summary',
            '',
            f'**Run date:** {self.run_date}  ',
            f'**Window:** Past {self.days} days (since {self.cutoff.strftime("%B %d, %Y")})  ',
            f'**Facilities checked:** {self.facilities_checked}  ',
            f'**Pages fetched:** {self.pages_fetched}  ',
            '',
            '## Results',
            '',
            '| Metric | Count |',
            '|--------|------:|',
            f'| Facilities with new content | {len(fac_set)} |',
            f'| Stories found | {len(stories)} |',
            f'| News releases found | {len(releases)} |',
            f'| **Total articles** | **{len(self.results)}** |',
            f'| Images downloaded | {total_imgs} |',
            f'| Errors | {len(self.errors)} |',
            '',
        ]

        if self.results:
            lines.append('## Articles Found')
            lines.append('')
            lines.append('| # | Type | State | Facility | Title | Date | Images |')
            lines.append('|--:|------|-------|----------|-------|------|-------:|')
            for idx, r in enumerate(self.results, 1):
                lines.append(
                    f'| {idx} | {r["type"]} | {r["state"]} | {r["facility"]} '
                    f'| [{r["title"]}]({r["file"]}) | {r["date"]} | {r["images"]} |'
                )
            lines.append('')

        if self.errors:
            lines.append('## Errors')
            lines.append('')
            for err in self.errors:
                lines.append(f'- **{err["facility"]}**: {err["error"]}')
            lines.append('')

        if not self.results:
            lines.append('*No new articles found within the specified window.*')
            lines.append('')

        path.write_text('\n'.join(lines), encoding='utf-8')
        print(f"\nSummary: {path}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Scrape VA medical facility news releases and stories.'
    )
    parser.add_argument('--days', type=int, default=7,
                        help='Look-back window in days (default: 7)')
    parser.add_argument('--test', type=int, default=None,
                        help='Only process the first N facilities (for testing)')
    parser.add_argument('--csv', type=str, default=None,
                        help='Path to facility CSV (default: auto-detect)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output base directory (default: ./output)')
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    csv_path = Path(args.csv) if args.csv else script_dir / 'VA Medical Facility URLs.csv'
    if not csv_path.exists():
        print(f"Error: CSV not found at {csv_path}")
        sys.exit(1)

    output_dir = Path(args.output) if args.output else script_dir / 'output'

    scraper = VAScraper(csv_path, output_dir, days=args.days)
    scraper.run(limit=args.test)


if __name__ == '__main__':
    main()
