import requests
import os
import re
from requests.utils import quote
from urllib.parse import urljoin

# === Configuration ===
project_id = '%PROJECT%'
api_key = '%API-KEY%'
base_url = 'https://%SITE-URL%'
headers = {'X-Redmine-API-Key': api_key}

# === Create output folder ===
output_folder = 'wiki_pages'
os.makedirs(output_folder, exist_ok=True)

def download_file(url, path):
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            with open(path, 'wb') as f:
                f.write(resp.content)
            print(f"   📎 Downloaded: {os.path.basename(path)}")
        else:
            print(f"   ⚠️ Failed to download {url} ({resp.status_code})")
    except Exception as ex:
        print(f"   ⚠️ Exception downloading {url}: {ex}")

def download_embedded_images(content, attachments, img_folder):
    # Find embedded images in Textile or HTML
    textile_imgs = re.findall(r'!(.+?)!', content)
    html_imgs = re.findall(r'<img [^>]*src=[\'"]([^\'"]+)[\'"]', content)
    all_imgs = set(textile_imgs + html_imgs)
    if not all_imgs:
        return

    os.makedirs(img_folder, exist_ok=True)
    attachment_lookup = {att['filename']: att for att in attachments}

    for img in all_imgs:
        if img in attachment_lookup:
            img_url = attachment_lookup[img]['content_url']
        elif img.startswith('http://') or img.startswith('https://'):
            img_url = img
        elif img.startswith('/'):
            img_url = urljoin(base_url, img)
        else:
            print(f"   ⚠️ Could not resolve image '{img}' as attachment or absolute URL, skipping.")
            continue

        img_filename = os.path.basename(img.split('?')[0])
        img_path = os.path.join(img_folder, img_filename)
        download_file(img_url, img_path)

# === Step 1: Get the list of wiki pages ===
wiki_index_url = f'{base_url}/projects/{project_id}/wiki/index.json'
response = requests.get(wiki_index_url, headers=headers)

if response.status_code != 200:
    print(f"❌ Failed to fetch wiki index: {response.status_code}")
    print(response.text)
    exit()

wiki_pages = response.json().get('wiki_pages', [])
print(f"📄 Found {len(wiki_pages)} wiki pages.")

# === Step 2: Download each wiki page and metadata ===
for page in wiki_pages:
    title = page['title']
    print(f"⬇ Downloading: {title}")

    # URL-encode the title for the request
    safe_title_for_url = quote(title, safe='')
    page_url = f'{base_url}/projects/{project_id}/wiki/{safe_title_for_url}.json?include=attachments'
    page_response = requests.get(page_url, headers=headers)

    if page_response.status_code != 200:
        print(f"⚠️ Failed to fetch page '{title}': {page_response.status_code}")
        continue

    try:
        page_data = page_response.json().get('wiki_page', {})
    except Exception as e:
        print(f"⚠️ Could not parse JSON for page '{title}': {str(e)}")
        print("Response text was:", page_response.text[:300])
        continue

    # Metadata fields
    content = page_data.get('text', '')
    author = page_data.get('author', {}).get('name', 'Unknown')
    created_on = page_data.get('created_on', 'Unknown')
    updated_on = page_data.get('updated_on', 'Unknown')
    version = page_data.get('version', 'Unknown')
    comments = page_data.get('comments', '')
    parent = page_data.get('parent', {}).get('title', 'None')
    attachments = page_data.get('attachments', [])

    # Sanitize filename
    safe_title = re.sub(r'[<>:\"/\\|?*]', '_', title)
    page_file_path = os.path.join(output_folder, f"{safe_title}.txt")

    # Write metadata + content
    with open(page_file_path, 'w', encoding='utf-8') as f:
        f.write(f"Title: {title}\n")
        f.write(f"Author: {author}\n")
        f.write(f"Created On: {created_on}\n")
        f.write(f"Last Updated: {updated_on}\n")
        f.write(f"Version: {version}\n")
        f.write(f"Parent Page: {parent}\n")
        f.write(f"Comments: {comments}\n")
        f.write(f"Attachments: {[att.get('filename') for att in attachments]}\n")
        f.write("\n---\n\n")
        f.write(content)

    # === Download attachments (if any) ===
    if attachments:
        attachment_folder = os.path.join(output_folder, f"{safe_title}_attachments")
        os.makedirs(attachment_folder, exist_ok=True)
        for att in attachments:
            filename = att.get('filename')
            content_url = att.get('content_url')
            if not content_url or not filename:
                continue
            file_path = os.path.join(attachment_folder, filename)
            download_file(content_url, file_path)

    # === Download embedded images ===
    img_folder = os.path.join(output_folder, f"{safe_title}_images")
    download_embedded_images(content, attachments, img_folder)

print(f"\n✅ Finished downloading {len(wiki_pages)} wiki pages into '{output_folder}' folder (including embedded images and all attachments).")
