import os
import re
import json
import requests
import time
import subprocess

# === Jira configuration ===
JIRA_URL = "https://DOMAIN.atlassian.net"
JIRA_USER = "YOUR EMAIL"
JIRA_API_TOKEN = "YOUR API KEY"
JIRA_PROJECT_KEY = "KEY"  # Your target Jira project key

# === Source Redmine issues (txt files for comments, description, and JSON of the issue)===
redmine_issues_folder = r"LOCATION OF EXPORTED FILES"

auth = (JIRA_USER, JIRA_API_TOKEN)

# === Modify this mapping based on your priority mapping, "REDMINE" : "JIRA"===
priority_map = {
    "P0" : "Highest (P1)",
    "P1" : "High (P2)",
    "P2" : "Medium (P3)",
    "P3" : "Low",
    "P4" : "Lowest",
}

SUMMARY_CHAR_LIMIT = 500    # Length of summary if content is too long, you can modify this to define how much of summary to keep in case you reach the max limit of ADF

def preprocess_redmine_plaintext(text):
    text = re.sub(r'\[\[([^\]]+)\]\]', r'[\1]', text)
    text = re.sub(r'(^\d+\.\s+.+)', r'**\1**', text, flags=re.MULTILINE)
    text = re.sub(r'(\{[\s\S]*?\})', r'```\n\1\n```', text)
    text = re.sub(
        r'((?:[A-Z ]+, ?)+)', 
        lambda m: '\n'.join(f'- {w.strip()}' for w in m.group(1).split(',')),
        text
    )
    text = re.sub(r'(\r\n|\r|\n){2,}', '\n\n', text)
    return text

def adf_heading(text, level=3):
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [
            {"type": "text", "text": text}
        ]
    }

def adf_bold_paragraph(text):
    return {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": text, "marks": [{"type": "strong"}]}
        ]
    }

def adf_infobox(text):
    return {
        "type": "panel",
        "attrs": {"panelType": "info"},
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": text}
                ]
            }
        ]
    }

def adf_paragraphs_from_markdown(md):
    paragraphs = [p.strip() for p in md.strip().split('\n\n') if p.strip()]
    return [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": p}]
        }
        for p in paragraphs
    ]   

def textile_to_markdown_with_pandoc(textile_text):
    proc = subprocess.run(
        ['pandoc', '--from=textile', '--to=markdown'],
        input=textile_text.encode('utf-8'),
        stdout=subprocess.PIPE
    )
    return proc.stdout.decode('utf-8')

def adf_metadata_table(redmine_issue):
    fields = [
        ("Redmine ID", redmine_issue.get("id", "")),
        ("Author", redmine_issue.get("author", {}).get("name", "")),
        ("Status", redmine_issue.get("status", {}).get("name", "")),
        ("Tracker", redmine_issue.get("tracker", {}).get("name", "")),
        ("Priority", redmine_issue.get("priority", {}).get("name", "")),
        ("Assigned To", redmine_issue.get("assigned_to", {}).get("name", "")),
        ("Created", redmine_issue.get("created_on", "")),
        ("Updated", redmine_issue.get("updated_on", ""))
    ]
    rows = [
        [
            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(k)}]}]},
            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(v)}]}]}
        ]
        for k, v in fields
    ]
    return {
        "type": "table",
        "content": [
            {
                "type": "tableRow",
                "content": [
                    {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Field"}]}]},
                    {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Value"}]}]},
                ]
            }
        ] + [
            {"type": "tableRow", "content": row}
            for row in rows
        ]
    }

def attach_file_to_jira(issue_key, file_path):
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments",
            auth=auth,
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (filename, f)}
        )
        if resp.status_code in (200, 201):
            print(f"   📎 Uploaded fallback file: {filename}")
        else:
            print(f"   ⚠️ Failed to upload fallback file '{filename}': {resp.text}")

def create_jira_issue(redmine_issue):
    summary = redmine_issue.get('subject', 'No subject')
    description_textile = redmine_issue.get('description', '')
    description_markdown = ""
    if description_textile:
        preprocessed = preprocess_redmine_plaintext(description_textile)
        description_markdown = textile_to_markdown_with_pandoc(preprocessed)
    else:
        description_markdown = "No description."

    adf_content = []
    adf_content.append(adf_infobox("Migrated From bugs.RamSoft.com"))
    adf_content.append(adf_metadata_table(redmine_issue))    
    adf_content.extend(adf_paragraphs_from_markdown(description_markdown))

    # Always attach the .txt and comments.txt for each issue
    issue_id = redmine_issue.get('id')
    txt_path = os.path.join(redmine_issues_folder, f"issue_{issue_id}.txt")
    comments_txt_path = os.path.join(redmine_issues_folder, f"issue_{issue_id}_comments.txt")

    # Prepare main Jira issue payload
    priority = redmine_issue.get('priority', {}).get('name', 'Medium')
    priority_jira = priority_map.get(priority, "Medium")
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": adf_content
            },
            "issuetype": {"name": "Task"},
            "priority": {"name": priority_jira}
        }
    }

    resp = requests.post(
        f"{JIRA_URL}/rest/api/3/issue",
        auth=auth,
        headers={"Content-Type": "application/json"},
        json=payload
    )

    # Fallback: If description too long, only add metadata and summary
    if resp.status_code not in (200, 201) and "CONTENT_LIMIT_EXCEEDED" in resp.text:
        print(f"⚠️ Content limit exceeded, retrying with summary only for Redmine #{issue_id}")
        summary_short = description_markdown[:SUMMARY_CHAR_LIMIT] + ("..." if len(description_markdown) > SUMMARY_CHAR_LIMIT else "")
        adf_content_fallback = []
        adf_content_fallback.append(adf_infobox("Migrated From bugs.RamSoft.com"))
        adf_content_fallback.append(adf_metadata_table(redmine_issue))
        adf_content_fallback.extend(adf_paragraphs_from_markdown(summary_short))
        payload["fields"]["description"]["content"] = adf_content_fallback
        resp2 = requests.post(
            f"{JIRA_URL}/rest/api/3/issue",
            auth=auth,
            headers={"Content-Type": "application/json"},
            json=payload
        )
        if resp2.status_code in (200, 201):
            issue_key = resp2.json()["key"]
            print(f"✅ Created Jira issue (summary only): {issue_key} for Redmine #{issue_id}")
            # Attach both .txt and comments.txt as files
            if os.path.exists(txt_path):
                attach_file_to_jira(issue_key, txt_path)
            if os.path.exists(comments_txt_path):
                attach_file_to_jira(issue_key, comments_txt_path)
            return issue_key
        else:
            print(f"❌ Failed to create Jira issue (summary fallback) for Redmine #{issue_id}: {resp2.text}")
            return None
    elif resp.status_code in (200, 201):
        issue_key = resp.json()["key"]
        print(f"✅ Created Jira issue: {issue_key} for Redmine #{issue_id}")
        # Attach both .txt and comments.txt as files
        if os.path.exists(txt_path):
            attach_file_to_jira(issue_key, txt_path)
        if os.path.exists(comments_txt_path):
            attach_file_to_jira(issue_key, comments_txt_path)
        return issue_key
    else:
        print(f"❌ Failed to create Jira issue for Redmine #{issue_id}: {resp.text}")
        return None

def upload_attachments_to_jira(issue_key, attachment_folder):
    if not os.path.exists(attachment_folder):
        return
    files = [os.path.join(attachment_folder, f) for f in os.listdir(attachment_folder)]
    for file_path in files:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments",
                auth=auth,
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, f)}
            )
            if resp.status_code in (200, 201):
                print(f"   📎 Uploaded attachment: {filename}")
            else:
                print(f"   ⚠️ Failed to upload attachment '{filename}': {resp.text}")

def main():
    for fname in os.listdir(redmine_issues_folder):
        if fname.endswith(".json"):
            with open(os.path.join(redmine_issues_folder, fname), "r", encoding="utf-8") as f:
                redmine_issue = json.load(f)
            issue_key = create_jira_issue(redmine_issue)
            if issue_key:
                attachment_dir = os.path.join(
                    redmine_issues_folder, 
                    f"issue_{redmine_issue['id']}_attachments"
                )
                upload_attachments_to_jira(issue_key, attachment_dir)
            time.sleep(0.6)  # Polite delay

if __name__ == "__main__":
    main()
