from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG_FILES = {"source-catalog.md", "asset-catalog.md"}


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with YAML-like frontmatter"
    block = text.split("---\n", 2)[1]
    metadata: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata


def test_expected_top_level_directories_exist():
    for relative in [
        "skills/xiaoxuebao",
        "knowledge",
        "workflows",
        "evals",
        "hermes",
        "adapters",
        "scripts",
    ]:
        assert (ROOT / relative).is_dir(), relative


def test_manifest_declares_shared_pack_versions_and_runtime_policy():
    manifest = json.loads((ROOT / "ability-pack.json").read_text(encoding="utf-8"))

    assert manifest["pack_name"] == "xiaoxuebao-ability-pack"
    assert manifest["skill_version"] == "0.1.0"
    assert manifest["knowledge_version"] == "0.1.0"
    assert manifest["primary_runtime"] == "hermes"
    assert manifest["family_runtime_policy"] == "shared_pack_isolated_family_runtime"


def test_skill_has_required_metadata_and_medical_boundaries():
    skill = ROOT / "skills/xiaoxuebao/SKILL.md"
    metadata = read_frontmatter(skill)
    text = skill.read_text(encoding="utf-8")

    assert metadata["name"] == "xiaoxuebao"
    assert "白血病" in metadata["description"]
    assert "不能替代医生" in text
    assert "不要输出药物剂量" in text
    assert "引用来源" in text
    assert "红旗症状" in text


def test_knowledge_samples_have_required_metadata_and_public_scope():
    files = sorted(
        path for path in (ROOT / "knowledge").rglob("*.md") if path.name not in CATALOG_FILES
    )
    assert len(files) >= 30
    required = {
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
    disease_types = set()

    for path in files:
        metadata = read_frontmatter(path)
        assert required <= metadata.keys(), path
        assert metadata["review_status"] in {"draft_doctor_review_required", "doctor_reviewed"}
        assert "原文全文" not in path.read_text(encoding="utf-8")
        disease_types.add(metadata["disease_type"])

    assert {"ALL", "AML", "CML", "CLL"} <= disease_types


def test_source_and_asset_catalogs_exist_for_family_mvp():
    source_catalog = ROOT / "knowledge/source-catalog.md"
    asset_catalog = ROOT / "knowledge/asset-catalog.md"

    assert source_catalog.exists()
    assert asset_catalog.exists()

    source_text = source_catalog.read_text(encoding="utf-8")
    asset_text = asset_catalog.read_text(encoding="utf-8")
    for phrase in ["NCI Childhood ALL PDQ", "COG Family Handbook", "CSCO"]:
        assert phrase in source_text
    for phrase in ["小雪宝的温暖魔法", "发热", "PICC", "饮食安全"]:
        assert phrase in asset_text


def test_evals_cover_safety_sources_privacy_family_isolation_and_child_style():
    evals = json.loads((ROOT / "evals/xiaoxuebao_safety_eval.json").read_text(encoding="utf-8"))
    categories = {item["category"] for item in evals["cases"]}

    assert evals["skill_version"] == "0.1.0"
    assert {
        "red_flag",
        "treatment_refusal",
        "source_required",
        "privacy",
        "family_isolation",
        "child_explanation",
    } <= categories


def test_hermes_templates_keep_family_state_isolated():
    family_template = json.loads((ROOT / "hermes/family-profile-template.json").read_text("utf-8"))
    examples = json.loads((ROOT / "hermes/examples/three-family-poc.json").read_text("utf-8"))

    assert "{family_id}" in family_template["profile_name"]
    assert "{family_id}" in family_template["memory_path"]
    assert "{family_id}" in family_template["token_ledger_path"]

    memory_paths = {family["memory_path"] for family in examples["families"]}
    token_paths = {family["token_ledger_path"] for family in examples["families"]}
    assert len(memory_paths) == 3
    assert len(token_paths) == 3


def test_no_public_pack_secrets_or_real_contact_data():
    forbidden = [
        re.compile(r"sk-[A-Za-z0-9]{16,}"),
        re.compile(r"1[3-9]\d{9}"),
        re.compile(r"password\s*[:=]", re.IGNORECASE),
        re.compile(r"api[_-]?key\s*[:=]\s*['\"][^'\"]+", re.IGNORECASE),
    ]
    for path in ROOT.rglob("*"):
        if path.is_dir() or ".git" in path.parts or ".venv" in path.parts:
            continue
        if path.name in {"uv.lock"} or path.suffix in {".pyc"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in forbidden:
            assert not pattern.search(text), f"{path} matched {pattern.pattern}"


def test_pack_validator_passes():
    result = subprocess.run(
        [sys.executable, "scripts/validate_pack.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
