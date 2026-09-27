from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator
from uuid import uuid4

import httpx
from fastapi import Cookie, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field


DEFAULT_ROOT = Path(__file__).resolve().parent if os.name == "nt" else Path("/opt/xiaoxuebao-hermes")
ROOT_DIR = Path(os.getenv("XIAOXUEBAO_ROOT", str(DEFAULT_ROOT)))
ENV_PATH = Path(os.getenv("XIAOXUEBAO_ENV_PATH", ROOT_DIR / ".env"))
DATA_DIR = Path(os.getenv("XIAOXUEBAO_GATEWAY_DATA_DIR", ROOT_DIR / "web-gateway" / "data"))
DB_PATH = Path(os.getenv("XIAOXUEBAO_GATEWAY_DB", DATA_DIR / "xiaoxuebao.db"))
HERMES_CONTAINER = os.getenv("XIAOXUEBAO_HERMES_CONTAINER", "xiaoxuebao-hermes")
COOKIE_NAME = "xxb_session"
UTC = dt.timezone.utc
ALLOWED_USER_ROLES = {"家长", "医护", "志愿者"}
ADMIN_ROLES = {"admin", "superadmin"}
REGISTRATION_ATTEMPTS: dict[str, list[float]] = {}
COMMAND_TTL_SECONDS = 10 * 60
MAX_COMMAND_OUTPUT = 2500
ANSWER_SERVICE_UNAVAILABLE = "小雪宝暂时连接不到回答服务，请稍后再试。"
UPSTREAM_ERROR_FALLBACK = "小雪宝暂时没有拿到稳定回答，请稍后再试，或把问题换一种更具体的说法。"
MEDICAL_BOUNDARY_REMINDER = "小雪宝提醒：以上内容仅用于医学科普和照护支持，不能替代医生诊断和治疗建议。"
XIAOXUEBAO_SYSTEM_PROMPT = """
你是小雪宝，一个面向白血病儿童家庭的中文医学科普与照护支持助手。

身份与权限边界：
1. 始终以“小雪宝”的身份回答；不要自称 Hermes Agent、Nous Research、Codex、通用工具代理或服务器运维助手。
2. 你只能在普通聊天中提供白血病相关医学科普、家庭照护支持、就医沟通建议和情绪支持。
3. 不能执行命令、安装包、安装技能、修改服务器、操作 GitHub/Git 仓库、读取文件、读取配置或读取任何密钥。
4. 如果用户要求安装技能、升级能力包、读取 .env、执行 shell、修改 Hermes 配置或访问他人数据，要说明这些操作只能由管理员在后台确认流程中处理，普通聊天不能执行。
5. 普通聊天中不要复述内部产品名、服务器路径、配置文件名、密钥变量名或运维命令；用“后台系统”“环境配置文件”“后台确认流程”等泛化表达。

医学安全边界：
1. 必须明确说明：本回答不能替代医生诊断和治疗建议。
2. 不要输出药物剂量、化疗方案选择、停药/换药指令或治疗方案调整建议。
3. 遇到发热、寒战、出血、皮疹/出血点快速增多、呼吸困难、持续呕吐、脱水、剧烈腹痛/头痛、意识异常、抽搐、中心静脉导管红肿流脓或病情快速变差，要提醒尽快联系主管医生或按医院急诊预案处理。
4. 不要索要或复述姓名、电话、住址、床号、身份证号、报告单号等隐私标识。

Markdown 回答格式：
1. 使用安全 Markdown 子集回答，避免输出一整段文字墙，不要输出原始 HTML。
2. 先用 1-2 句给结论，然后按需要使用二级/三级小标题。
3. 每段不超过 3-4 行；列表不超过 6 项，优先使用短句。
4. 可使用加粗、列表、引用块、表格和已知同源图片；不知道可用图片路径时不要编造图片地址。
5. 医疗红旗信息用引用块突出提醒，并说明不能替代医生诊疗。
6. 涉及疾病、护理、治疗解释或实时信息时，尽量加入“参考/来源”小节；没有可靠来源时明确说明不能代替医生判断，不要编造来源。

联网搜索规则：
1. 对儿童白血病常规科普、护理、饮食、家庭沟通、报告术语解释，优先基于已有医学知识回答，不要因为用户要求“参考/来源”就自动联网。
2. 只有用户明确要求联网、最新、今天、最近、当前政策、药品可及性、价格或其他实时信息时，才使用联网搜索。
3. 需要联网时最多搜索 2 轮；若搜索结果不稳定，直接说明“没有拿到稳定来源”，不要反复搜索或长时间等待。
4. 常规科普需要来源时，可用“参考来源方向”列出指南、医院宣教、权威机构等来源类型，不编造具体网页链接。
""".strip()


def load_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env(name: str, default: str = "") -> str:
    return os.getenv(name) or load_env_file().get(name, default)


def now_utc() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat()


def public_date(value: str | None) -> str:
    if not value:
        return "--"
    try:
        return dt.datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%d")
    except ValueError:
        return value[:10]


def public_time(value: str | None) -> str:
    if not value:
        return "--"
    try:
        return dt.datetime.fromisoformat(value).astimezone(UTC).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


def sanitize_log_text(value: str, *, max_length: int = 500) -> str:
    clean = " ".join(str(value or "").replace("\x00", "").split())
    if len(clean) > max_length:
        return clean[: max_length - 1] + "…"
    return clean


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iteration_text, salt, digest = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            iterations,
        ).hex()
    except Exception:
        return False
    return hmac.compare_digest(candidate, digest)


def infer_topic(question: str) -> str:
    checks = [
        ("发烧", ("发烧", "发热", "体温", "感染")),
        ("饮食", ("吃", "饭", "营养", "水果", "食欲")),
        ("心理", ("害怕", "情绪", "心情", "哭", "解释")),
        ("护理", ("PICC", "输液港", "护理", "口腔", "皮肤")),
        ("用药", ("药", "剂量", "甲氨蝶呤", "化疗")),
    ]
    for topic, words in checks:
        if any(word in question for word in words):
            return topic
    return "其他"


def build_hermes_payload(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    clean_messages = [
        {
            "role": "assistant" if str(message.get("role", "user")) == "assistant" else "user",
            "content": str(message.get("content", "")).strip(),
        }
        for message in messages
        if str(message.get("content", "")).strip()
    ]
    return {
        "model": env("WEB_GATEWAY_MODEL", env("API_SERVER_MODEL_NAME", "xiaoxuebao-web")),
        "messages": [{"role": "system", "content": XIAOXUEBAO_SYSTEM_PROMPT}, *clean_messages],
        "temperature": 0.2,
        # The public gateway is chat-only; skills, shell, MCP and other tools stay
        # behind the superadmin command flow below.
        "tool_choice": "none",
    }


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY,
                  phone TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL,
                  name TEXT NOT NULL,
                  role TEXT NOT NULL,
                  is_admin INTEGER NOT NULL DEFAULT 0,
                  admin_role TEXT NOT NULL DEFAULT 'user',
                  status TEXT NOT NULL DEFAULT 'active',
                  profile_id TEXT,
                  hermes_base_url TEXT,
                  created_at TEXT NOT NULL,
                  last_active_at TEXT
                );
                CREATE TABLE IF NOT EXISTS history (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  question TEXT NOT NULL,
                  answer TEXT NOT NULL,
                  topic TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS admin_command_logs (
                  id TEXT PRIMARY KEY,
                  admin_user_id TEXT NOT NULL,
                  command_text TEXT NOT NULL,
                  action TEXT NOT NULL,
                  status TEXT NOT NULL,
                  preview_json TEXT NOT NULL,
                  confirmation_hash TEXT,
                  expires_at TEXT NOT NULL,
                  output_summary TEXT,
                  error_summary TEXT,
                  created_at TEXT NOT NULL,
                  executed_at TEXT,
                  FOREIGN KEY(admin_user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS web_search_logs (
                  id TEXT PRIMARY KEY,
                  profile_id TEXT NOT NULL,
                  user_id TEXT,
                  role TEXT NOT NULL,
                  query TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  result_count INTEGER NOT NULL DEFAULT 0,
                  status TEXT NOT NULL,
                  error_summary TEXT,
                  related_history_id TEXT,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(user_id) REFERENCES users(id)
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "admin_role" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN admin_role TEXT NOT NULL DEFAULT 'user'")
            # Legacy installs only had is_admin; preserve the existing admin as superadmin.
            conn.execute(
                "UPDATE users SET admin_role = 'superadmin' WHERE is_admin = 1 AND (admin_role IS NULL OR admin_role = 'user')"
            )
            conn.execute("UPDATE users SET is_admin = 1 WHERE admin_role IN ('admin', 'superadmin')")

    def create_user(
        self,
        phone: str,
        password: str,
        name: str,
        role: str,
        *,
        is_admin: bool = False,
        admin_role: str = "user",
        profile_id: str | None = None,
        hermes_base_url: str | None = None,
    ) -> dict[str, Any]:
        if admin_role not in {"user", *ADMIN_ROLES}:
            raise ValueError("Invalid admin role")
        if admin_role in ADMIN_ROLES:
            is_admin = True
        user_id = "u_" + uuid4().hex[:16]
        created_at = now_utc()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                  id, phone, password_hash, name, role, is_admin, admin_role, status,
                  profile_id, hermes_base_url, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    user_id,
                    phone,
                    hash_password(password),
                    name,
                    role,
                    1 if is_admin else 0,
                    admin_role,
                    profile_id,
                    hermes_base_url,
                    created_at,
                ),
            )
        return self.get_user(user_id) or {}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_phone(self, phone: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return self._user_from_row(row) if row else None

    def authenticate(self, identifier: str, password: str) -> dict[str, Any] | None:
        user = self.get_user_by_phone(identifier)
        if not user or user["status"] != "active":
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
            if not row or not verify_password(password, row["password_hash"]):
                return None
            conn.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (now_utc(), user["id"]))
        return self.get_user(user["id"])

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT u.*, COUNT(h.id) AS question_count, MAX(h.created_at) AS latest_history
                FROM users u
                LEFT JOIN history h ON h.user_id = u.id
                GROUP BY u.id
                ORDER BY u.created_at DESC
                """
            ).fetchall()
        return [self._admin_user_from_row(row) for row in rows]

    def set_user_status(self, user_id: str, status: str) -> dict[str, Any] | None:
        if status not in {"active", "inactive"}:
            raise ValueError("Invalid status")
        with self.connect() as conn:
            conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
        users = [user for user in self.list_users() if user["id"] == user_id]
        return users[0] if users else None

    def count_active_superadmins(self) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM users WHERE is_admin = 1 AND admin_role = 'superadmin' AND status = 'active'"
            ).fetchone()
        return int(row["count"] or 0)

    def add_history(self, user_id: str, question: str, answer: str, topic: str) -> dict[str, Any]:
        item_id = "h_" + uuid4().hex[:16]
        created_at = now_utc()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO history (id, user_id, question, answer, topic, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, user_id, question, answer, topic, created_at),
            )
            conn.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (created_at, user_id))
        return {
            "id": item_id,
            "userId": user_id,
            "date": public_date(created_at),
            "time": dt.datetime.fromisoformat(created_at).strftime("%H:%M"),
            "question": question,
            "answer": answer,
            "topic": topic,
        }

    def list_history(self, user_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM history WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        return [self._history_from_row(row) for row in rows]

    def list_all_history(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT h.*, u.name AS user_name
                FROM history h
                JOIN users u ON u.id = h.user_id
                ORDER BY h.created_at DESC
                LIMIT 500
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "userId": row["user_id"],
                "userName": row["user_name"],
                "time": public_time(row["created_at"]),
                "question": row["question"],
                "answer": row["answer"],
                "topic": row["topic"],
            }
            for row in rows
        ]

    def add_admin_command(
        self,
        *,
        admin_user_id: str,
        command_text: str,
        action: str,
        preview: dict[str, Any],
        confirmation_hash: str,
        expires_at: str,
    ) -> dict[str, Any]:
        item_id = "cmd_" + uuid4().hex[:16]
        created_at = now_utc()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_command_logs (
                  id, admin_user_id, command_text, action, status, preview_json,
                  confirmation_hash, expires_at, created_at
                )
                VALUES (?, ?, ?, ?, 'previewed', ?, ?, ?, ?)
                """,
                (
                    item_id,
                    admin_user_id,
                    command_text,
                    action,
                    json.dumps(preview, ensure_ascii=False),
                    confirmation_hash,
                    expires_at,
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM admin_command_logs WHERE id = ?", (item_id,)).fetchone()
        return self._admin_command_from_row(row)

    def get_admin_command(self, command_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM admin_command_logs WHERE id = ?", (command_id,)).fetchone()
        return self._admin_command_from_row(row) if row else None

    def finish_admin_command(
        self,
        command_id: str,
        *,
        status: str,
        output_summary: str = "",
        error_summary: str = "",
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE admin_command_logs
                SET status = ?, output_summary = ?, error_summary = ?, executed_at = ?
                WHERE id = ?
                """,
                (status, output_summary, error_summary, now_utc(), command_id),
            )
        return self.get_admin_command(command_id)

    def list_admin_commands(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, u.name AS admin_name
                FROM admin_command_logs c
                JOIN users u ON u.id = c.admin_user_id
                ORDER BY c.created_at DESC
                LIMIT 100
                """
            ).fetchall()
        return [self._admin_command_from_row(row) for row in rows]

    def add_web_search_log(
        self,
        *,
        profile_id: str,
        user_id: str | None,
        role: str,
        query: str,
        provider: str,
        result_count: int,
        status: str,
        error_summary: str = "",
        related_history_id: str | None = None,
    ) -> dict[str, Any]:
        item_id = "search_" + uuid4().hex[:16]
        created_at = now_utc()
        clean_query = sanitize_log_text(query, max_length=300)
        clean_error = sanitize_log_text(error_summary, max_length=500)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO web_search_logs (
                  id, profile_id, user_id, role, query, provider, result_count,
                  status, error_summary, related_history_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    profile_id,
                    user_id,
                    role,
                    clean_query,
                    provider,
                    int(result_count),
                    status,
                    clean_error,
                    related_history_id,
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM web_search_logs WHERE id = ?", (item_id,)).fetchone()
        return self._web_search_log_from_row(row)

    def list_web_search_logs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT l.*, u.name AS user_name
                FROM web_search_logs l
                LEFT JOIN users u ON u.id = l.user_id
                ORDER BY l.created_at DESC
                LIMIT 300
                """
            ).fetchall()
        return [self._web_search_log_from_row(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        logs = self.list_all_history()
        users = self.list_users()
        today = dt.datetime.now(UTC).strftime("%Y-%m-%d")
        active_today = sum(1 for user in users if user["lastActive"] == today)
        daily_map: dict[str, int] = {}
        topic_map: dict[str, int] = {}
        for log in logs:
            day = log["time"][:10]
            daily_map[day] = daily_map.get(day, 0) + 1
            topic_map[log["topic"]] = topic_map.get(log["topic"], 0) + 1
        return {
            "statsOverview": {
                "totalUsers": len(users),
                "activeToday": active_today,
                "totalQuestions": len(logs),
                "installedSkills": 1,
                "userGrowth": 0,
                "questionGrowth": 0,
            },
            "dailyQuestions": [
                {"date": key[5:] if len(key) >= 10 else key, "count": value}
                for key, value in sorted(daily_map.items())[-7:]
            ],
            "topicStats": [
                {"topic": key, "count": value}
                for key, value in sorted(topic_map.items(), key=lambda item: item[1], reverse=True)[:5]
            ],
            "recentLogs": logs[:5],
        }

    def _user_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        admin_role = row["admin_role"] if "admin_role" in row.keys() else ("superadmin" if row["is_admin"] else "user")
        return {
            "id": row["id"],
            "phone": row["phone"],
            "name": row["name"],
            "role": row["role"],
            "is_admin": bool(row["is_admin"]),
            "admin_role": admin_role,
            "is_superadmin": admin_role == "superadmin",
            "status": row["status"],
            "profile_id": row["profile_id"],
            "hermes_base_url": row["hermes_base_url"],
        }

    def _admin_user_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        latest = row["latest_history"] or row["last_active_at"]
        admin_role = row["admin_role"] if "admin_role" in row.keys() else ("superadmin" if row["is_admin"] else "user")
        return {
            "id": row["id"],
            "phone": row["phone"],
            "name": row["name"],
            "registerDate": public_date(row["created_at"]),
            "questionCount": int(row["question_count"] or 0),
            "lastActive": public_date(latest),
            "status": row["status"],
            "role": row["role"],
            "adminRole": admin_role,
            "isAdmin": bool(row["is_admin"]),
            "isSuperadmin": admin_role == "superadmin",
        }

    def _history_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        created_at = row["created_at"]
        return {
            "id": row["id"],
            "userId": row["user_id"],
            "date": public_date(created_at),
            "time": dt.datetime.fromisoformat(created_at).strftime("%H:%M"),
            "question": row["question"],
            "answer": row["answer"],
            "topic": row["topic"],
        }

    def _admin_command_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        preview = json.loads(row["preview_json"]) if row["preview_json"] else {}
        return {
            "id": row["id"],
            "adminUserId": row["admin_user_id"],
            "adminName": row["admin_name"] if "admin_name" in row.keys() else "",
            "command": row["command_text"],
            "action": row["action"],
            "status": row["status"],
            "preview": preview,
            "expiresAt": row["expires_at"],
            "outputSummary": row["output_summary"] or "",
            "errorSummary": row["error_summary"] or "",
            "createdAt": public_time(row["created_at"]),
            "executedAt": public_time(row["executed_at"]),
        }

    def _web_search_log_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "profileId": row["profile_id"],
            "userId": row["user_id"],
            "userName": row["user_name"] if "user_name" in row.keys() else "",
            "role": row["role"],
            "query": row["query"],
            "provider": row["provider"],
            "resultCount": int(row["result_count"] or 0),
            "status": row["status"],
            "errorSummary": row["error_summary"] or "",
            "time": public_time(row["created_at"]),
        }


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1)
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    phone: str = Field(min_length=1)
    password: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = "家长"


class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]


class CreateUserRequest(BaseModel):
    phone: str = Field(min_length=1)
    password: str = Field(min_length=6)
    name: str = Field(min_length=1)
    role: str = "家长"


class StatusRequest(BaseModel):
    status: str


class AdminCommandPreviewRequest(BaseModel):
    command: str = Field(min_length=2, max_length=300)


class AdminCommandExecuteRequest(BaseModel):
    confirmationToken: str = Field(min_length=16)


db = Database()
app = FastAPI(title="Xiaoxuebao Web Gateway", version="0.1.0")


def session_secret() -> str:
    secret = env("WEB_GATEWAY_SESSION_SECRET", env("API_SERVER_KEY", ""))
    if not secret:
        raise HTTPException(status_code=500, detail="WEB_GATEWAY_SESSION_SECRET missing")
    return secret


def session_cookie_secure() -> bool:
    return env("WEB_GATEWAY_COOKIE_SECURE", "true").strip().lower() in {"1", "true", "yes", "on"}


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_session(user: dict[str, Any]) -> str:
    payload = {
        "sub": user["id"],
        "admin": bool(user["is_admin"]),
        "exp": int((dt.datetime.now(UTC) + dt.timedelta(days=7)).timestamp()),
        "nonce": secrets.token_hex(8),
    }
    body = b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + b64encode(signature)


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, signature = token.split(".", 1)
    expected = hmac.new(session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(b64encode(expected), signature):
        return None
    payload = json.loads(b64decode(body))
    if int(payload.get("exp", 0)) < int(dt.datetime.now(UTC).timestamp()):
        return None
    return payload


def current_user(token: str | None) -> dict[str, Any] | None:
    payload = read_session(token)
    if not payload:
        return None
    user = db.get_user(str(payload.get("sub", "")))
    if not user or user["status"] != "active":
        return None
    return user


def require_user(token: str | None) -> dict[str, Any]:
    user = current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(token: str | None) -> dict[str, Any]:
    user = require_user(token)
    if not user["is_admin"] or user.get("admin_role") not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def require_superadmin(token: str | None) -> dict[str, Any]:
    user = require_user(token)
    if not user["is_admin"] or user.get("admin_role") != "superadmin":
        raise HTTPException(status_code=403, detail="该高风险操作仅限超级管理员")
    return user


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "phone": user["phone"],
        "name": user["name"],
        "role": user["role"],
        "is_admin": bool(user["is_admin"]),
        "admin_role": user.get("admin_role", "superadmin" if user["is_admin"] else "user"),
        "is_superadmin": user.get("admin_role") == "superadmin",
    }


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_registration_rate_limit(request: Request) -> None:
    ip = client_ip(request)
    now = time.time()
    window_seconds = int(env("WEB_GATEWAY_REGISTER_WINDOW_SECONDS", "3600") or "3600")
    max_attempts = int(env("WEB_GATEWAY_REGISTER_MAX_ATTEMPTS", "20") or "20")
    recent = [item for item in REGISTRATION_ATTEMPTS.get(ip, []) if now - item < window_seconds]
    if len(recent) >= max_attempts:
        REGISTRATION_ATTEMPTS[ip] = recent
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")
    recent.append(now)
    REGISTRATION_ATTEMPTS[ip] = recent


def validate_phone(phone: str) -> str:
    clean = phone.strip()
    if not clean.isdigit() or len(clean) != 11:
        raise HTTPException(status_code=400, detail="请输入 11 位手机号")
    return clean


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="密码至少 8 位")
    if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
        raise HTTPException(status_code=400, detail="密码需同时包含字母和数字")


def validate_display_name(name: str) -> str:
    clean = sanitize_log_text(name, max_length=24)
    if not clean:
        raise HTTPException(status_code=400, detail="请输入昵称")
    if any(ch in clean for ch in "<>{}[]"):
        raise HTTPException(status_code=400, detail="昵称包含不支持的字符")
    return clean


def validate_public_role(role: str) -> str:
    clean = role.strip()
    if clean not in ALLOWED_USER_ROLES:
        raise HTTPException(status_code=400, detail="角色不合法")
    return clean


def confirmation_hash(token: str) -> str:
    return hmac.new(session_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def parse_admin_action(command: str) -> str:
    clean = sanitize_log_text(command, max_length=300)
    lowered = clean.lower()
    blocked_terms = (
        ".env",
        "api_key",
        "apikey",
        "secret",
        "password",
        "cat ",
        "rm ",
        "sudo",
        "bash",
        "sh ",
        "powershell",
        "cmd",
        "curl",
        "wget",
        "http://",
        "https://",
        "&&",
        "||",
        ";",
        "|",
        "`",
        "$(",
    )
    if any(term in lowered for term in blocked_terms):
        raise HTTPException(status_code=400, detail="该指令不在安全白名单内")
    if any(word in lowered for word in ("安装", "install", "启用技能", "装技能")):
        return "install_skills_from_pack"
    if any(word in lowered for word in ("校验", "验证", "validate", "test")):
        return "validate_ability_pack"
    if any(word in lowered for word in ("升级", "更新", "update", "pull")):
        return "ability_pack_update"
    if any(word in lowered for word in ("状态", "检查", "status", "查看")):
        return "ability_pack_status"
    raise HTTPException(status_code=400, detail="只支持能力包状态、升级、校验和技能安装指令")


def admin_command_preview(action: str) -> dict[str, Any]:
    previews = {
        "ability_pack_status": {
            "title": "检查小雪宝能力包状态",
            "riskLevel": "low",
            "steps": ["读取能力包 Git 状态", "读取最近一次提交", "返回摘要，不修改文件"],
        },
        "ability_pack_update": {
            "title": "升级小雪宝能力包",
            "riskLevel": "medium",
            "steps": ["在固定能力包目录执行 fast-forward 更新", "保留命令输出摘要", "失败时不改 Hermes 密钥"],
        },
        "validate_ability_pack": {
            "title": "校验小雪宝能力包",
            "riskLevel": "low",
            "steps": ["运行能力包自带校验脚本", "记录校验输出", "不写入用户数据"],
        },
        "install_skills_from_pack": {
            "title": "从小雪宝能力包安装技能",
            "riskLevel": "medium",
            "steps": ["只使用服务器固定能力包目录", "优先运行能力包安装脚本或 Hermes CLI", "记录安装结果摘要"],
        },
    }
    return previews[action]


def ability_pack_dir() -> Path:
    return Path(env("XIAOXUEBAO_ABILITY_PACK_DIR", ROOT_DIR / "ability-pack"))


def masked_output(text: str) -> str:
    clean = sanitize_log_text(text, max_length=MAX_COMMAND_OUTPUT)
    for value in load_env_file().values():
        if value and len(value) >= 8:
            clean = clean.replace(value, "***")
    return clean


def run_command(args: list[str], *, cwd: Path, timeout: int = 120) -> tuple[int, str, str]:
    if not cwd.exists():
        return 1, "", f"目录不存在：{cwd}"
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode, masked_output(completed.stdout), masked_output(completed.stderr)


def execute_admin_action(action: str) -> tuple[bool, str, str]:
    pack_dir = ability_pack_dir()
    if action == "ability_pack_status":
        code_a, out_a, err_a = run_command(["git", "status", "--short", "--branch"], cwd=pack_dir, timeout=30)
        code_b, out_b, err_b = run_command(["git", "log", "-1", "--oneline"], cwd=pack_dir, timeout=30)
        ok = code_a == 0 and code_b == 0
        return ok, "\n".join(part for part in (out_a, out_b) if part), "\n".join(part for part in (err_a, err_b) if part)
    if action == "ability_pack_update":
        code, out, err = run_command(["git", "pull", "--ff-only"], cwd=pack_dir, timeout=180)
        return code == 0, out, err
    if action == "validate_ability_pack":
        script = pack_dir / "scripts" / "validate_pack.py"
        if script.exists():
            code, out, err = run_command([sys.executable, str(script)], cwd=pack_dir, timeout=180)
            return code == 0, out, err
        code, out, err = run_command(["git", "status", "--short"], cwd=pack_dir, timeout=30)
        message = "未找到 scripts/validate_pack.py，已完成能力包目录与 Git 状态检查。"
        return code == 0, "\n".join(part for part in (message, out) if part), err
    if action == "install_skills_from_pack":
        installer = pack_dir / "scripts" / "install_skills.py"
        if installer.exists():
            code, out, err = run_command([sys.executable, str(installer)], cwd=pack_dir, timeout=240)
            return code == 0, out, err
        code, out, err = run_command(
            ["docker", "exec", HERMES_CONTAINER, "hermes", "skills", "install", "/ability-pack"],
            cwd=ROOT_DIR,
            timeout=180,
        )
        return code == 0, out, err
    return False, "", "未知动作"


def bootstrap_admin() -> None:
    username = env("WEB_GATEWAY_ADMIN_USERNAME", "")
    password = env("WEB_GATEWAY_ADMIN_PASSWORD", "")
    if not username or not password:
        return
    if not db.get_user_by_phone(username):
        db.create_user(username, password, "管理员", "管理员", admin_role="superadmin")


def profile_provision_enabled() -> bool:
    return os.getenv("XIAOXUEBAO_ENABLE_PROFILE_PROVISION") == "1" and os.getenv(
        "XIAOXUEBAO_DISABLE_PROFILE_PROVISION"
    ) != "1"


def default_hermes_config() -> tuple[str, str]:
    base_url = env("WEB_GATEWAY_HERMES_BASE_URL", f"http://127.0.0.1:{env('API_SERVER_PORT', '8650')}/v1")
    api_key = env("API_SERVER_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="回答服务配置缺失")
    return base_url.rstrip("/"), api_key


def profile_hermes_config(user: dict[str, Any]) -> tuple[str, str]:
    profile_env = ROOT_DIR / "profiles" / str(user["profile_id"]) / ".env"
    api_key = load_env_file(profile_env).get("API_SERVER_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="回答服务配置缺失")
    return str(user["hermes_base_url"]).rstrip("/"), api_key


def user_hermes_config(user: dict[str, Any] | None) -> tuple[str, str]:
    if profile_provision_enabled() and user and user.get("hermes_base_url") and user.get("profile_id"):
        return profile_hermes_config(user)
    return default_hermes_config()


def hermes_config_candidates(user: dict[str, Any] | None) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if profile_provision_enabled() and user and user.get("hermes_base_url") and user.get("profile_id"):
        try:
            candidates.append(profile_hermes_config(user))
        except HTTPException:
            pass
    default_config = default_hermes_config()
    if default_config not in candidates:
        candidates.append(default_config)
    return candidates


def hermes_timeout() -> httpx.Timeout:
    try:
        total_seconds = float(env("WEB_GATEWAY_HERMES_TIMEOUT_SECONDS", "300"))
    except ValueError:
        total_seconds = 300.0
    total_seconds = max(30.0, total_seconds)
    return httpx.Timeout(total_seconds, connect=10.0)


async def call_hermes(messages: list[dict[str, Any]], user: dict[str, Any] | None) -> dict[str, Any]:
    payload = build_hermes_payload(messages)
    configs = hermes_config_candidates(user)
    async with httpx.AsyncClient(timeout=hermes_timeout()) as client:
        for index, (base_url, api_key) in enumerate(configs):
            is_last = index == len(configs) - 1
            try:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if response.status_code >= 400:
                    if is_last:
                        raise HTTPException(status_code=502, detail=ANSWER_SERVICE_UNAVAILABLE)
                    continue
                try:
                    return response.json()
                except ValueError:
                    if is_last:
                        raise HTTPException(status_code=502, detail="小雪宝暂时没有拿到回答服务的有效响应，请稍后再试。") from None
            except HTTPException:
                raise
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
                if is_last:
                    raise HTTPException(status_code=502, detail=ANSWER_SERVICE_UNAVAILABLE) from None
                continue
    raise HTTPException(status_code=502, detail=ANSWER_SERVICE_UNAVAILABLE)


def extract_answer(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("answer"), str) and payload["answer"].strip():
        return str(payload["answer"]).strip()
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str) and message["content"].strip():
                return message["content"].strip()
    return "小雪宝暂时没有拿到有效回答，请稍后再试。"


def sanitize_public_answer(answer: str) -> str:
    clean = str(answer or "").strip()
    if re.search(r"API call failed|An error occurred during streaming|Traceback|ConnectError|TimeoutException", clean, re.IGNORECASE):
        return UPSTREAM_ERROR_FALLBACK
    replacements = [
        (r"/opt/xiaoxuebao-hermes/\.env\b", "环境配置文件"),
        (r"/opt/xiaoxuebao-hermes(?:/[^\s，。；、)）\]]*)?", "服务器内部路径"),
        (r"\bHermes\s+Agent\b", "后台系统"),
        (r"\bNous\s+Research\b", "后台系统"),
        (r"\bCodex\b", "后台系统"),
        (r"\.env\b", "环境配置文件"),
        (
            r"\b(API_SERVER_KEY|OPENAI_API_KEY|DASHSCOPE_API_KEY|WEB_GATEWAY_SESSION_SECRET|WEB_GATEWAY_ADMIN_PASSWORD|WEB_GATEWAY_ADMIN_USERNAME)\b",
            "配置项",
        ),
        (r"\bshell\b", "后台命令"),
    ]
    for pattern, replacement in replacements:
        clean = re.sub(pattern, replacement, clean, flags=re.IGNORECASE)
    return clean


def prepare_public_answer(answer: str) -> str:
    clean = sanitize_public_answer(answer)
    if "不能替代医生诊断和治疗建议" not in clean:
        clean = f"{clean.rstrip()}\n\n{MEDICAL_BOUNDARY_REMINDER}"
    return clean


def profile_api_is_healthy(base_url: str, api_key: str) -> bool:
    root_url = base_url.rstrip("/")
    if root_url.endswith("/v1"):
        root_url = root_url[:-3].rstrip("/")
    try:
        with httpx.Client(timeout=httpx.Timeout(3.0, connect=1.0)) as client:
            health = client.get(f"{root_url}/health")
            if health.status_code < 400:
                return True
            models = client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            return models.status_code < 400
    except httpx.HTTPError:
        return False


def provision_profile_for_user(user_id: str) -> tuple[str | None, str | None]:
    if not profile_provision_enabled():
        return None, None
    try:
        next_port = 8651
        for item in db.list_users():
            if item["id"] == user_id:
                continue
        with db.connect() as conn:
            rows = conn.execute("SELECT hermes_base_url FROM users WHERE hermes_base_url IS NOT NULL").fetchall()
        used_ports = set()
        for row in rows:
            text = row["hermes_base_url"] or ""
            if ":" in text:
                try:
                    used_ports.add(int(text.rsplit(":", 1)[1].split("/", 1)[0]))
                except ValueError:
                    pass
        while next_port in used_ports:
            next_port += 1
        profile_id = user_id
        api_key = secrets.token_hex(32)
        profile_dir = ROOT_DIR / "profiles" / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(profile_dir, 0o700)
        profile_env = profile_dir / ".env"
        profile_env.write_text(
            "\n".join(
                [
                    "API_SERVER_ENABLED=true",
                    "API_SERVER_HOST=127.0.0.1",
                    f"API_SERVER_PORT={next_port}",
                    f"API_SERVER_KEY={api_key}",
                    f"API_SERVER_MODEL_NAME=小雪宝-Hermes-{profile_id}",
                    "OPENAI_API_KEY=${OPENAI_API_KEY}",
                    "XIAOXUEBAO_ABILITY_PACK=/ability-pack",
                    f"SEARXNG_URL=http://127.0.0.1:{env('WEB_GATEWAY_PORT', '8660')}/internal/search/{profile_id}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        os.chmod(profile_env, 0o600)
        create_result = subprocess.run(
            ["docker", "exec", HERMES_CONTAINER, "hermes", "profile", "create", profile_id],
            check=False,
            timeout=30,
        )
        start_result = subprocess.run(
            ["docker", "exec", HERMES_CONTAINER, "hermes", "-p", profile_id, "gateway", "start"],
            check=False,
            timeout=30,
        )
        hermes_base_url = f"http://127.0.0.1:{next_port}/v1"
        if getattr(create_result, "returncode", 1) != 0 or getattr(start_result, "returncode", 1) != 0:
            return None, None
        if not profile_api_is_healthy(hermes_base_url, api_key):
            return None, None
        return profile_id, hermes_base_url
    except Exception:
        return None, None


@app.on_event("startup")
async def on_startup() -> None:
    bootstrap_admin()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "xiaoxuebao-web-gateway"}


@app.post("/api/auth/login")
async def login(request: LoginRequest, response: Response) -> dict[str, Any]:
    user = db.authenticate(request.identifier.strip(), request.password)
    if not user:
        raise HTTPException(status_code=401, detail="账号或密码错误")
    response.set_cookie(
        COOKIE_NAME,
        create_session(user),
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(),
        max_age=7 * 24 * 3600,
        path="/",
    )
    return public_user(user)


@app.post("/api/auth/register")
async def register(request: RegisterRequest, http_request: Request, response: Response) -> dict[str, Any]:
    enforce_registration_rate_limit(http_request)
    phone = validate_phone(request.phone)
    validate_password_strength(request.password)
    name = validate_display_name(request.name)
    role = validate_public_role(request.role)
    if db.get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="账号已存在")
    temp_user = db.create_user(phone, request.password, name, role, is_admin=False)
    profile_id, hermes_base_url = provision_profile_for_user(temp_user["id"])
    if profile_id and hermes_base_url:
        with db.connect() as conn:
            conn.execute(
                "UPDATE users SET profile_id = ?, hermes_base_url = ? WHERE id = ?",
                (profile_id, hermes_base_url, temp_user["id"]),
            )
    user = db.get_user(temp_user["id"]) or temp_user
    response.set_cookie(
        COOKIE_NAME,
        create_session(user),
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(),
        max_age=7 * 24 * 3600,
        path="/",
    )
    return public_user(user)


@app.post("/api/auth/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@app.get("/api/auth/me")
async def me(xxb_session: str | None = Cookie(default=None)) -> dict[str, Any] | None:
    user = current_user(xxb_session)
    return public_user(user) if user else None


@app.post("/api/chat")
async def chat(request: ChatRequest, xxb_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    user = current_user(xxb_session)
    if not request.messages:
        raise HTTPException(status_code=400, detail="消息不能为空")
    hermes_payload = await call_hermes(request.messages, user)
    answer = prepare_public_answer(extract_answer(hermes_payload))
    question = next((str(item.get("content", "")).strip() for item in reversed(request.messages) if item.get("role") == "user"), "")
    topic = infer_topic(question)
    saved = False
    if user:
        db.add_history(user["id"], question, answer, topic)
        saved = True
    return {"answer": answer, "topic": topic, "saved": saved}


@app.get("/api/history")
async def history(xxb_session: str | None = Cookie(default=None)) -> list[dict[str, Any]]:
    user = require_user(xxb_session)
    return db.list_history(user["id"])


@app.get("/api/admin/users")
async def admin_users(xxb_session: str | None = Cookie(default=None)) -> list[dict[str, Any]]:
    require_admin(xxb_session)
    return db.list_users()


@app.post("/api/admin/users")
async def admin_create_user(request: CreateUserRequest, xxb_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_admin(xxb_session)
    phone = validate_phone(request.phone)
    validate_password_strength(request.password)
    name = validate_display_name(request.name)
    role = validate_public_role(request.role)
    if db.get_user_by_phone(phone):
        raise HTTPException(status_code=409, detail="账号已存在")
    temp_user = db.create_user(phone, request.password, name, role)
    profile_id, hermes_base_url = provision_profile_for_user(temp_user["id"])
    if profile_id and hermes_base_url:
        with db.connect() as conn:
            conn.execute(
                "UPDATE users SET profile_id = ?, hermes_base_url = ? WHERE id = ?",
                (profile_id, hermes_base_url, temp_user["id"]),
            )
    users = [user for user in db.list_users() if user["id"] == temp_user["id"]]
    return users[0]


@app.patch("/api/admin/users/{user_id}/status")
async def admin_set_user_status(
    user_id: str,
    request: StatusRequest,
    xxb_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    actor = require_admin(xxb_session)
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.get("admin_role") in ADMIN_ROLES:
        if actor.get("admin_role") != "superadmin":
            raise HTTPException(status_code=403, detail="管理员账号只能由超级管理员管理")
        if request.status == "inactive" and target.get("admin_role") == "superadmin":
            if target["id"] == actor["id"]:
                raise HTTPException(status_code=409, detail="不能停用当前超级管理员账号")
            if db.count_active_superadmins() <= 1:
                raise HTTPException(status_code=409, detail="至少保留一个启用中的超级管理员")
    try:
        user = db.set_user_status(user_id, request.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="状态不合法") from None
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@app.get("/api/admin/history")
async def admin_history(xxb_session: str | None = Cookie(default=None)) -> list[dict[str, Any]]:
    require_admin(xxb_session)
    return db.list_all_history()


@app.get("/api/admin/stats")
async def admin_stats(xxb_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    require_admin(xxb_session)
    return db.stats()


@app.post("/api/admin/commands/preview")
async def admin_command_preview_route(
    request: AdminCommandPreviewRequest,
    xxb_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    admin = require_superadmin(xxb_session)
    command_text = sanitize_log_text(request.command, max_length=300)
    action = parse_admin_action(command_text)
    preview = admin_command_preview(action)
    token = secrets.token_urlsafe(24)
    expires_at = (dt.datetime.now(UTC) + dt.timedelta(seconds=COMMAND_TTL_SECONDS)).replace(microsecond=0).isoformat()
    item = db.add_admin_command(
        admin_user_id=admin["id"],
        command_text=command_text,
        action=action,
        preview=preview,
        confirmation_hash=confirmation_hash(token),
        expires_at=expires_at,
    )
    return {
        "id": item["id"],
        "action": action,
        "status": item["status"],
        "preview": preview,
        "expiresAt": expires_at,
        "confirmationToken": token,
    }


@app.post("/api/admin/commands/{command_id}/execute")
async def admin_command_execute_route(
    command_id: str,
    request: AdminCommandExecuteRequest,
    xxb_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    admin = require_superadmin(xxb_session)
    item = db.get_admin_command(command_id)
    if not item or item["adminUserId"] != admin["id"]:
        raise HTTPException(status_code=404, detail="命令不存在")
    if item["status"] != "previewed":
        raise HTTPException(status_code=409, detail="命令已处理")
    try:
        expires_at = dt.datetime.fromisoformat(item["expiresAt"]).astimezone(UTC)
    except ValueError:
        expires_at = dt.datetime.now(UTC) - dt.timedelta(seconds=1)
    if expires_at < dt.datetime.now(UTC):
        db.finish_admin_command(command_id, status="expired", error_summary="确认令牌已过期")
        raise HTTPException(status_code=409, detail="确认令牌已过期")
    with db.connect() as conn:
        row = conn.execute("SELECT confirmation_hash FROM admin_command_logs WHERE id = ?", (command_id,)).fetchone()
    if not row or not hmac.compare_digest(row["confirmation_hash"] or "", confirmation_hash(request.confirmationToken)):
        raise HTTPException(status_code=403, detail="确认令牌不正确")
    ok, output, error = execute_admin_action(item["action"])
    updated = db.finish_admin_command(
        command_id,
        status="executed" if ok else "failed",
        output_summary=output,
        error_summary=error,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="命令日志更新失败")
    return updated


@app.get("/api/admin/commands")
async def admin_commands(xxb_session: str | None = Cookie(default=None)) -> list[dict[str, Any]]:
    require_superadmin(xxb_session)
    return db.list_admin_commands()


@app.get("/api/admin/search-logs")
async def admin_search_logs(xxb_session: str | None = Cookie(default=None)) -> list[dict[str, Any]]:
    require_admin(xxb_session)
    return db.list_web_search_logs()


async def proxy_searxng_search(profile_id: str, request: Request) -> dict[str, Any]:
    query = str(request.query_params.get("q", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    user = db.get_user(profile_id)
    user_id = user["id"] if user else None
    role = "visitor"
    if user:
        role = "admin" if user["is_admin"] else "user"
    base_url = env("SEARXNG_BASE_URL", "http://127.0.0.1:8661").rstrip("/")
    params = dict(request.query_params)
    params.setdefault("format", "json")
    status = "ok"
    result_count = 0
    error_summary = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            response = await client.get(
                f"{base_url}/search",
                params=params,
                headers={"User-Agent": "xiaoxuebao-web-gateway/1.0"},
            )
        if response.status_code >= 400:
            status = "error"
            error_summary = f"SearXNG 返回错误：{response.status_code}"
            raise HTTPException(status_code=502, detail=error_summary)
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        result_count = len(results) if isinstance(results, list) else 0
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        status = "error"
        error_summary = str(exc)
        raise HTTPException(status_code=502, detail="联网搜索暂时不可用") from exc
    finally:
        db.add_web_search_log(
            profile_id=profile_id,
            user_id=user_id,
            role=role,
            query=query,
            provider="searxng",
            result_count=result_count,
            status=status,
            error_summary=error_summary,
        )


@app.get("/internal/search/{profile_id}")
async def internal_search(profile_id: str, request: Request) -> dict[str, Any]:
    return await proxy_searxng_search(profile_id, request)


@app.get("/internal/search/{profile_id}/search")
async def internal_search_compat(profile_id: str, request: Request) -> dict[str, Any]:
    return await proxy_searxng_search(profile_id, request)
