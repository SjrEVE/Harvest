"""Form semantics verified from saved payloads; absent tables are not universally errors."""

import json
from dataclasses import dataclass

STRUCTURED_CODES = {"BD.DT.02." + code for code in ("1957", "1958", "1959", "1960", "2034", "2035", "2036", "2037")}
AUXILIARY_CODES = {"BD.DT.02.2102"}
DOCUMENT_CODES = {"BD.DT.02.3152", "BD.DT.02.3153"}


@dataclass(frozen=True)
class FormContent:
    code: str
    data: dict
    kind: str
    documents: tuple = ()
    warnings: tuple = ()
    problems: tuple = ()


def read_form(form):
    code = str(form.get("formCode") or "UNKNOWN")
    raw = form.get("formValue")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return FormContent(code, {}, "INVALID", problems=(f"FORM_{code}_INVALID_JSON",))
    if not isinstance(data, dict):
        return FormContent(code, {}, "INVALID", problems=(f"FORM_{code}_TABLE_MISSING_OR_INVALID",))
    warnings, problems, documents = [], [], []
    for field, kind in (("fileTech", "HSMT_TECHNICAL"), ("fileDoc", "HSMT_DOCUMENT")):
        value = data.get(field)
        if value is None:
            continue
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("fileId"), str)
            or not value["fileId"].strip()
        ):
            problems.append(f"FORM_{code}_{field}_REFERENCE_INVALID")
            continue
        name = value.get("fileName")
        if not isinstance(name, str) or not name.strip():
            name = value["fileId"]
            warnings.append(f"FORM_{code}_{field}_NAME_MISSING")
        documents.append(
            {
                "document_kind": kind,
                "source_file_id": value["fileId"],
                "name": name,
                "owner_kind": "NOTICE",
                "owner_key": form.get("notifyNo"),
                "owner_version": form.get("notifyVersion"),
                "source_path": f"{code}.formValue.{field}",
                "raw_reference": value,
                "download_status": "REFERENCE_ONLY",
                "download_reason": "DOWNLOAD_ENDPOINT_UNVERIFIED",
            }
        )
    # Shared strings are retained in raw. Their meaning/URL contract is not inferred.
    if data.get("sharedFiles"):
        warnings.append(f"FORM_{code}_SHARED_REFERENCES_UNRESOLVED")
    if code in STRUCTURED_CODES:
        kind = "STRUCTURED"
        if not isinstance(data.get("Table"), list):
            problems.append(f"FORM_{code}_TABLE_MISSING_OR_INVALID")
    elif code in AUXILIARY_CODES:
        kind = "AUXILIARY"
        warnings.append(f"AUXILIARY_FORM:{code}")
        if "Table" in data:
            problems.append(f"FORM_{code}_UNEXPECTED_AUXILIARY_TABLE")
    elif code in DOCUMENT_CODES:
        kind = "DOCUMENT"
        warnings.append("STRUCTURED_ITEMS_UNAVAILABLE")
        if "Table" in data:
            warnings.append(f"FORM_{code}_UNVERIFIED_DOCUMENT_TABLE")
    elif isinstance(data.get("Table"), list):
        kind = "UNVERIFIED_STRUCTURED" if any(isinstance(row,dict) and "name" in row and "qty" in row for row in data["Table"]) else "UNVERIFIED"
        warnings.append(f"UNREGISTERED_FORM_CODE:{code}")
    else:
        kind = "INVALID"
        problems.append(f"FORM_{code}_TABLE_MISSING_OR_INVALID")
    return FormContent(code, data, kind, tuple(documents), tuple(warnings), tuple(problems))
