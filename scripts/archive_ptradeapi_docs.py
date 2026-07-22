"""Archive ptradeapi.com documentation as local Markdown chapters."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import time
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


BINARY_SUFFIXES = {
    ".7z",
    ".apk",
    ".css",
    ".doc",
    ".docx",
    ".exe",
    ".ico",
    ".js",
    ".pdf",
    ".rar",
    ".tar",
    ".xls",
    ".xlsx",
    ".zip",
}
IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
SKIP_TAGS = {"script", "style", "noscript", "iframe"}
BLOCK_TAGS = {
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
}


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = "https" if parsed.scheme in {"", "http", "https"} else parsed.scheme
    netloc = parsed.netloc or "ptradeapi.com"
    path = parsed.path or "/"
    if path == "/":
        path = "/index.html"
    return urlunparse((scheme, netloc.lower(), path, "", parsed.query, ""))


def is_html_page(url: str, allowed_host: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != allowed_host:
        return False
    suffix = Path(parsed.path).suffix.lower()
    if suffix in BINARY_SUFFIXES or suffix in IMAGE_SUFFIXES:
        return False
    return suffix in {"", ".html", ".htm"}


def slugify(text: str, fallback: str = "section") -> str:
    text = re.sub(r"\s+", "-", text.strip().lower())
    text = re.sub(r"[\\/:*?\"<>|#%&{}$!`'@+=,;()\[\]]+", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return text[:80] or fallback


def clean_text(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def indent(text: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def inline_markdown(node: Tag | NavigableString, page_url: str, image_map: dict[str, str]) -> str:
    if isinstance(node, NavigableString):
        return clean_text(str(node))
    if not isinstance(node, Tag):
        return ""
    if node.name in SKIP_TAGS:
        return ""
    if node.name == "br":
        return "\n"
    if node.name in {"strong", "b"}:
        return f"**{children_inline(node, page_url, image_map)}**"
    if node.name in {"em", "i"}:
        return f"*{children_inline(node, page_url, image_map)}*"
    if node.name == "code":
        return f"`{node.get_text('', strip=False).strip()}`"
    if node.name == "a":
        text = children_inline(node, page_url, image_map) or clean_text(
            node.get_text(" ", strip=True)
        )
        href = node.get("href")
        if not href:
            return text
        return f"[{text}]({urljoin(page_url, href)})"
    if node.name == "img":
        src = node.get("src")
        alt = clean_text(node.get("alt") or node.get("title") or "image")
        if not src:
            return ""
        absolute = urljoin(page_url, src)
        local = image_map.get(absolute, absolute)
        return f"![{alt}]({local})"
    return children_inline(node, page_url, image_map)


def children_inline(node: Tag, page_url: str, image_map: dict[str, str]) -> str:
    parts = [inline_markdown(child, page_url, image_map) for child in node.children]
    text = " ".join(part for part in parts if part)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def table_markdown(table: Tag, page_url: str, image_map: dict[str, str]) -> str:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if cells:
            rows.append(
                [children_inline(cell, page_url, image_map).replace("\n", " ") for cell in cells]
            )
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def block_markdown(node: Tag | NavigableString, page_url: str, image_map: dict[str, str]) -> str:
    if isinstance(node, NavigableString):
        return clean_text(str(node))
    if not isinstance(node, Tag) or node.name in SKIP_TAGS:
        return ""
    name = node.name
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(name[1])
        text = children_inline(node, page_url, image_map)
        return f"{'#' * level} {text}" if text else ""
    if name == "p":
        return children_inline(node, page_url, image_map)
    if name in {"div", "section", "article"}:
        if not node.find(BLOCK_TAGS):
            return children_inline(node, page_url, image_map)
        child_blocks = [block_markdown(child, page_url, image_map) for child in node.children]
        child_blocks = [part for part in child_blocks if part]
        if child_blocks:
            return "\n\n".join(child_blocks)
        return children_inline(node, page_url, image_map)
    if name in {"ul", "ol"}:
        ordered = name == "ol"
        lines = []
        for idx, li in enumerate(node.find_all("li", recursive=False), 1):
            marker = f"{idx}." if ordered else "-"
            text = block_markdown(li, page_url, image_map).strip()
            if "\n" in text:
                first, rest = text.split("\n", 1)
                lines.append(f"{marker} {first}\n{indent(rest)}")
            elif text:
                lines.append(f"{marker} {text}")
        return "\n".join(lines)
    if name == "li":
        parts = [block_markdown(child, page_url, image_map) for child in node.children]
        return "\n".join(part for part in parts if part)
    if name == "pre":
        code = node.get_text("", strip=False).strip("\n")
        return f"```\n{code}\n```"
    if name == "blockquote":
        text = "\n\n".join(
            part
            for part in (block_markdown(child, page_url, image_map) for child in node.children)
            if part
        )
        return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())
    if name == "table":
        return table_markdown(node, page_url, image_map)
    if name == "img":
        return inline_markdown(node, page_url, image_map)
    return inline_markdown(node, page_url, image_map)


def content_root(soup: BeautifulSoup) -> Tag:
    root = soup.select_one(".markdown-body")
    return root if root is not None else soup.body or soup


def collect_page_links(soup: BeautifulSoup, page_url: str, allowed_host: str) -> list[str]:
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        raw = anchor.get("href")
        if not raw or raw.startswith(("javascript:", "mailto:", "tel:")):
            continue
        absolute = normalize_url(urldefrag(urljoin(page_url, raw))[0])
        if is_html_page(absolute, allowed_host):
            links.append(absolute)
    return links


def collect_images(root: Tag, page_url: str) -> list[str]:
    images = []
    for img in root.find_all("img", src=True):
        images.append(normalize_url(urljoin(page_url, img["src"])))
    return images


def download_image(session: requests.Session, url: str, asset_dir: Path) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".bin"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    stem = slugify(Path(parsed.path).stem, "image")
    filename = f"{stem}-{digest}{suffix}"
    target = asset_dir / filename
    if not target.exists():
        response = session.get(url, timeout=30)
        response.raise_for_status()
        target.write_bytes(response.content)
    return f"../assets/{filename}"


def split_sections(root: Tag) -> list[tuple[str, list[Tag | NavigableString]]]:
    sections: list[tuple[str, list[Tag | NavigableString]]] = []
    current_title = "introduction"
    current_nodes: list[Tag | NavigableString] = []
    saw_h1 = False
    for child in root.children:
        if isinstance(child, NavigableString) and not clean_text(str(child)):
            continue
        if isinstance(child, Tag) and child.name == "h1":
            if current_nodes:
                sections.append((current_title, current_nodes))
            current_title = clean_text(child.get_text(" ", strip=True)) or "untitled"
            current_nodes = [child]
            saw_h1 = True
        else:
            current_nodes.append(child)
    if current_nodes:
        sections.append((current_title, current_nodes))
    if saw_h1:
        return sections
    return [
        (
            clean_text(root.find(["h2", "h3"]).get_text(" ", strip=True))
            if root.find(["h2", "h3"])
            else "page",
            list(root.children),
        )
    ]


def write_page(
    session: requests.Session,
    page_url: str,
    html: str,
    out_dir: Path,
    asset_dir: Path,
) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    root = content_root(soup)
    image_map: dict[str, str] = {}
    for image_url in collect_images(root, page_url):
        try:
            image_map[image_url] = download_image(session, image_url, asset_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"image failed: {image_url} ({exc})")

    parsed = urlparse(page_url)
    if parsed.path in {"", "/", "/index.html"}:
        page_stem = "index"
    else:
        page_parts = [part for part in Path(parsed.path).with_suffix("").parts if part != "/"]
        page_stem = slugify("-".join(page_parts), "page")
    page_dir = out_dir / page_stem
    page_dir.mkdir(parents=True, exist_ok=True)
    title = clean_text((soup.title.get_text(" ", strip=True) if soup.title else page_stem))
    sections = split_sections(root)
    records: list[dict[str, str]] = []

    for idx, (section_title, nodes) in enumerate(sections, 1):
        section_slug = slugify(section_title, f"section-{idx:03d}")
        filename = f"{idx:03d}-{section_slug}.md"
        markdown_parts = [
            f"<!-- Source: {page_url} -->",
            f"<!-- Page: {title} -->",
            "",
        ]
        markdown_parts.extend(
            part for part in (block_markdown(node, page_url, image_map) for node in nodes) if part
        )
        markdown = "\n\n".join(markdown_parts).strip() + "\n"
        (page_dir / filename).write_text(markdown, encoding="utf-8")
        records.append(
            {
                "page": page_stem,
                "title": section_title,
                "path": f"{page_stem}/{filename}",
                "source": page_url,
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-url", default="https://ptradeapi.com/")
    parser.add_argument("--out-dir", default="docs/ptradeapi")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    asset_dir = out_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    allowed_host = urlparse(normalize_url(args.start_url)).netloc.lower()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 ptradeapi-doc-archiver"})
    queue = deque([normalize_url(urldefrag(args.start_url)[0])])
    seen: set[str] = set()
    records: list[dict[str, str]] = []

    while queue and len(seen) < args.max_pages:
        url = queue.popleft()
        if url in seen or not is_html_page(url, allowed_host):
            continue
        seen.add(url)
        print(f"fetch {len(seen):03d}: {url}")
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            print(f"page failed: {url} ({exc})")
            continue
        html = response.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")
        records.extend(write_page(session, url, html, out_dir, asset_dir))
        for link in collect_page_links(soup, url, allowed_host):
            if link not in seen:
                queue.append(link)
        time.sleep(args.delay)

    readme_lines = [
        "# Ptrade API Local Archive",
        "",
        f"- Start URL: {args.start_url}",
        f"- Pages fetched: {len(seen)}",
        f"- Markdown chapters: {len(records)}",
        "",
        "## Chapters",
        "",
    ]
    for record in records:
        readme_lines.append(f"- [{record['page']} / {record['title']}]({record['path']})")
    (out_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    print(f"done: {len(seen)} pages, {len(records)} chapters -> {out_dir}")


if __name__ == "__main__":
    main()
