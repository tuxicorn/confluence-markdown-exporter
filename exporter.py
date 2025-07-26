import os
import re
import requests
from dotenv import load_dotenv
from atlassian import Confluence
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# Load environment variables
load_dotenv()
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL")
CONFLUENCE_USER = os.getenv("CONFLUENCE_USER")
CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_TOKEN")
SPACE = os.getenv("SPACE")

# Connect to Confluence
confluence = Confluence(
    url=CONFLUENCE_URL,
    username=CONFLUENCE_USER,
    password=CONFLUENCE_TOKEN,
)

# Output folders
output_dir = "exported_docs"
attachments_dir = os.path.join(output_dir, "attachments")
os.makedirs(output_dir, exist_ok=True)
os.makedirs(attachments_dir, exist_ok=True)

def fix_unclosed_tags(soup):
    for br in soup.find_all("br"):
        br.insert_after("\n")
    return soup

def fix_lists_in_table_cells_to_html_list(soup):
    for cell in soup.find_all(['td', 'th']):
        html = cell.decode_contents()
        if any(html.strip().startswith(c) for c in ["*", "-", "•"]) or "<br" in html:
            lines = re.split(r'<br\s*/?>', html)
            items = []
            for line in lines:
                line = line.strip()
                if line.startswith("*") or line.startswith("-") or line.startswith("•"):
                    item_html = re.sub(r'^[*\-•]\s*', '', line)
                    li = soup.new_tag("li")
                    li.string = item_html
                    items.append(li)
            if items:
                ul = soup.new_tag("ul")
                for li in items:
                    ul.append(li)
                cell.clear()
                cell.append(ul)
    return soup

def fix_table_column_alignment(markdown):
    lines = markdown.split("\n")
    fixed_lines = []
    inside_table = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            inside_table = True
        elif inside_table and not stripped:
            inside_table = False

        if inside_table:
            parts = [p.strip() for p in line.split("|")]
            while parts and parts[0] == '':
                parts = parts[1:]
            while parts and parts[-1] == '':
                parts = parts[:-1]
            fixed_lines.append('| ' + ' | '.join(parts) + ' |')
        else:
            fixed_lines.append(line)

    return "\n".join(fixed_lines)

def fix_spacing_between_tables_and_text(markdown):
    lines = markdown.split("\n")
    output = []
    prev_line_was_table = False

    for line in lines:
        if line.strip().startswith("|"):
            if not prev_line_was_table and output and output[-1].strip():
                output.append("")
            output.append(line)
            prev_line_was_table = True
        else:
            if prev_line_was_table and line.strip():
                output.append("")
            output.append(line)
            prev_line_was_table = False

    return "\n".join(output)

def generate_toc(markdown):
    toc = []
    for line in markdown.splitlines():
        match = re.match(r'^(#{1,6}) (.*)', line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if title.lower() == "table of contents":
                continue
            slug = re.sub(r'[^\w\- ]', '', title).lower().replace(' ', '-')
            toc.append(f"{'  ' * (level - 1)}* [{title}](#{slug})")
    return '\n'.join(toc)

def clean_existing_toc_and_wip_section(markdown):
    lines = markdown.splitlines()
    result = []
    inside_toc = False
    for line in lines:
        if inside_toc:
            if not line.startswith("*") and not line.startswith("  *"):
                inside_toc = False
        if not inside_toc:
            if re.match(r'^#+ Table of Contents$', line.strip(), re.IGNORECASE):
                inside_toc = True
                continue
            if line.strip().lower().startswith("work in progress"):
                continue
            if line.strip() == "61falseTable of Contentsnonelisttrue":
                continue
            result.append(line)
    return "\n".join(result)

def convert_html_to_markdown_with_fixes(html):
    soup = BeautifulSoup(html, 'html.parser')
    soup = fix_unclosed_tags(soup)
    soup = fix_lists_in_table_cells_to_html_list(soup)
    html = str(soup)
    markdown = md(
        html,
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="*",
        code_language_detection=False,
    )
    markdown = fix_table_column_alignment(markdown)
    markdown = fix_spacing_between_tables_and_text(markdown)
    markdown = clean_existing_toc_and_wip_section(markdown)
    return markdown

# Export each page
pages = confluence.get_all_pages_from_space(SPACE, start=0, limit=100, status='current')
for page in pages:
    page_id = page["id"]
    title = page["title"].replace('/', '_')
    print(f"Exporting: {title}")

    content = confluence.get_page_by_id(page_id, expand="body.storage")
    html = content["body"]["storage"]["value"]
    soup = BeautifulSoup(html, "html.parser")

    # Prepare attachment folder
    page_attachments_dir = os.path.join(attachments_dir, page_id)
    os.makedirs(page_attachments_dir, exist_ok=True)
    attachment_map = {}

    attachments = confluence.get_attachments_from_content(page_id, limit=1000)['results']
    for attachment in attachments:
        filename_raw = attachment['title']
        filename = filename_raw.replace(" ", "_")
        filename = re.sub(r"[^\w\-_\.]", "", filename)

        download_url = f"{CONFLUENCE_URL}/wiki{attachment['_links']['download']}"
        print(f"📎 Downloading via _links.download: {filename_raw} → {download_url}")

        try:
            r = requests.get(download_url, auth=(CONFLUENCE_USER, CONFLUENCE_TOKEN), stream=True)
            if r.status_code == 200:
                with open(os.path.join(page_attachments_dir, filename), "wb") as f_att:
                    for chunk in r.iter_content(chunk_size=8192):
                        f_att.write(chunk)
                print(f"✅ Saved: {filename}")
                attachment_map[filename_raw] = filename
            else:
                print(f"❌ Failed: {r.status_code} {r.reason}")
        except Exception as e:
            print(f"❌ Exception: {e}")

    # Replace <ac:image> with <img src=...>
    for img in soup.find_all("ac:image"):
        ri_attachment = img.find("ri:attachment")
        if ri_attachment and ri_attachment.has_attr("ri:filename"):
            original = ri_attachment["ri:filename"]
            filename = attachment_map.get(original)
            if filename:
                new_tag = soup.new_tag("img", src=f"./attachments/{page_id}/{filename}")
                img.replace_with(new_tag)

    # Convert fixed HTML to Markdown
    markdown = convert_html_to_markdown_with_fixes(str(soup))
    toc = generate_toc(markdown)

    # Write final Markdown file
    output_file = os.path.join(output_dir, f"{title}.md")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write("# Table of Contents\n\n")
        f.write(toc + "\n\n")
        f.write(markdown)
