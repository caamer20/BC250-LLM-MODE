"""Private PDF extraction child. Receives bytes on stdin, emits bounded JSON.

Invoked by fixed argv from DocumentService. Never imports the application,
executes document scripts, follows links, or logs document text. Linux memory,
CPU, output-file and descriptor limits apply before importing the PDF parser.
"""
import io
import json
import logging
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (8, 9))
    resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024, 512 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    logging.disable(logging.CRITICAL)
    try:
        from pypdf import PdfReader, filters
    except ImportError:
        return {"error": "unavailable"}
    filters.ZLIB_MAX_OUTPUT_LENGTH = 4 * 1024 * 1024
    filters.LZW_MAX_OUTPUT_LENGTH = 4 * 1024 * 1024
    filters.JBIG2_MAX_OUTPUT_LENGTH = 4 * 1024 * 1024
    try:
        raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024 or not raw.startswith(b"%PDF-"):
            return {"error": "invalid"}
        reader = PdfReader(io.BytesIO(raw), strict=True, root_object_recovery_limit=1000)
        if reader.is_encrypted:
            return {"error": "encrypted"}
        count = len(reader.pages)
        if not 1 <= count <= 40:
            return {"error": "pages"}
        parts, total, has_text = [], 0, False
        for index, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > 4 * 1024 * 1024:
                return {"error": "stream_size"}
            text = page.extract_text() or ""
            has_text = has_text or bool(text.strip())
            part = f"[Page {index}]\n{text}\n"
            total += len(part.encode("utf-8"))
            if total > 64 * 1024:
                return {"error": "text_size"}
            parts.append(part)
        if not has_text:
            return {"error": "no_text"}
        return {"text": "\n".join(parts), "pages": count}
    except Exception:
        return {"error": "invalid"}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=True))
