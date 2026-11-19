"""
openapi-breaking-change-diff-cli-lite
100% Python 3.9+ stdlib CLI tool for detecting breaking changes between two OpenAPI 3.x (JSON/YAML) specs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


@dataclass
class BreakingChange:
    category: str
    severity: str  # "BREAKING" | "WARNING"
    location: str
    message: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None


# ---------------------------------------------------------------------------
# Lightweight stdlib YAML 1.2 subset parser (for OpenAPI 3.x specs)
# ---------------------------------------------------------------------------

def _strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1].isspace():
                return line[:i]
    return line


def _parse_scalar(val: str) -> Any:
    val = val.strip()
    if val == "" or val in ("null", "Null", "NULL", "~"):
        return None
    if val in ("true", "True", "TRUE"):
        return True
    if val in ("false", "False", "FALSE"):
        return False
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_inline(inner)]
    if val.startswith("{") and val.endswith("}"):
        inner = val[1:-1].strip()
        if not inner:
            return {}
        res: Dict[str, Any] = {}
        for part in _split_inline(inner):
            if ":" in part:
                k, v = part.split(":", 1)
                res[str(_parse_scalar(k))] = _parse_scalar(v)
        return res
    if re.fullmatch(r"-?\d+", val):
        return int(val)
    if re.fullmatch(r"-?\d+\.\d+", val):
        return float(val)
    return val


def _split_inline(text: str) -> List[str]:
    parts: List[str] = []
    cur: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    for ch in text:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def parse_yaml_lite(text: str) -> Any:
    raw_lines = text.splitlines()
    lines: List[Tuple[int, str]] = []
    for raw in raw_lines:
        cleaned = _strip_yaml_comment(raw).rstrip()
        if not cleaned.strip():
            continue
        if cleaned.strip() in ("---", "..."):
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        lines.append((indent, cleaned.strip()))

    if not lines:
        return {}

    def parse_block(idx: int, min_indent: int) -> Tuple[Any, int]:
        if idx >= len(lines):
            return {}, idx
        indent, content = lines[idx]
        if content.startswith("- ") or content == "-":
            arr: List[Any] = []
            while idx < len(lines):
                c_indent, c_text = lines[idx]
                if c_indent < min_indent or not (c_text.startswith("- ") or c_text == "-"):
                    break
                if c_indent > indent:
                    break
                after = c_text[1:].strip()
                if not after:
                    val, idx = parse_block(idx + 1, c_indent + 1)
                    arr.append(val)
                elif ":" in after and not after.startswith("{") and not after.startswith('"') and not after.startswith("'"):
                    # Inline dict item in list, e.g. - name: id
                    k, v = after.split(":", 1)
                    item: Dict[str, Any] = {}
                    key = str(_parse_scalar(k))
                    v_str = v.strip()
                    if v_str:
                        item[key] = _parse_scalar(v_str)
                        idx += 1
                    else:
                        sub_val, idx = parse_block(idx + 1, c_indent + 2)
                        item[key] = sub_val
                    # Consume subsequent keys at deeper indent belonging to this list item
                    while idx < len(lines):
                        n_indent, n_text = lines[idx]
                        if n_indent <= c_indent or n_text.startswith("- "):
                            break
                        if ":" in n_text:
                            nk, nv = n_text.split(":", 1)
                            nkey = str(_parse_scalar(nk))
                            nv_str = nv.strip()
                            if nv_str:
                                item[nkey] = _parse_scalar(nv_str)
                                idx += 1
                            else:
                                sub_v, idx = parse_block(idx + 1, n_indent + 1)
                                item[nkey] = sub_v
                        else:
                            idx += 1
                    arr.append(item)
                else:
                    arr.append(_parse_scalar(after))
                    idx += 1
            return arr, idx
        else:
            obj: Dict[str, Any] = {}
            while idx < len(lines):
                c_indent, c_text = lines[idx]
                if c_indent < min_indent or c_text.startswith("- "):
                    break
                if c_indent > indent:
                    break
                if ":" not in c_text:
                    idx += 1
                    continue
                k, v = c_text.split(":", 1)
                key = str(_parse_scalar(k))
                v_str = v.strip()
                if v_str:
                    obj[key] = _parse_scalar(v_str)
                    idx += 1
                else:
                    if idx + 1 < len(lines) and lines[idx + 1][0] > c_indent:
                        sub_val, idx = parse_block(idx + 1, c_indent + 1)
                        obj[key] = sub_val
                    else:
                        obj[key] = {}
                        idx += 1
            return obj, idx

    result, _ = parse_block(0, lines[0][0])
    return result


def load_spec(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        data = json.loads(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = parse_yaml_lite(text)
    if not isinstance(data, dict):
        raise ValueError(f"Spec {path} must parse to a JSON/YAML object.")
    return data


# ---------------------------------------------------------------------------
# $ref resolution & schema helpers
# ---------------------------------------------------------------------------

def resolve_ref(spec: Dict[str, Any], obj: Any, seen: Optional[set] = None) -> Any:
    if seen is None:
        seen = set()
    if not isinstance(obj, dict):
        return obj
    ref = obj.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen:
            return obj
        seen.add(ref)
        parts = ref[2:].split("/")
        cur: Any = spec
        for p in parts:
            p = p.replace("~1", "/").replace("~0", "~")
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return obj
        return resolve_ref(spec, cur, seen)
    return obj


def _schema_type(spec: Dict[str, Any], schema: Any) -> Optional[str]:
    schema = resolve_ref(spec, schema)
    if not isinstance(schema, dict):
        return None
    t = schema.get("type")
    if isinstance(t, str):
        return t
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None


def _compare_schemas(
    old_spec: Dict[str, Any],
    new_spec: Dict[str, Any],
    old_schema: Any,
    new_schema: Any,
    location: str,
    context: str,  # "request" | "response"
    changes: List[BreakingChange],
) -> None:
    old_s = resolve_ref(old_spec, old_schema)
    new_s = resolve_ref(new_spec, new_schema)
    if not isinstance(old_s, dict) or not isinstance(new_s, dict):
        return

    old_type = _schema_type(old_spec, old_s)
    new_type = _schema_type(new_spec, new_s)

    if old_type and new_type and old_type != new_type:
        changes.append(
            BreakingChange(
                category="response_type_changed" if context == "response" else "request_type_changed",
                severity="BREAKING",
                location=location,
                message=f"{context.capitalize()} schema type changed from '{old_type}' to '{new_type}'.",
                old_value=old_type,
                new_value=new_type,
            )
        )
        return

    # Enum narrowing
    old_enum = old_s.get("enum")
    new_enum = new_s.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed_enum = [v for v in old_enum if v not in new_enum]
        if removed_enum:
            changes.append(
                BreakingChange(
                    category="enum_narrowed",
                    severity="BREAKING",
                    location=location,
                    message=f"Enum values removed: {removed_enum}.",
                    old_value=old_enum,
                    new_value=new_enum,
                )
            )

    # Object properties & required fields
    old_props = old_s.get("properties") or {}
    new_props = new_s.get("properties") or {}
    old_req = set(old_s.get("required") or [])
    new_req = set(new_s.get("required") or [])

    if context == "request":
        added_req = sorted(new_req - old_req)
        for field in added_req:
            changes.append(
                BreakingChange(
                    category="new_required_request_field",
                    severity="BREAKING",
                    location=f"{location}.{field}",
                    message=f"New required request field '{field}' added.",
                    old_value=False,
                    new_value=True,
                )
            )

    if isinstance(old_props, dict) and isinstance(new_props, dict):
        for prop_name, old_prop_schema in old_props.items():
            if prop_name in new_props:
                _compare_schemas(
                    old_spec,
                    new_spec,
                    old_prop_schema,
                    new_props[prop_name],
                    f"{location}.{prop_name}",
                    context,
                    changes,
                )
            elif context == "response":
                changes.append(
                    BreakingChange(
                        category="response_field_removed",
                        severity="BREAKING",
                        location=f"{location}.{prop_name}",
                        message=f"Response field '{prop_name}' was removed.",
                        old_value=prop_name,
                        new_value=None,
                    )
                )

    # Array items
    if old_type == "array" and "items" in old_s and "items" in new_s:
        _compare_schemas(
            old_spec,
            new_spec,
            old_s["items"],
            new_s["items"],
            f"{location}[]",
            context,
            changes,
        )


# ---------------------------------------------------------------------------
# Security schemes comparison
# ---------------------------------------------------------------------------

def _compare_security(
    old_spec: Dict[str, Any],
    new_spec: Dict[str, Any],
    changes: List[BreakingChange],
) -> None:
    old_schemes = (old_spec.get("components") or {}).get("securitySchemes") or {}
    new_schemes = (new_spec.get("components") or {}).get("securitySchemes") or {}

    for name, old_def in old_schemes.items():
        if name not in new_schemes:
            changes.append(
                BreakingChange(
                    category="security_scheme_removed",
                    severity="BREAKING",
                    location=f"components.securitySchemes.{name}",
                    message=f"Security scheme '{name}' was removed.",
                    old_value=old_def,
                    new_value=None,
                )
            )
        else:
            new_def = new_schemes[name]
            if isinstance(old_def, dict) and isinstance(new_def, dict):
                if old_def.get("type") != new_def.get("type") or old_def.get("scheme") != new_def.get("scheme"):
                    changes.append(
                        BreakingChange(
                            category="security_scheme_changed",
                            severity="BREAKING",
                            location=f"components.securitySchemes.{name}",
                            message=f"Security scheme '{name}' type/scheme changed.",
                            old_value=old_def,
                            new_value=new_def,
                        )
                    )

    old_global_sec = old_spec.get("security") or []
    new_global_sec = new_spec.get("security") or []
    if not old_global_sec and new_global_sec:
        changes.append(
            BreakingChange(
                category="security_strengthened",
                severity="BREAKING",
                location="security",
                message="Global security requirement added where none existed before.",
                old_value=old_global_sec,
                new_value=new_global_sec,
            )
        )
    elif old_global_sec and not new_global_sec:
        changes.append(
            BreakingChange(
                category="security_weakened",
                severity="WARNING",
                location="security",
                message="Global security requirement was removed (security weakened).",
                old_value=old_global_sec,
                new_value=new_global_sec,
            )
        )


# ---------------------------------------------------------------------------
# Main OpenAPI 3.x diff engine
# ---------------------------------------------------------------------------

def diff_specs(old_spec: Dict[str, Any], new_spec: Dict[str, Any]) -> List[BreakingChange]:
    changes: List[BreakingChange] = []

    _compare_security(old_spec, new_spec, changes)

    old_paths = old_spec.get("paths") or {}
    new_paths = new_spec.get("paths") or {}

    for path, old_path_item in old_paths.items():
        if path not in new_paths:
            changes.append(
                BreakingChange(
                    category="endpoint_removed",
                    severity="BREAKING",
                    location=path,
                    message=f"Endpoint '{path}' was removed.",
                    old_value=path,
                    new_value=None,
                )
            )
            continue

        new_path_item = new_paths[path]
        if not isinstance(old_path_item, dict) or not isinstance(new_path_item, dict):
            continue

        for method in HTTP_METHODS:
            if method not in old_path_item:
                continue
            if method not in new_path_item:
                changes.append(
                    BreakingChange(
                        category="method_removed",
                        severity="BREAKING",
                        location=f"{method.upper()} {path}",
                        message=f"HTTP method '{method.upper()}' on '{path}' was removed.",
                        old_value=method.upper(),
                        new_value=None,
                    )
                )
                continue

            old_op = old_path_item[method]
            new_op = new_path_item[method]
            op_loc = f"{method.upper()} {path}"

            # Parameters: new required parameters & enum narrowing
            old_params = {
                (p.get("name"), p.get("in")): resolve_ref(old_spec, p)
                for p in (old_path_item.get("parameters") or []) + (old_op.get("parameters") or [])
                if isinstance(p, dict)
            }
            new_params = {
                (p.get("name"), p.get("in")): resolve_ref(new_spec, p)
                for p in (new_path_item.get("parameters") or []) + (new_op.get("parameters") or [])
                if isinstance(p, dict)
            }

            for key, new_p in new_params.items():
                p_name, p_in = key
                if key not in old_params:
                    if new_p.get("required") is True:
                        changes.append(
                            BreakingChange(
                                category="new_required_parameter",
                                severity="BREAKING",
                                location=f"{op_loc} param:{p_name}({p_in})",
                                message=f"New required {p_in} parameter '{p_name}' added.",
                                old_value=None,
                                new_value=True,
                            )
                        )
                else:
                    old_p = old_params[key]
                    if not old_p.get("required") and new_p.get("required") is True:
                        changes.append(
                            BreakingChange(
                                category="parameter_became_required",
                                severity="BREAKING",
                                location=f"{op_loc} param:{p_name}({p_in})",
                                message=f"Parameter '{p_name}' ({p_in}) became required.",
                                old_value=False,
                                new_value=True,
                            )
                        )
                    if "schema" in old_p and "schema" in new_p:
                        _compare_schemas(
                            old_spec,
                            new_spec,
                            old_p["schema"],
                            new_p["schema"],
                            f"{op_loc} param:{p_name}",
                            "request",
                            changes,
                        )

            # RequestBody
            old_rb = resolve_ref(old_spec, old_op.get("requestBody"))
            new_rb = resolve_ref(new_spec, new_op.get("requestBody"))
            if isinstance(new_rb, dict) and new_rb.get("required") is True:
                if not isinstance(old_rb, dict) or not old_rb.get("required"):
                    changes.append(
                        BreakingChange(
                            category="request_body_became_required",
                            severity="BREAKING",
                            location=f"{op_loc} requestBody",
                            message="Request body became required.",
                            old_value=False,
                            new_value=True,
                        )
                    )
            if isinstance(old_rb, dict) and isinstance(new_rb, dict):
                old_content = old_rb.get("content") or {}
                new_content = new_rb.get("content") or {}
                for media_type, old_mt in old_content.items():
                    if media_type in new_content:
                        old_s = (old_mt or {}).get("schema")
                        new_s = (new_content[media_type] or {}).get("schema")
                        if old_s and new_s:
                            _compare_schemas(
                                old_spec,
                                new_spec,
                                old_s,
                                new_s,
                                f"{op_loc} requestBody[{media_type}]",
                                "request",
                                changes,
                            )

            # Responses: removed status codes & response schema changes
            old_resps = old_op.get("responses") or {}
            new_resps = new_op.get("responses") or {}
            for status_code, old_resp in old_resps.items():
                sc_str = str(status_code)
                if sc_str not in {str(k) for k in new_resps.keys()}:
                    changes.append(
                        BreakingChange(
                            category="status_code_removed",
                            severity="BREAKING",
                            location=f"{op_loc} response:{sc_str}",
                            message=f"Response status code '{sc_str}' was removed.",
                            old_value=sc_str,
                            new_value=None,
                        )
                    )
                    continue
                new_resp = new_resps.get(status_code) or new_resps.get(sc_str)
                old_r = resolve_ref(old_spec, old_resp)
                new_r = resolve_ref(new_spec, new_resp)
                if isinstance(old_r, dict) and isinstance(new_r, dict):
                    old_rc = old_r.get("content") or {}
                    new_rc = new_r.get("content") or {}
                    for media_type, old_mt in old_rc.items():
                        if media_type in new_rc:
                            old_s = (old_mt or {}).get("schema")
                            new_s = (new_rc[media_type] or {}).get("schema")
                            if old_s and new_s:
                                _compare_schemas(
                                    old_spec,
                                    new_spec,
                                    old_s,
                                    new_s,
                                    f"{op_loc} response:{sc_str}[{media_type}]",
                                    "response",
                                    changes,
                                )

    return changes


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_text(changes: List[BreakingChange]) -> str:
    if not changes:
        return "No breaking changes detected."
    lines = [f"Detected {len(changes)} change(s):"]
    for c in changes:
        lines.append(f"[{c.severity}] ({c.category}) {c.location}: {c.message}")
    return "\n".join(lines)


def format_json(changes: List[BreakingChange]) -> str:
    breaking_count = sum(1 for c in changes if c.severity == "BREAKING")
    payload = {
        "breaking_count": breaking_count,
        "total_count": len(changes),
        "changes": [asdict(c) for c in changes],
    }
    return json.dumps(payload, indent=2)


def format_markdown(changes: List[BreakingChange]) -> str:
    if not changes:
        return "## OpenAPI Diff Report\n\nNo breaking changes detected."
    lines = [
        "## OpenAPI Diff Report",
        "",
        f"Found **{len(changes)}** change(s):",
        "",
        "| Severity | Category | Location | Message |",
        "| --- | --- | --- | --- |",
    ]
    for c in changes:
        lines.append(f"| {c.severity} | `{c.category}` | `{c.location}` | {c.message} |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openapi-diff",
        description="Detect breaking changes between two OpenAPI 3.x (JSON/YAML) specifications.",
    )
    parser.add_argument("old_spec", type=Path, help="Path to the base/old OpenAPI 3.x spec (JSON or YAML)")
    parser.add_argument("new_spec", type=Path, help="Path to the target/new OpenAPI 3.x spec (JSON or YAML)")
    parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args(argv)

    old_spec = load_spec(args.old_spec)
    new_spec = load_spec(args.new_spec)
    changes = diff_specs(old_spec, new_spec)

    if args.format == "json":
        print(format_json(changes))
    elif args.format == "markdown":
        print(format_markdown(changes))
    else:
        print(format_text(changes))

    has_breaking = any(c.severity == "BREAKING" for c in changes)
    return 1 if has_breaking else 0


if __name__ == "__main__":
    sys.exit(main())
