"""
export_kemono_favorites.py
--------------------------
从 kemono.cr 导出当前账号的收藏帖子和收藏作者，分别保存为 JSON 文件。

依赖：
    pip install curl_cffi python-dotenv

.env 配置（与 KToolBox 共用同一个 .env 文件）：
    KEMONO_COOKIE_HEADER=<完整 Cookie 请求头>   # 从浏览器 Network 面板复制

用法：
    python export_kemono_favorites.py

输出：
    kemono_favorites_posts.json   - 收藏的帖子列表
    kemono_favorites_artists.json - 收藏的作者列表
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv

# ── 配置 ────────────────────────────────────────────────────────────────────

BASE_URL    = "https://kemono.cr/api/v1"
PAGE_SIZE   = 50      # kemono.cr 固定步进
REQ_DELAY   = 0.8     # 每次请求之间的间隔（秒）
TIMEOUT     = 20

OUTPUT_POSTS   = Path("kemono_favorites_posts.json")
OUTPUT_ARTISTS = Path("kemono_favorites_artists.json")

# ── 加载 Cookies ─────────────────────────────────────────────────────────────

def _parse_cookie_header(raw: str) -> dict[str, str]:
    """解析浏览器复制的原始 Cookie 头字符串"""
    result: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = v.strip()
    return result


def load_cookies() -> dict[str, str]:
    dotenv_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=True)

    cookie_header = os.environ.get("KEMONO_COOKIE_HEADER", "").strip()
    # 去除 python-dotenv 可能保留的外层引号
    if len(cookie_header) >= 2 and cookie_header[0] in ('"', "'") and cookie_header[0] == cookie_header[-1]:
        cookie_header = cookie_header[1:-1]

    if not cookie_header:
        # 回退：仅 session key
        session = os.environ.get("KTOOLBOX_API__SESSION_KEY", "").strip()
        if not session:
            print(
                "[ERROR] 未找到 Cookie 配置。\n\n"
                "请在 .env 中添加：\n"
                "  KEMONO_COOKIE_HEADER=<完整 Cookie 值>\n\n"
                "获取方法：\n"
                "  浏览器打开 kemono.cr（保持登录）\n"
                "  → F12 → Network → 刷新页面\n"
                "  → 点击任意请求 → Headers → 复制 Cookie 字段的值"
            )
            sys.exit(1)
        print("[WARN] 仅使用 session Cookie，可能遇到 Bot 拦截。")
        return {"session": session}

    cookies = _parse_cookie_header(cookie_header)
    if "session" not in cookies:
        print("[WARN] KEMONO_COOKIE_HEADER 中未包含 'session'，请确认复制了正确内容。")
    print(f"[INFO] 已加载 {len(cookies)} 个 Cookie: {', '.join(cookies.keys())}")
    return cookies


# ── 核心请求逻辑 ──────────────────────────────────────────────────────────────

async def fetch_favorites(session: AsyncSession, fav_type: str) -> list[dict]:
    """
    分页拉取指定类型的收藏，直到结果为空。
    fav_type: "post" 或 "artist"
    """
    results: list[dict] = []
    seen_ids: set[str] = set()
    offset = 0
    page   = 1

    print(f"  开始拉取（type={fav_type}）...")

    while True:
        url    = f"{BASE_URL}/account/favorites"
        params = {"type": fav_type, "o": offset}

        try:
            resp = await session.get(url, params=params, timeout=TIMEOUT)
        except Exception as e:
            print(f"  [ERROR] 网络错误（page={page}）: {e}")
            break

        if resp.status_code in (302, 401, 403):
            body_preview = resp.text[:600] if hasattr(resp, 'text') else "(无法读取响应体)"
            print(
                f"  [ERROR] HTTP {resp.status_code}\n"
                f"  响应体预览:\n{body_preview}\n"
                "  ──────────────────────────────────────────\n"
                "  如果响应体是 HTML（机器人验证页），请确认 curl_cffi 已正确安装：\n"
                "  在运行此脚本的同一 Python 中执行：\n"
                "    python -m pip install curl_cffi\n"
                "  然后移除脚本中 import httpx 的行（如果有）。\n"
                "  如果响应体是 JSON 且包含 'error'，Cookie 可能已过期，重新复制 KEMONO_COOKIE_HEADER。"
            )
            sys.exit(1)

        if resp.status_code != 200:
            print(f"  [WARN] 状态码 {resp.status_code}（page={page}），停止。")
            break

        try:
            data: list[dict] = resp.json()
        except Exception as e:
            print(f"  [ERROR] JSON 解析失败（page={page}）: {e}\n  原始响应: {resp.text[:200]}")
            break

        if not data:
            break

        # 去重：如果 API 不支持分页（返回全部），第二页会返回跟第一页一样的内容
        new_items = []
        for item in data:
            # 组合唯一键（对于帖子是 service_user_id，对于作者是 service__id）
            unique_id = f"{item.get('service')}_{item.get('user', '')}_{item.get('id')}"
            if unique_id not in seen_ids:
                seen_ids.add(unique_id)
                new_items.append(item)

        if not new_items:
            # 这一页全都是已经见过的内容，说明 API 忽略了 offset 参数，循环结束
            break

        results.extend(new_items)
        print(f"  第 {page} 页：获取 {len(new_items)} 条，累计 {len(results)} 条")

        # 正常分页时，如果返回数量小于步进，说明是最后一页
        # 如果返回数量大于步进（比如一次返回了 401 条），说明 API 忽略了分页，一次性返回了全部
        if len(data) != PAGE_SIZE:
            break

        offset += PAGE_SIZE
        page   += 1
        await asyncio.sleep(REQ_DELAY)

    return results


# ── 保存 JSON ─────────────────────────────────────────────────────────────────

def save_json(data: list[dict], path: Path, label: str) -> None:
    payload = {
        "_meta": {
            "source":      "kemono.cr",
            "type":        label,
            "count":       len(data),
            "exported_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "data": data,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  已保存 {len(data)} 条 → {path.resolve()}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    cookies = load_cookies()

    # impersonate="chrome" 让 curl_cffi 使用 Chrome 的 TLS 指纹，
    # 这是绕过 DuckDuckGo Bot Protection 的关键。
    async with AsyncSession(
        impersonate="chrome",
        cookies=cookies,
        headers={
            # Kemono 官方要求：爬虫必须使用 Accept: text/css 来绕过 DDG 拦截 JSON
            "Accept":          "text/css",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer":         "https://kemono.cr/favorites",
        },
    ) as sess:

        print("\n[1/2] 导出收藏帖子 (post)...")
        posts = await fetch_favorites(sess, "post")
        save_json(posts, OUTPUT_POSTS, "favorites_post")

        await asyncio.sleep(REQ_DELAY)

        print("\n[2/2] 导出收藏作者 (artist)...")
        artists = await fetch_favorites(sess, "artist")
        save_json(artists, OUTPUT_ARTISTS, "favorites_artist")

    print(
        f"\n✅ 导出完成！"
        f"\n   收藏帖子：{len(posts)} 条  → {OUTPUT_POSTS}"
        f"\n   收藏作者：{len(artists)} 条  → {OUTPUT_ARTISTS}"
    )


if __name__ == "__main__":
    asyncio.run(main())
