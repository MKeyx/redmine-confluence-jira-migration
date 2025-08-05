import os
import re
import subprocess
from atlassian import Confluence
import requests

# === Confluence configuration ===
CONFLUENCE_URL = "https://REPLACEWITHYOURS.atlassian.net/wiki"
CONFLUENCE_USER = "REPLACEWITHYOURS"
CONFLUENCE_API_TOKEN = "REPLACEWITHYOURS"
CONFLUENCE_SPACE_KEY = "RA""  # Your target space key

# === Local wiki export location ===
wiki_dir = r"LOCATION OF DOWNLOADED WIKI"  # Use raw string to avoid escape issues

# === Connect to Confluence ===
confluence = Confluence(
    url=CONFLUENCE_URL,
    username=CONFLUENCE_USER,
    password=CONFLUENCE_API_TOKEN
)


def html_replace_img_with_confluence_macro(html, attachments):
    # Only replace images that match an attachment
    filenames = {os.path.basename(f) for f in attachments}
    def replacer(match):
        fname = match.group(1)
        if fname in filenames:
            return f'<ac:image><ri:attachment ri:filename="{fname}"/></ac:image>'
        else:
            return match.group(0)
    return re.sub(r'<img[^>]+src=[\'"]([^\'"]+)[\'"][^>]*>', replacer, html)
def textile_to_html_with_pandoc(textile_text):
    proc = subprocess.run(
        ['pandoc', '--from=textile', '--to=html'],
        input=textile_text.encode('utf-8'),
        stdout=subprocess.PIPE
    )
    return proc.stdout.decode('utf-8')

def create_page_hierarchy(wiki_dir):
    hierarchy = {}
    for fname in os.listdir(wiki_dir):
        if fname.endswith('.txt'):
            title = fname[:-4]
            path = os.path.join(wiki_dir, fname)
            parent = None
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith("Parent Page:"):
                        parent = line.replace("Parent Page:", '').strip()
                        if parent == 'None':
                            parent = None
                        break
            attachments = []
            attach_dir = os.path.join(wiki_dir, f"{title}_attachments")
            if os.path.exists(attach_dir):
                attachments = [os.path.join(attach_dir, af) for af in os.listdir(attach_dir)]
            images = []
            img_dir = os.path.join(wiki_dir, f"{title}_images")
            if os.path.exists(img_dir):
                images = [os.path.join(img_dir, af) for af in os.listdir(img_dir)]
            hierarchy[title] = {
                'file': path,
                'parent': parent,
                'attachments': attachments,
                'images': images
            }
    return hierarchy

def get_page_id(title, space, parent_id=None):
    results = confluence.get_page_id(space, title)
    if results:
        return results
    return None

def upload_attachments_to_page(page_id, file_paths):
    url = f"/rest/api/content/{page_id}/child/attachment"
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        filesize = os.path.getsize(file_path)
        print(f"Uploading {filename} ({filesize} bytes) to page {page_id}")
        if filesize == 0:
            print(f"   ⚠️ Skipping empty file: {filename}")
            continue
        with open(file_path, 'rb') as fobj:
            files = {'file': (filename, fobj)}
            try:
                resp = confluence.request(
                    method='POST',
                    path=url,
                    files=files,
                    headers={'X-Atlassian-Token': 'nocheck'}
                )
                # Check response for status or text
                if hasattr(resp, 'status_code'):
                    status = resp.status_code
                    text = resp.text
                else:
                    status = resp.get('statusCode', 'n/a')
                    text = str(resp)
                if status in (200, 201):
                    print(f"   📎 Uploaded {filename} to Confluence page")
                else:
                    print(f"   ⚠️ Failed to upload {filename}: HTTP {status}\n{text}")
            except Exception as e:
                print(f"   ⚠️ Exception uploading {filename}: {e}")
                continue


def create_confluence_wiki(wiki_dir, confluence_space):
    hierarchy = create_page_hierarchy(wiki_dir)
    created_pages = {}

    # First, create all root pages (no parent)
    for title, info in hierarchy.items():
        if info['parent'] is None:
            with open(info['file'], 'r', encoding='utf-8') as f:
                raw_content = f.read()
            split = raw_content.find('---\n\n')
            if split != -1:
                body = raw_content[split+5:]
            else:
                body = raw_content
            html_body = textile_to_html_with_pandoc(body)
            html_body = html_replace_img_with_confluence_macro(html_body, info['attachments'] + info['images'])
            try:
                created_page = confluence.create_page(
                    space=confluence_space,
                    title=title,
                    body=html_body,
                    parent_id=None,
                    representation='storage'
                )
                page_id = created_page['id'] if isinstance(created_page, dict) else created_page
                created_pages[title] = page_id
                upload_attachments_to_page(page_id, info['attachments'] + info['images'])
            except Exception as e:
                error_str = str(e)
                if "already exists" in error_str:
                    print(f"⚠️ Page '{title}' already exists. Skipping creation.")
                    page_id = confluence.get_page_id(confluence_space, title)
                    if not page_id:
                        print(f"    ⚠️ Could not find existing page ID for '{title}', skipping attachments.")
                        continue
                    else:
                        created_pages[title] = page_id
                        upload_attachments_to_page(page_id, info['attachments'] + info['images'])
                    continue
                else:
                    print(f"⚠️ Exception while creating page '{title}': {e}")
                    continue
    # Then, create all child pages
    pages_remaining = {k: v for k, v in hierarchy.items() if v['parent'] is not None}
    progress = True
    while pages_remaining and progress:
        progress = False
        for title, info in list(pages_remaining.items()):
            parent_title = info['parent']
            if parent_title in created_pages:
                with open(info['file'], 'r', encoding='utf-8') as f:
                    raw_content = f.read()
                split = raw_content.find('---\n\n')
                if split != -1:
                    body = raw_content[split+5:]
                else:
                    body = raw_content
                html_body = textile_to_html_with_pandoc(body)
                html_body = html_replace_img_with_confluence_macro(html_body, info['attachments'] + info['images'])
                try:
                    created_page = confluence.create_page(
                        space=confluence_space,
                        title=title,
                        body=html_body,
                        parent_id=created_pages[parent_title],
                        representation='storage'
                    )
                    page_id = created_page['id'] if isinstance(created_page, dict) else created_page
                    created_pages[title] = page_id
                    upload_attachments_to_page(page_id, info['attachments'] + info['images'])
                    del pages_remaining[title]
                    progress = True
                except Exception as e:
                    error_str = str(e)
                    if "already exists" in error_str:
                        print(f"⚠️ Page '{title}' already exists. Skipping creation.")
                        page_id = confluence.get_page_id(confluence_space, title)
                        if not page_id:
                            print(f"    ⚠️ Could not find existing page ID for '{title}', skipping attachments.")
                            continue
                        else:
                            created_pages[title] = page_id
                            upload_attachments_to_page(page_id, info['attachments'] + info['images'])
                            del pages_remaining[title]
                            progress = True
                        continue
                    else:
                        print(f"⚠️ Exception while creating page '{title}': {e}")
                        continue

    if pages_remaining:
        print("⚠️ These pages could not be placed due to missing parent(s):")
        for k in pages_remaining:
            print(f"  - {k}")

if __name__ == "__main__":
    create_confluence_wiki(wiki_dir, CONFLUENCE_SPACE_KEY)
