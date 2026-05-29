#!/usr/bin/env python3
"""
Build static site from scraped VA facility articles.

Scans all output/*/stories/*.md and output/*/news-releases/*.md,
extracts metadata + body, copies hero images, and generates
docs/data.json for the frontend. Deduplicates by source URL.

Usage:
    python build_site.py
"""

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


def make_article_id(source_url, date):
    """Generate a deterministic ID from article date + source URL hash.

    Format: YYYYMMDD-XXXX  (e.g. 20260519-A3F2)
    Same article always produces the same ID.
    """
    url_hash = hashlib.sha256(source_url.encode()).hexdigest()[:4].upper()
    date_part = date.replace('-', '') if date else '00000000'
    return f"{date_part}-{url_hash}"


def parse_frontmatter(text):
    """Parse YAML-style frontmatter from markdown text."""
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if not match:
        return {}, text

    fm_text = match.group(1)
    body = text[match.end():]

    metadata = {}
    for line in fm_text.split('\n'):
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"')
            metadata[key] = value

    return metadata, body


def extract_body(raw_body):
    """
    Strip the metadata header (title, image, author, date, source, hr)
    from the markdown body, returning just the article content.
    """
    # The body starts with: # Title\n\n![img](...)\n\n*By ...*\n\n*Published...*\n\n*Source...*\n\n---\n\n
    # We want everything after the first --- separator
    parts = re.split(r'\n---\n', raw_body, maxsplit=1)
    if len(parts) == 2:
        return parts[1].strip()
    return raw_body.strip()


def find_hero_image(md_path):
    """Find the hero image file for a markdown article."""
    images_dir = md_path.parent / 'images'
    if not images_dir.exists():
        return None

    serial_match = re.match(r'^(\d+)_', md_path.stem)
    if not serial_match:
        return None

    serial = serial_match.group(1)
    for img in images_dir.iterdir():
        if img.name.startswith(f'{serial}_hero'):
            return img
    return None


def build_site(project_dir):
    """Scan output folders and build the static site."""
    project_dir = Path(project_dir)
    output_dir = project_dir / 'output'
    docs_dir = project_dir / 'docs'
    docs_images = docs_dir / 'images'

    docs_dir.mkdir(exist_ok=True)
    docs_images.mkdir(exist_ok=True)

    if not output_dir.exists():
        print("No output directory found. Run scraper.py first.")
        return

    articles = []
    seen_urls = set()
    images_copied = 0

    # Process run folders newest-first so dedup keeps the latest scrape
    for run_dir in sorted(output_dir.iterdir(), reverse=True):
        if not run_dir.is_dir() or run_dir.name.startswith('_'):
            continue

        for content_type in ['stories', 'news-releases']:
            type_dir = run_dir / content_type
            if not type_dir.exists():
                continue

            for md_file in sorted(type_dir.glob('*.md')):
                try:
                    text = md_file.read_text(encoding='utf-8')
                except Exception:
                    continue

                metadata, raw_body = parse_frontmatter(text)

                source_url = metadata.get('source_url', '')
                if not source_url or source_url in seen_urls:
                    continue
                seen_urls.add(source_url)

                # Extract clean body
                body = extract_body(raw_body)

                # Find and copy hero image
                hero_site_path = ''
                hero_img = find_hero_image(md_file)
                if hero_img and hero_img.exists():
                    new_name = f"{md_file.stem}_hero{hero_img.suffix}"
                    dest = docs_images / new_name
                    if not dest.exists():
                        shutil.copy2(hero_img, dest)
                    hero_site_path = f"images/{new_name}"
                    images_copied += 1

                article_date = metadata.get('date', '')
                article_id = make_article_id(source_url, article_date)

                articles.append({
                    'id': article_id,
                    'title': metadata.get('title', 'Untitled'),
                    'facility': metadata.get('facility', ''),
                    'state': metadata.get('state', ''),
                    'type': metadata.get('type', ''),
                    'date': article_date,
                    'author': metadata.get('author', 'Unknown'),
                    'meta_description': metadata.get('meta_description', ''),
                    'hero_image_url': metadata.get('hero_image_url', ''),
                    'source_url': source_url,
                    'scraped_date': metadata.get('scraped_date', ''),
                    'hero_image': hero_site_path,
                    'body': body,
                })

    # Sort by date descending
    articles.sort(key=lambda a: a['date'], reverse=True)

    # Write data.json
    data = {
        'generated': datetime.now().isoformat(),
        'total_articles': len(articles),
        'articles': articles,
    }

    data_path = docs_dir / 'data.json'
    data_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    print(f"Built site from scraped data")
    print(f"  Articles:       {len(articles)}")
    print(f"  Images copied:  {images_copied}")
    print(f"  Output:         {docs_dir}")
    print(f"  Data:           {data_path}")


if __name__ == '__main__':
    build_site(Path(__file__).parent)
