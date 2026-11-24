"""Render the same eight report sections as text or a self-contained illustrated file."""

import base64
import html
import io
import math
from pathlib import Path

from PIL import Image

from .report_data import Figure, Table, build_report, valid_reference


def _md(value):
    return (
        str(value)
        .replace("\n", " ")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def markdown_report(state, spec, events):
    document = build_report(state, spec, events)
    lines = ["# " + _md(document.title), "", "本文件为文字版；现场图片展示于图文版报告中。", ""]
    for i, section in enumerate(document.sections, 1):
        lines += [f"## {i}. {section.title}", ""]
        for block in section.blocks:
            if isinstance(block, Table):
                lines.append("| " + " | ".join(_md(cell) for cell in block.headers) + " |")
                lines.append("|" + "---|" * len(block.headers))
                lines.extend("| " + " | ".join(_md(cell) for cell in row) + " |" for row in block.rows)
            elif isinstance(block, Figure):
                lines.append(f"**图{block.number}**　{_md(block.caption)}")
            else:
                lines.append(_md(block))
            lines.append("")
    return "\n".join(lines)


def _image_bytes(figure, image_dir):
    if image_dir is None or not valid_reference(figure.reference):
        return None
    root = Path(image_dir).resolve()
    path = (root / (figure.reference[6:] + ".jpg")).resolve()
    try:
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 4_000_000:
            return None
        raw = path.read_bytes()
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG"} or image.width * image.height > 12_000_000:
                return None
            mime = "image/jpeg" if image.format == "JPEG" else "image/png"
            if figure.roi:
                if len(figure.roi) != 4 or any(
                    not isinstance(v, (int, float)) or not math.isfinite(v) for v in figure.roi
                ):
                    return None
                x, y, w, h = figure.roi
                if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.00001 or y + h > 1.00001:
                    return None
                left, top = round(x * image.width), round(y * image.height)
                right, bottom = round((x + w) * image.width), round((y + h) * image.height)
                if right <= left or bottom <= top:
                    return None
                output = io.BytesIO()
                image.crop((left, top, right, bottom)).save(output, format="PNG")
                raw, mime = output.getvalue(), "image/png"
        return (mime, raw) if len(raw) <= 4_000_000 else None
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


STYLE = """
*{box-sizing:border-box}body{margin:0;color:#203338;background:#f4f6f5;font:15px/1.8 'Microsoft YaHei','Noto Sans CJK SC',sans-serif}
main{max-width:1000px;margin:28px auto;padding:38px 45px;background:white;border:1px solid #e0e7e4}
h1{font-size:25px;line-height:1.5;margin:0 0 20px;color:#173e36}h2{font-size:18px;margin:30px 0 13px;border-bottom:2px solid #d9e9e1;padding-bottom:7px;break-after:avoid}
p{margin:9px 0}table{width:100%;border-collapse:collapse;margin:13px 0 18px;font-size:13px;table-layout:auto}th,td{border:1px solid #dce4e0;padding:8px 10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:#eff5f1;font-weight:600}tbody tr:nth-child(even){background:#fafcfb}thead{display:table-header-group}tr{break-inside:avoid}
figure{margin:18px auto 23px;max-width:700px;break-inside:avoid}figure img{display:block;max-width:100%;max-height:330px;width:auto;height:auto;margin:auto;border:1px solid #e1e7e4;object-fit:contain}
figcaption{font-size:12px;color:#53665e;text-align:center;margin-top:7px}.missing{padding:18px;background:#f6f7f6;color:#66746d;text-align:center}.replay{background:#fff4dc;border-left:4px solid #b87916;padding:10px 15px;margin-bottom:20px}
@media(max-width:700px){main{margin:0;padding:22px 16px;border:0}h1{font-size:21px}table{font-size:12px}td,th{padding:6px}figure img{max-height:260px}}
@media print{@page{size:A4;margin:16mm}body{background:white;font-size:10.5pt}main{max-width:none;margin:0;padding:0;border:0}h1{font-size:18pt}h2{font-size:13pt;margin-top:18pt}table{font-size:9pt}figure img{max-height:65mm}figcaption{font-size:9pt}}
"""


def html_report(state, spec, events, image_dir=None):
    document = build_report(state, spec, events)
    escape = html.escape
    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>" + escape(document.title) + "</title><style>" + STYLE + "</style></head><body><main>",
        "<h1>" + escape(document.title) + "</h1>",
    ]
    if document.replay:
        parts.append('<p class="replay">软件演练报告 · 非真实测量。以下数据用于记录软件流程。</p>')
    remaining = 12_000_000
    for i, section in enumerate(document.sections, 1):
        parts.append(f"<section><h2>{i}. {escape(section.title)}</h2>")
        for block in section.blocks:
            if isinstance(block, Table):
                parts.append(
                    "<table><thead><tr>"
                    + "".join("<th>" + escape(v) + "</th>" for v in block.headers)
                    + "</tr></thead><tbody>"
                )
                for row in block.rows:
                    parts.append("<tr>" + "".join("<td>" + escape(str(v)) + "</td>" for v in row) + "</tr>")
                parts.append("</tbody></table>")
            elif isinstance(block, Figure):
                parts.append(f'<figure id="figure-{block.number}">')
                image = _image_bytes(block, image_dir)
                if image and len(image[1]) <= remaining:
                    mime, raw = image
                    remaining -= len(raw)
                    encoded = base64.b64encode(raw).decode("ascii")
                    parts.append(
                        f'<img src="data:{mime};base64,{encoded}" alt="{escape(block.caption, quote=True)}">'
                    )
                else:
                    parts.append('<div class="missing">该画面未能载入，表中保留相应记录。</div>')
                parts.append(f"<figcaption>图{block.number}　{escape(block.caption)}</figcaption></figure>")
            else:
                parts.append("<p>" + escape(block) + "</p>")
        parts.append("</section>")
    parts.append("</main></body></html>")
    return "\n".join(parts)
