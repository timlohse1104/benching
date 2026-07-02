"""HTML validation: strict parser pass + headless browser pass."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import html5lib
from bs4 import BeautifulSoup


@dataclass
class ValidationIssue:
    kind: str   # parse_error | missing_doctype | missing_tag | duplicate_id | js_error | page_error | network_error
    message: str
    line: int | None = None
    col: int | None = None


@dataclass
class ValidationResult:
    parse_issues: list[ValidationIssue] = field(default_factory=list)
    runtime_issues: list[ValidationIssue] = field(default_factory=list)
    thumbnail_path: str | None = None
    has_doctype: bool = False
    has_html: bool = False
    has_head: bool = False
    has_body: bool = False

    @property
    def status(self) -> str:
        if any(i.kind in {"js_error", "page_error"} for i in self.runtime_issues):
            return "broken"
        if self.parse_issues or self.runtime_issues:
            return "warnings"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "has_doctype": self.has_doctype,
            "has_html": self.has_html,
            "has_head": self.has_head,
            "has_body": self.has_body,
            "parse_issues": [i.__dict__ for i in self.parse_issues],
            "runtime_issues": [i.__dict__ for i in self.runtime_issues],
            "thumbnail": self.thumbnail_path,
        }


_DOCTYPE_RE = re.compile(r"<!doctype\s+html", re.IGNORECASE)


def extract_html(raw: str) -> tuple[str, bool]:
    """Pull an HTML document out of a raw LLM response.

    Returns (html, looks_like_html).
    Strips a single ```html ... ``` fence if present.
    """
    text = raw.strip()
    if not text:
        return "", False

    # Markdown fenced block: ```html ... ```
    fence = re.match(r"^```(?:html)?\s*\n(.*?)\n```\s*$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    looks_html = bool(_DOCTYPE_RE.search(text)) or text.lower().lstrip().startswith("<html")
    return text, looks_html


def parse_validate(html: str) -> ValidationResult:
    result = ValidationResult()

    result.has_doctype = bool(_DOCTYPE_RE.search(html))
    if not result.has_doctype:
        result.parse_issues.append(
            ValidationIssue(kind="missing_doctype", message="Document is missing <!DOCTYPE html>.")
        )

    # Strict parse with html5lib to capture parse errors.
    parser = html5lib.HTMLParser(strict=False)
    try:
        parser.parse(html)
    except Exception as exc:  # pragma: no cover - parser shouldn't raise with strict=False
        result.parse_issues.append(ValidationIssue(kind="parse_error", message=str(exc)))

    for err in parser.errors or []:
        # html5lib reports tuples: ((line, col), code, datavars)
        try:
            (line, col), code, _data = err
        except (ValueError, TypeError):
            line, col, code = None, None, "parse_error"
        result.parse_issues.append(
            ValidationIssue(kind="parse_error", message=str(code), line=line, col=col)
        )

    # Structural checks via BeautifulSoup
    soup = BeautifulSoup(html, "html5lib")
    result.has_html = soup.find("html") is not None
    result.has_head = soup.find("head") is not None
    result.has_body = soup.find("body") is not None
    if not result.has_html:
        result.parse_issues.append(ValidationIssue(kind="missing_tag", message="Missing <html> element."))
    if not result.has_head:
        result.parse_issues.append(ValidationIssue(kind="missing_tag", message="Missing <head> element."))
    if not result.has_body:
        result.parse_issues.append(ValidationIssue(kind="missing_tag", message="Missing <body> element."))

    # Duplicate IDs
    seen: dict[str, int] = {}
    for el in soup.find_all(attrs={"id": True}):
        seen[el["id"]] = seen.get(el["id"], 0) + 1
    for id_, count in seen.items():
        if count > 1:
            result.parse_issues.append(
                ValidationIssue(
                    kind="duplicate_id",
                    message=f'Duplicate id="{id_}" found {count} times.',
                )
            )

    return result


async def headless_validate(
    html_path: Path, thumbnail_path: Path, timeout_ms: int = 8000
) -> tuple[list[ValidationIssue], str | None]:
    """Load the HTML in headless Chromium, capture JS errors + screenshot."""
    issues: list[ValidationIssue] = []
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        issues.append(
            ValidationIssue(
                kind="page_error",
                message="playwright is not installed; skipping headless check.",
            )
        )
        return issues, None

    try:
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception as exc:
                issues.append(
                    ValidationIssue(
                        kind="page_error",
                        message=f"Could not launch headless Chromium ({exc}). Run: playwright install chromium",
                    )
                )
                return issues, None

            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()

            page.on(
                "console",
                lambda msg: issues.append(
                    ValidationIssue(kind="js_error", message=msg.text)
                )
                if msg.type == "error"
                else None,
            )
            page.on(
                "pageerror",
                lambda err: issues.append(
                    ValidationIssue(kind="page_error", message=str(err))
                ),
            )
            page.on(
                "requestfailed",
                lambda req: issues.append(
                    ValidationIssue(
                        kind="network_error",
                        message=f"{req.method} {req.url} failed: {req.failure}",
                    )
                ),
            )

            try:
                await page.goto(html_path.as_uri(), wait_until="load", timeout=timeout_ms)
                # Give scripts a brief moment after load to surface errors
                await page.wait_for_timeout(300)
                thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(thumbnail_path), full_page=False)
                thumb_value: str | None = thumbnail_path.name
            except Exception as exc:
                issues.append(
                    ValidationIssue(kind="page_error", message=f"Page load failed: {exc}")
                )
                thumb_value = None
            finally:
                await context.close()
                await browser.close()
        return issues, thumb_value
    except Exception as exc:  # pragma: no cover - last-resort guard
        issues.append(ValidationIssue(kind="page_error", message=str(exc)))
        return issues, None
