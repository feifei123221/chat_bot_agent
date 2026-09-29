"""Current-task file and image input handling.

Attachments are parsed for the active request only. They are not written to
long-term memory unless a later, explicit persistence flow is added.
"""

import base64
import csv
import io
import mimetypes
import os
from typing import Any, TypedDict


class Attachment(TypedDict, total=False):
    name: str
    mime_type: str
    kind: str
    text: str
    data_url: str
    size: int


TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _read_text(data: bytes, suffix: str) -> str:
    text = data.decode("utf-8-sig", errors="replace")
    if suffix != ".csv":
        return text.strip()
    rows = list(csv.reader(io.StringIO(text)))
    return "\n".join(" | ".join(row) for row in rows).strip()


def _read_optional_document(data: bytes, suffix: str) -> str:
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if suffix == ".docx":
        from docx import Document

        document = Document(io.BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
    if suffix in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines: list[str] = []
        for worksheet in workbook.worksheets:
            lines.append(f"[{worksheet.title}]")
            for row in worksheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    lines.append(" | ".join(values))
        return "\n".join(lines).strip()
    raise ValueError(f"暂不支持读取文件类型: {suffix}")


def extract_uploaded_file(uploaded_file: Any, max_bytes: int = 10 * 1024 * 1024) -> Attachment:
    """Extract a Streamlit UploadedFile-like object for the current request."""
    name = os.path.basename(str(uploaded_file.name))
    data = uploaded_file.getvalue()
    if len(data) > max_bytes:
        raise ValueError(f"文件 {name} 超过 10 MB 限制。")
    mime_type = getattr(uploaded_file, "type", None) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    suffix = os.path.splitext(name)[1].lower()

    if suffix in IMAGE_EXTENSIONS or mime_type.startswith("image/"):
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "name": name,
            "mime_type": mime_type,
            "kind": "image",
            "data_url": f"data:{mime_type};base64,{encoded}",
            "size": len(data),
        }

    if suffix in TEXT_EXTENSIONS:
        text = _read_text(data, suffix)
    elif suffix in {".pdf", ".docx", ".xlsx", ".xlsm"}:
        text = _read_optional_document(data, suffix)
    else:
        raise ValueError(f"暂不支持读取文件类型: {suffix or '未知'}。")

    return {
        "name": name,
        "mime_type": mime_type,
        "kind": "text",
        "text": text,
        "size": len(data),
    }


def build_attachment_context(attachments: list[Attachment], prompt: str) -> str | list[dict[str, Any]]:
    """Build a text or multimodal message for an OpenAI-compatible API."""
    text_parts = [prompt.strip()] if prompt.strip() else []
    image_attachments: list[Attachment] = []
    for attachment in attachments:
        if attachment.get("kind") == "image":
            image_attachments.append(attachment)
            text_parts.append(f"请分析附件图片：{attachment['name']}")
        else:
            text_parts.append(
                f"附件文件：{attachment['name']}\n文件内容：\n{attachment.get('text', '')}"
            )

    text_content = "\n\n".join(text_parts)
    if not image_attachments:
        return text_content
    content: list[dict[str, Any]] = [{"type": "text", "text": text_content}]
    content.extend(
        {
            "type": "image_url",
            "image_url": {"url": attachment["data_url"]},
        }
        for attachment in image_attachments
    )
    return content
