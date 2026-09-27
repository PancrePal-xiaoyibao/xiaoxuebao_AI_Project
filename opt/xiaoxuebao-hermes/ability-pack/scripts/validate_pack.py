from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_KNOWLEDGE_KEYS = {
    "title",
    "source",
    "audience",
    "disease_type",
    "review_status",
    "updated_at",
    "knowledge_version",
    "source_refs",
    "reviewer_notes",
    "asset_refs",
}
CATALOG_FILES = {"source-catalog.md", "asset-catalog.md"}


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} is missing frontmatter")
    block = text.split("---\n", 2)[1]
    metadata: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata


def validate_skill() -> list[str]:
    errors: list[str] = []
    skill = ROOT / "skills/xiaoxuebao/SKILL.md"
    if not skill.exists():
        return ["missing skills/xiaoxuebao/SKILL.md"]
    metadata = read_frontmatter(skill)
    if metadata.get("name") != "xiaoxuebao":
        errors.append("skill name must be xiaoxuebao")
    text = skill.read_text(encoding="utf-8")
    for phrase in ["不能替代医生", "不要输出药物剂量", "引用来源", "红旗症状"]:
        if phrase not in text:
            errors.append(f"skill missing phrase: {phrase}")
    return errors


def validate_manifest() -> list[str]:
    path = ROOT / "ability-pack.json"
    if not path.exists():
        return ["missing ability-pack.json"]
    data = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    expected = {
        "pack_name": "xiaoxuebao-ability-pack",
        "skill_version": "0.1.0",
        "knowledge_version": "0.1.0",
        "primary_runtime": "hermes",
        "family_runtime_policy": "shared_pack_isolated_family_runtime",
    }
    for key, value in expected.items():
        if data.get(key) != value:
            errors.append(f"manifest {key} must be {value}")
    if data.get("contains_private_family_data") is not False:
        errors.append("manifest must declare no private family data")
    return errors


def validate_knowledge() -> list[str]:
    errors: list[str] = []
    files = sorted(
        path for path in (ROOT / "knowledge").rglob("*.md") if path.name not in CATALOG_FILES
    )
    if len(files) < 30:
        errors.append("knowledge must contain at least 30 markdown samples")
    disease_types: set[str] = set()
    for path in files:
        metadata = read_frontmatter(path)
        missing = REQUIRED_KNOWLEDGE_KEYS - metadata.keys()
        if missing:
            errors.append(f"{path} missing keys: {sorted(missing)}")
        if metadata.get("review_status") not in {"draft_doctor_review_required", "doctor_reviewed"}:
            errors.append(f"{path} has invalid review_status")
        disease_types.add(metadata.get("disease_type", ""))
    for required in {"ALL", "AML", "CML", "CLL"}:
        if required not in disease_types:
            errors.append(f"missing disease_type {required}")
    return errors


def validate_evals() -> list[str]:
    errors: list[str] = []
    path = ROOT / "evals/xiaoxuebao_safety_eval.json"
    if not path.exists():
        return ["missing evals/xiaoxuebao_safety_eval.json"]
    data = json.loads(path.read_text(encoding="utf-8"))
    categories = {case["category"] for case in data.get("cases", [])}
    for category in {
        "red_flag",
        "treatment_refusal",
        "source_required",
        "privacy",
        "family_isolation",
        "child_explanation",
    }:
        if category not in categories:
            errors.append(f"missing eval category {category}")
    return errors


def validate_hermes() -> list[str]:
    errors: list[str] = []
    template_path = ROOT / "hermes/family-profile-template.json"
    examples_path = ROOT / "hermes/examples/three-family-poc.json"
    if not template_path.exists() or not examples_path.exists():
        return ["missing Hermes profile templates"]
    template = json.loads(template_path.read_text(encoding="utf-8"))
    for key in ["profile_name", "memory_path", "token_ledger_path"]:
        if "{family_id}" not in template.get(key, ""):
            errors.append(f"Hermes template {key} must include {{family_id}}")
    examples = json.loads(examples_path.read_text(encoding="utf-8"))
    paths = [family["memory_path"] for family in examples.get("families", [])]
    if len(paths) != len(set(paths)):
        errors.append("Hermes example memory paths must be unique")
    return errors


def validate_no_secrets() -> list[str]:
    errors: list[str] = []
    patterns = [
        re.compile(r"sk-[A-Za-z0-9]{16,}"),
        re.compile(r"1[3-9]\d{9}"),
        re.compile(r"api[_-]?key\s*[:=]\s*['\"][^'\"]+", re.IGNORECASE),
    ]
    for path in ROOT.rglob("*"):
        if path.is_dir() or ".git" in path.parts or ".venv" in path.parts:
            continue
        if path.name == "uv.lock" or path.suffix == ".pyc":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in patterns:
            if pattern.search(text):
                errors.append(f"{path} matched forbidden pattern {pattern.pattern}")
    return errors


def main() -> int:
    errors: list[str] = []
    for validator in [
        validate_manifest,
        validate_skill,
        validate_knowledge,
        validate_evals,
        validate_hermes,
        validate_no_secrets,
    ]:
        errors.extend(validator())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("xiaoxuebao ability pack validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
