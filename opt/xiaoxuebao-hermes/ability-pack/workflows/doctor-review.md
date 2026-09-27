# 医生审核流程

1. 新知识条目先标记 `draft_doctor_review_required`。
2. 内容作者只写原创科普摘要，不粘贴指南原文全文。
3. 医生审核重点：医学准确性、适用对象、风险边界、是否容易被误解成治疗指令。
4. 审核通过后，`review_status` 可改为 `doctor_reviewed`。
5. 每次发布记录 `knowledge_version`，并保留变更说明。
