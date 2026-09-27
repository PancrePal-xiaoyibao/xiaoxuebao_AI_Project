# 小雪宝能力包

`xiaoxuebao-ability-pack` 是小雪宝多家庭共用的公共能力仓库。它保存可公开复用的 skill、知识库样例、工作流、评测集和 Hermes 接入模板。

## 核心原则

- 公共能力包可共用，家庭运行态必须隔离。
- 公共仓库不保存真实家庭对话、密钥、token 账本、私有病情资料。
- 医学回答只做科普和照护支持，不能替代医生诊断和治疗建议。
- 每个家庭后续使用独立 Hermes profile 或 Docker container 保存记忆和计费状态。

## 目录

- `skills/xiaoxuebao/`: 小雪宝核心 skill。
- `knowledge/`: 儿童白血病家属 MVP Markdown 知识条目、来源清单和资产清单。
- `workflows/`: 家庭问答、微信客服、医生审核、知识更新流程。
- `evals/`: 安全和隔离评测用例。
- `hermes/`: Hermes 家庭 profile/container 模板。
- `adapters/`: OpenClaw、FastAPI、Dify 适配占位。
- `scripts/validate_pack.py`: 能力包一致性检查。

## 验证

```powershell
uv run --extra dev pytest
python scripts/validate_pack.py
```

## Knowledge 规则

- 每条知识必须带 `source_refs`、`reviewer_notes`、`asset_refs`，便于追溯来源、医生审核和儿童友好资产挂接。
- `knowledge/source-catalog.md` 记录本地 PDF、官方网页和待补指南；待补来源不能作为正式回答依据。
- `knowledge/asset-catalog.md` 记录可复用或待生成的图卡、视频和儿童解释素材。
- 新内容默认 `draft_doctor_review_required`，审核通过后再改为 `doctor_reviewed`。
