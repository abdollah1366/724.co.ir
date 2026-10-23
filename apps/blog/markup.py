"""تبدیل متن ساده مقاله به HTML امن.

متن ابتدا کامل escape می‌شود، پس نویسنده نمی‌تواند HTML یا اسکریپت وارد کند؛ فقط چند قالب ساده پشتیبانی می‌شود.
"""

import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

BOLD = re.compile(r"\*\*(.+?)\*\*")
LINK = re.compile(r"\[([^\]]+)\]\(((?:https?://|/(?!/))[^\s)]*)\)")
ORDERED = re.compile(r"^[0-9۰-۹]+[.)]\s+")
IMAGE = re.compile(r"^\[(?:تصویر|image)\s*([0-9۰-۹]+)\]$")
FA_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _inline(text):
    text = BOLD.sub(r"<strong>\1</strong>", escape(text))

    def link(match):
        label, url = match.groups()
        external = url.startswith("http")
        rel = ' rel="nofollow noopener" target="_blank"' if external else ""
        return f'<a href="{url}"{rel}>{label}</a>'

    return LINK.sub(link, text)


def headings(body):
    """عنوان‌های ## برای فهرست مطالب: [(شناسه، متن)]."""
    result = []
    for line in body.splitlines():
        if line.startswith("## "):
            result.append((f"s{len(result) + 1}", line[3:].strip()))
    return result


def _figure(image):
    caption = f"<figcaption>{escape(image.caption)}</figcaption>" if image.caption else ""
    return (f'<figure class="post-figure"><img src="{escape(image.image.url)}" alt="{escape(image.alt)}" loading="lazy">'
            f"{caption}</figure>")


def render(body, images=None):
    """images: {شماره: PostImage} برای جای‌گذاری «[تصویر ۱]»؛ شماره ناموجود نادیده گرفته می‌شود."""
    images = images or {}
    html = []
    section = 0
    for block in re.split(r"\n\s*\n", body.replace("\r\n", "\n").strip()):
        lines = [line.rstrip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        first = lines[0]
        if first.startswith("### "):
            html.append(f"<h3>{_inline(first[4:])}</h3>")
            lines = lines[1:]
        elif first.startswith("## "):
            section += 1
            html.append(f'<h2 id="s{section}">{_inline(first[3:])}</h2>')
            lines = lines[1:]
        if not lines:
            continue
        match = IMAGE.match(lines[0].strip()) if len(lines) == 1 else None
        if match:
            image = images.get(int(match.group(1).translate(FA_TO_EN)))
            if image:
                html.append(_figure(image))
            continue
        if all(line.startswith("- ") for line in lines):
            items = "".join(f"<li>{_inline(line[2:])}</li>" for line in lines)
            html.append(f"<ul>{items}</ul>")
        elif all(ORDERED.match(line) for line in lines):
            items = "".join(f"<li>{_inline(ORDERED.sub('', line))}</li>" for line in lines)
            html.append(f"<ol>{items}</ol>")
        elif all(line.startswith(">") for line in lines):
            text = "<br>".join(_inline(line.lstrip("> ")) for line in lines)
            html.append(f'<aside class="callout">{text}</aside>')
        else:
            html.append("<p>" + "<br>".join(_inline(line) for line in lines) + "</p>")
    return mark_safe("\n".join(html))
