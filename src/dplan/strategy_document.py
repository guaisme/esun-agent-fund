from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field, model_validator

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class StrategyDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    etf_name: str = Field(min_length=2, max_length=20)
    theme: str = Field(min_length=1, max_length=50)
    philosophy: str = Field(min_length=100, max_length=300)
    strategy_version: str
    model_version: str
    agent_generated: bool

    @model_validator(mode="after")
    def validate_content(self):
        if not self.etf_name.startswith("主動") or not re.fullmatch(
            r"[\u4e00-\u9fffA-Za-z]+", self.etf_name
        ):
            raise ValueError("ETF name must start with 主動, using Chinese or English letters only")
        return self


def fill_official_template(
    template: Path,
    destination: Path,
    declaration: StrategyDeclaration,
    *,
    allow_fixture: bool = False,
) -> dict:
    """Patch only the three empty right-hand cells, preserve every other ZIP part.

    No hard-coded claim that Gemini produced the text: actual trace is retained
    by the caller. Submission requires separately frozen strategy and visual QA.
    """
    if not declaration.agent_generated and not allow_fixture:
        raise ValueError("Official strategy text must have an actual Agent trace")
    if template.resolve() == destination.resolve():
        raise ValueError("Never overwrite organizer template")
    with ZipFile(template) as archive:
        contents = {item.filename: archive.read(item.filename) for item in archive.infolist()}
        infos = archive.infolist()
    # Read the structure, then patch original XML bytes. Never reserialize the
    # namespace root: mc:Ignorable can refer to otherwise unused namespace
    # declarations which generic XML serializers discard.
    tree = ET.fromstring(contents["word/document.xml"])
    table = tree.find(f".//{W}tbl")
    if table is None:
        raise ValueError("Official template table missing")
    rows = table.findall(f"{W}tr")
    if len(rows) != 3:
        raise ValueError("Official three-slot template changed")
    slots = {}
    for row, text in zip(rows, (declaration.etf_name, declaration.theme, declaration.philosophy)):
        cells = row.findall(f"{W}tc")
        if len(cells) != 2:
            raise ValueError("Official two-column template changed")
        cell = cells[1]
        if any(node.text for node in cell.iter(f"{W}t")):
            raise ValueError("Editable template slot is not empty")
        paragraph = cell.find(f"{W}p")
        if paragraph is None:
            raise ValueError("Template paragraph missing")
        para_id = paragraph.get("{http://schemas.microsoft.com/office/word/2010/wordml}paraId")
        if not para_id:
            raise ValueError("Stable editable paragraph ID missing")
        slots[para_id.encode()] = text
    changed = []

    def patch(match):
        start, body, end = match.groups()
        identifier = re.search(rb'w14:paraId="([^"]+)"', start)
        if not identifier or identifier.group(1) not in slots:
            return match.group(0)
        properties = re.search(rb"<w:rPr\b[^>]*>.*?</w:rPr>", body, flags=re.S)
        run = (
            b"<w:r>"
            + (properties.group(0) if properties else b"")
            + b"<w:t>"
            + escape(slots[identifier.group(1)]).encode("utf-8")
            + b"</w:t></w:r>"
        )
        changed.append(identifier.group(1))
        return start + body + run + end

    contents["word/document.xml"] = re.sub(
        rb"(<w:p\b[^>]*>)(.*?)(</w:p>)", patch, contents["word/document.xml"], flags=re.S
    )
    if len(changed) != 3 or len(set(changed)) != 3:
        raise ValueError("Did not patch exactly three official slots")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w") as archive:
        for info in infos:
            archive.writestr(info, contents[info.filename])
    with ZipFile(template) as source, ZipFile(destination) as final:
        for name in source.namelist():
            if name != "word/document.xml" and source.read(name) != final.read(name):
                raise ValueError("Unrelated template part changed")
    return {
        "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "document_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "only_modified_part": "word/document.xml",
        "strategy_version": declaration.strategy_version,
        "model_version": declaration.model_version,
        "agent_generated": declaration.agent_generated,
        "visual_qa": "NOT_TESTED",
    }


def freeze_declaration(
    declaration: StrategyDeclaration,
    path: Path,
    trace_path: Path,
    *,
    visual_qa_passed: bool = False,
) -> None:
    if not declaration.agent_generated or not trace_path.exists() or not visual_qa_passed:
        raise ValueError("Agent trace and completed visual QA required before freezing")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    if (
        trace.get("status") != "DECLARATION_PARSED"
        or trace.get("declaration") != declaration.model_dump()
        or trace.get("model_version") != declaration.model_version
    ):
        raise ValueError("Trace must contain the actual validated Agent declaration")
    value = {
        "declaration": declaration.model_dump(),
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        "strategy_version": declaration.strategy_version,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise ValueError("Frozen official strategy is immutable; no silent replacement")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
