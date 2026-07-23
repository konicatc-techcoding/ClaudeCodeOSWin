#!/usr/bin/env python3
"""dashboard/redact.py — 憑證掃描(第三道防線)的共用實作。

提案 docs/webui-migration-proposal.md §3.4:唯讀 API 在把任何 endpoint 的
回應序列化為 JSON 前,統一再跑一次遞迴掃描——防的是「未來新增 endpoint
忘了走白名單函式」的情境。這份實作是**共用正本**:P1 由 dashboard/api.py
在序列化前呼叫;P2 的 data.py `get_hermes_credential_status()` 白名單
輸出前掃描也 import 這裡,不複製貼上。

只依賴 stdlib(re)。純函式、不讀任何檔案、不碰網路——本模組本身
不可能成為洩漏源,只可能把可疑值換成佔位字串。

掃描規則(§3.4 原文:「eyJ 開頭/高熵長字串 → [REDACTED-SUSPECT-SECRET]」):

1. JWT-like:`eyJ` 開頭的 base64url 長段(含可選的 `.` 分段)。
2. 高熵長 token:48 字元以上、不含分隔符的 base64url 字串,且大小寫/數字
   字元類別混雜(避免把一般長單字/純數字誤殺)。
3. 可疑 key 名稱(token/secret/password/api_key/credential…)對應的
   非空字串值整值遮蔽——data.py 的白名單輸出本來就不含這類 key,
   這條是純防禦性的。

門檻刻意保守:job id(UUID,36 字元)、log 文字(含空白/換行)、
一般中文/英文敘述都不會被誤傷。
"""
import re

REDACTED = "[REDACTED-SUSPECT-SECRET]"

# eyJ 開頭 = base64 編碼的 '{"'(JWT header 慣例);含可選的 . 分段。
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]{5,}){0,2}")

# 48+ 字元、無分隔符的 base64url 段(前後不能緊鄰同類字元,避免切半)。
_LONG_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_\-])[A-Za-z0-9_\-]{48,}(?![A-Za-z0-9_\-])")

# 可疑 key 名稱(大小寫不敏感;子字串比對)。
_SUSPECT_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api_?key|credential|private_?key)", re.IGNORECASE
)


def _mixed_char_classes(token: str) -> bool:
    """至少兩種字元類別(小寫/大寫/數字)混雜才視為高熵候選。"""
    classes = 0
    if re.search(r"[a-z]", token):
        classes += 1
    if re.search(r"[A-Z]", token):
        classes += 1
    if re.search(r"[0-9]", token):
        classes += 1
    return classes >= 2


def scan_text(text: str) -> str:
    """對單一字串做可疑 token 置換,回傳淨化後字串。"""
    text = _JWT_RE.sub(REDACTED, text)

    def _maybe_redact(match: re.Match) -> str:
        token = match.group(0)
        return REDACTED if _mixed_char_classes(token) else token

    return _LONG_TOKEN_RE.sub(_maybe_redact, text)


def scan_structure(obj, _key: str | None = None):
    """遞迴掃描 dict/list/str 結構,回傳淨化後的新結構(不就地修改)。"""
    if isinstance(obj, dict):
        return {k: scan_structure(v, _key=str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scan_structure(v) for v in obj]
    if isinstance(obj, str):
        if _key is not None and _SUSPECT_KEY_RE.search(_key) and obj:
            return REDACTED
        return scan_text(obj)
    return obj
