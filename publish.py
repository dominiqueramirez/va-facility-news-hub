#!/usr/bin/env python3
"""
Publish selected VA facility articles to VA News (news.va.gov) as drafts.

Takes a comma-separated list of article IDs, looks them up in docs/data.json,
uploads hero images, and creates draft posts via the WordPress REST API.

Usage:
    python publish.py 20260519-A3F2, 20260519-B1C4, 20260519-D7E9

SAFETY:
    - All posts are created as DRAFTS only (never auto-published)
    - Requires explicit confirmation before any post is created
    - Credentials are read from .env (never hardcoded)
"""

import json
import re
import sys
from base64 import b64encode
from pathlib import Path

import requests
from dotenv import load_dotenv
import os


def get_auth_headers():
    """Build WordPress API auth headers from .env credentials."""
    load_dotenv()
    username = os.getenv('WP_USERNAME')
    app_password = os.getenv('WP_APP_PASSWORD')
    site_url = os.getenv('WP_SITE_URL')

    if not all([username, app_password, site_url]):
        print("ERROR: Missing credentials in .env file.")
        print("Required: WP_USERNAME, WP_APP_PASSWORD, WP_SITE_URL")
        sys.exit(1)

    credentials = b64encode(f'{username}:{app_password}'.encode()).decode('utf-8')
    headers = {
        'Authorization': f'Basic {credentials}',
        'Content-Type': 'application/json',
    }
    return headers, site_url


def load_articles():
    """Load articles from docs/data.json."""
    data_path = Path(__file__).parent / 'docs' / 'data.json'
    if not data_path.exists():
        print("ERROR: docs/data.json not found. Run build_site.py first.")
        sys.exit(1)

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    return {a['id']: a for a in data['articles'] if 'id' in a}


def markdown_to_html(md_text):
    """Convert article markdown body to basic HTML for WordPress."""
    if not md_text:
        return ''

    html_parts = []
    for block in re.split(r'\n\n+', md_text):
        block = block.strip()
        if not block:
            continue

        # Blockquotes
        if block.startswith('> '):
            inner = re.sub(r'^> ?', '', block, flags=re.MULTILINE)
            html_parts.append(f'<blockquote><p>{inline_md(inner)}</p></blockquote>')
            continue

        # Unordered lists
        if re.match(r'^\* ', block):
            items = [line.lstrip('* ') for line in block.split('\n') if line.startswith('* ')]
            li = ''.join(f'<li>{inline_md(i)}</li>' for i in items)
            html_parts.append(f'<ul>{li}</ul>')
            continue

        # Ordered lists
        if re.match(r'^\d+\. ', block):
            items = [re.sub(r'^\d+\. ', '', line) for line in block.split('\n') if re.match(r'^\d+\. ', line)]
            li = ''.join(f'<li>{inline_md(i)}</li>' for i in items)
            html_parts.append(f'<ol>{li}</ol>')
            continue

        # Horizontal rule
        if block == '---':
            html_parts.append('<hr>')
            continue

        # Headings
        if block.startswith('### '):
            html_parts.append(f'<h3>{inline_md(block[4:])}</h3>')
            continue
        if block.startswith('## '):
            html_parts.append(f'<h2>{inline_md(block[3:])}</h2>')
            continue
        if block.startswith('# '):
            html_parts.append(f'<h1>{inline_md(block[2:])}</h1>')
            continue

        # Paragraph
        html_parts.append(f'<p>{inline_md(block)}</p>')

    return '\n'.join(html_parts)


def inline_md(text):
    """Convert inline markdown (bold, italic, links, images) to HTML."""
    # Images
    text = re.sub(r'!\[(.*?)\]\((.*?)\)', r'<img alt="\1" src="\2">', text)
    # Links
    text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    # Line breaks
    text = text.replace('\n', '<br>')
    return text


def upload_image(image_path, headers, site_url, alt_text=''):
    """Upload an image to WordPress media library. Returns media ID or None."""
    if not image_path or not Path(image_path).exists():
        return None

    img = Path(image_path)
    mime_types = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                  '.gif': 'image/gif', '.webp': 'image/webp'}
    mime = mime_types.get(img.suffix.lower(), 'image/jpeg')

    upload_headers = {
        'Authorization': headers['Authorization'],
        'Content-Disposition': f'attachment; filename="{img.name}"',
        'Content-Type': mime,
    }

    with open(img, 'rb') as f:
        resp = requests.post(
            f'{site_url}/wp-json/wp/v2/media',
            headers=upload_headers,
            data=f
        )

    if resp.status_code == 201:
        media = resp.json()
        media_id = media.get('id')
        # Set alt text (and a human-readable title) on the uploaded media
        if media_id and alt_text:
            requests.post(
                f'{site_url}/wp-json/wp/v2/media/{media_id}',
                headers=headers,
                json={'alt_text': alt_text, 'title': alt_text}
            )
        return media_id
    else:
        print(f"  WARNING: Image upload failed ({resp.status_code}): {resp.text[:200]}")
        return None


def publish_article(article, headers, site_url, docs_dir):
    """Create a single draft post on WordPress. Returns (success, post_id, message)."""
    # Convert body to HTML
    html_body = markdown_to_html(article.get('body', ''))

    # Upload hero image if available
    featured_media_id = None
    hero_path = article.get('hero_image', '')
    if hero_path:
        full_path = docs_dir / hero_path
        if full_path.exists():
            print(f"  Uploading hero image: {hero_path}")
            featured_media_id = upload_image(full_path, headers, site_url,
                                             alt_text=article.get('hero_image_alt', ''))
            if featured_media_id:
                print(f"  Image uploaded (media ID: {featured_media_id})")

    # Build post payload
    post_data = {
        'title': article.get('title', 'Untitled'),
        'content': html_body,
        'status': 'draft',  # ALWAYS draft — never auto-publish
        'excerpt': article.get('meta_description', ''),
    }

    if featured_media_id:
        post_data['featured_media'] = featured_media_id

    resp = requests.post(
        f'{site_url}/wp-json/wp/v2/posts',
        headers=headers,
        json=post_data
    )

    if resp.status_code == 201:
        post = resp.json()
        return True, post.get('id'), post.get('link', '')
    else:
        return False, None, resp.text[:300]


def main():
    if len(sys.argv) < 2:
        print("Usage: python publish.py ID1, ID2, ID3, ...")
        print("  IDs are comma-separated article IDs from the VA Facility News Hub.")
        print("  Example: python publish.py 20260519-A3F2, 20260519-B1C4")
        sys.exit(0)

    # Parse IDs from arguments (support comma-separated, with or without spaces)
    raw_ids = ' '.join(sys.argv[1:])
    article_ids = [aid.strip() for aid in raw_ids.split(',') if aid.strip()]

    if not article_ids:
        print("No article IDs provided.")
        sys.exit(1)

    # Load data
    headers, site_url = get_auth_headers()
    articles_by_id = load_articles()
    docs_dir = Path(__file__).parent / 'docs'

    # Validate IDs
    valid = []
    for aid in article_ids:
        if aid in articles_by_id:
            valid.append((aid, articles_by_id[aid]))
        else:
            print(f"WARNING: ID '{aid}' not found in data.json — skipping.")

    if not valid:
        print("No valid article IDs found. Nothing to publish.")
        sys.exit(1)

    # Show what will be published and ask for confirmation
    print(f"\n{'='*60}")
    print(f"  PUBLISH TO VA NEWS AS DRAFTS")
    print(f"  Target: {site_url}")
    print(f"  Articles: {len(valid)}")
    print(f"{'='*60}\n")

    for aid, article in valid:
        has_img = '✓' if article.get('hero_image') else '✗'
        print(f"  [{aid}] {article['title']}")
        print(f"          {article['facility']} · {article['state']}")
        print(f"          Image: {has_img}  |  Date: {article['date']}")
        print()

    print(f"{'='*60}")
    confirm = input("  Type 'yes' to create these as DRAFTS on VA News: ").strip().lower()
    print(f"{'='*60}\n")

    if confirm != 'yes':
        print("Cancelled. No posts were created.")
        sys.exit(0)

    # Publish each article
    results = []
    for aid, article in valid:
        print(f"Publishing [{aid}] {article['title']}...")
        success, post_id, msg = publish_article(article, headers, site_url, docs_dir)
        if success:
            print(f"  ✓ Draft created (post ID: {post_id})")
            print(f"    Preview: {msg}")
            results.append((aid, True, post_id))
        else:
            print(f"  ✗ Failed: {msg}")
            results.append((aid, False, None))
        print()

    # Summary
    succeeded = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{'='*60}")
    print(f"  DONE: {succeeded} drafts created, {failed} failed")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
