# 小雪宝部署与能力包

这是小雪宝（LeukemiaPal）当前部署快照的可公开部分，包含：

- `deploy/`：systemd、容器和反向代理部署配置
- `opt/xiaoxuebao-hermes/ability-pack/`：共享 Hermes 能力包、知识条目、工作流和评测
- `opt/xiaoxuebao-hermes/web-gateway/`：FastAPI 网关源码与测试
- `opt/xiaoxuebao-hermes/router/`：模型路由源码
- `opt/xiaoxuebao-hermes/web/current/`：当前前端构建产物
- `etc/`：非敏感的系统配置样例

## 安全边界

仓库不包含 API 密钥、环境变量文件、家庭 profile、对话/令牌账本、运行数据库、虚拟环境或 TLS 私钥。部署时请在服务器上单独创建 `opt/xiaoxuebao-hermes/.env`，并按实际环境填写凭证。

## 能力包验证

```bash
cd opt/xiaoxuebao-hermes/ability-pack
python scripts/validate_pack.py
python -m pytest
```

## 网关验证

```bash
cd opt/xiaoxuebao-hermes/web-gateway
python -m pytest
```

医疗内容仅用于健康教育和照护支持，不能替代医生诊断或治疗建议。

