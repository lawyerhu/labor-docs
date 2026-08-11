from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings


@dataclass(frozen=True)
class LegalSnapshot:
    query: str
    verified: bool
    retrieved_at: str
    source: str
    content: dict

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "verified": self.verified,
            "retrieved_at": self.retrieved_at,
            "source": self.source,
            "content": self.content,
        }


class YuandianLegalResearchProvider:
    """后端元典适配边界；未配置或失败时明确返回未核验，不使用记忆补写条文。"""

    async def search(self, query: str) -> LegalSnapshot:
        settings = get_settings()
        now = datetime.now(timezone.utc).isoformat()
        if not settings.yuandian_endpoint or not settings.yuandian_token:
            return LegalSnapshot(query, False, now, "yuandian:not-configured", {})
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    settings.yuandian_endpoint,
                    headers={"Authorization": f"Bearer {settings.yuandian_token}"},
                    json={"query": query},
                )
                response.raise_for_status()
            content = response.json()
            if not isinstance(content, dict):
                return LegalSnapshot(query, False, now, "yuandian:invalid-response", {})
            # The adapter trusts only an explicit provider-side verification flag.
            verified = content.get("verified") is True
            source = "yuandian:verified" if verified else "yuandian:unverified"
            return LegalSnapshot(query, verified, now, source, content)
        except Exception:
            return LegalSnapshot(query, False, now, "yuandian:unavailable", {})
