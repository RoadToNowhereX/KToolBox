"""
import_pawchive_favorites.py
--------------------------
读取从 kemono.cr 导出的 JSON 文件，并将这些收藏导入到 pawchive.st 中。

依赖：
    pip install curl_cffi python-dotenv

.env 配置：
    PAWCHIVE_COOKIE_HEADER=<pawchive.st 的完整 Cookie 请求头>   # 从浏览器复制
    # 或回退使用：
    # PAWCHIVE_SESSION_KEY=<pawchive.st 的 session 值>

用法：
    python import_pawchive_favorites.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv

# ── 配置 ────────────────────────────────────────────────────────────────────

BASE_URL = "https://pawchive.st/api/v1"
REQ_DELAY = 0.8      # 请求间隔，防止被服务端限流
TIMEOUT = 15.0

INPUT_POSTS   = Path("kemono_favorites_posts.json")
INPUT_ARTISTS = Path("kemono_favorites_artists.json")

# ── 加载 Cookies ─────────────────────────────────────────────────────────────

def _parse_cookie_header(raw: str) -> dict[str, str]:
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

    cookie_header = os.environ.get("PAWCHIVE_COOKIE_HEADER", "").strip()
    # 兼容环境变量可能带引号的情况
    if len(cookie_header) >= 2 and cookie_header[0] in ('"', "'") and cookie_header[0] == cookie_header[-1]:
        cookie_header = cookie_header[1:-1]

    if not cookie_header:
        session = os.environ.get("PAWCHIVE_SESSION_KEY", "").strip()
        if not session:
            print(
                "[ERROR] 未找到 pawchive.st 的 Cookie 配置。\n\n"
                "请在 .env 中添加：\n"
                "  PAWCHIVE_COOKIE_HEADER=<完整 Cookie 值>\n\n"
                "获取方法：\n"
                "  浏览器打开 pawchive.st（保持登录）\n"
                "  → F12 → Network → 刷新页面\n"
                "  → 点击任意请求 → Headers → 复制 Cookie 字段的值"
            )
            sys.exit(1)
        print("[WARN] 仅使用 PAWCHIVE_SESSION_KEY，若遇到 403 请改用 PAWCHIVE_COOKIE_HEADER。")
        return {"session": session}

    cookies = _parse_cookie_header(cookie_header)
    if "session" not in cookies:
        print("[WARN] PAWCHIVE_COOKIE_HEADER 中未包含 'session'，请确认复制了正确内容。")
    print(f"[INFO] 已加载 pawchive.st Cookie ({len(cookies)} 个): {', '.join(cookies.keys())}")
    return cookies

# ── 加载 JSON ───────────────────────────────────────────────────────────────

def load_json(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[WARN] 文件不存在，跳过: {path}")
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            # 根据 kemono.cr 的导出格式，实际数组在 "data" 键中
            return data.get("data", [])
    except Exception as e:
        print(f"[ERROR] 读取文件 {path} 失败: {e}")
        sys.exit(1)

# ── 核心导入逻辑 ──────────────────────────────────────────────────────────────

async def import_favorites(
    session: AsyncSession, 
    items: list[dict], 
    fav_type: str
):
    """
    fav_type: "post" 或 "artist"
    """
    total = len(items)
    if total == 0:
        return

    success_count = 0
    fail_count = 0

    print(f"  准备导入 {total} 个 {fav_type} 收藏...")

    for i, item in enumerate(items, 1):
        service = item.get("service")
        
        if fav_type == "post":
            # 帖子需要 service, creator_id(user), post_id(id)
            creator_id = item.get("user")
            post_id = item.get("id")
            if not all([service, creator_id, post_id]):
                print(f"  [{i}/{total}] 跳过：数据不完整 {item}")
                continue
            url = f"{BASE_URL}/favorites/post/{service}/{creator_id}/{post_id}"
            log_name = f"Post: {service} / {creator_id} / {post_id}"
            
        else:
            # 作者需要 service, creator_id(id)
            creator_id = item.get("id")
            if not all([service, creator_id]):
                print(f"  [{i}/{total}] 跳过：数据不完整 {item}")
                continue
            url = f"{BASE_URL}/favorites/creator/{service}/{creator_id}"
            log_name = f"Artist: {service} / {creator_id} ({item.get('name', 'Unknown')})"

        try:
            # 发送 POST 请求导入收藏
            resp = await session.post(url, timeout=TIMEOUT)
        except Exception as e:
            print(f"  [{i}/{total}] ❌ 网络请求失败 {log_name}: {e}")
            fail_count += 1
            await asyncio.sleep(REQ_DELAY)
            continue

        if resp.status_code == 200 or resp.status_code == 201:
            print(f"  [{i}/{total}] ✅ 成功 {log_name}")
            success_count += 1
        elif resp.status_code in (302, 401, 403):
            print(
                f"  [{i}/{total}] ❌ 鉴权失败 (HTTP {resp.status_code})。\n"
                "  pawchive.st 的 Cookie 可能已过期或需要通过 Bot 验证，请重新获取。"
            )
            sys.exit(1)
        else:
            # 服务端返回其他状态码（可能已收藏或报错）
            print(f"  [{i}/{total}] ⚠️ 异常状态码 HTTP {resp.status_code} - {log_name}")
            # 即使报错，我们也算处理过，继续下一个
            fail_count += 1

        await asyncio.sleep(REQ_DELAY)

    print(f"  统计：成功 {success_count}，失败 {fail_count}。")


# ── 入口 ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    cookies = load_cookies()

    posts = load_json(INPUT_POSTS)
    artists = load_json(INPUT_ARTISTS)

    if not posts and not artists:
        print("[INFO] 没发现需要导入的数据。")
        return

    # pawchive.st 和 kemono.cr 一样使用了防 Bot，所以也使用 curl_cffi + chrome 模拟
    async with AsyncSession(
        impersonate="chrome",
        cookies=cookies,
        headers={
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer":         "https://pawchive.st/favorites",
            # 防止服务器拦截 JSON POST 请求
            "Content-Type":    "application/json",
        },
    ) as sess:

        if posts:
            print("\n[1/2] 开始导入收藏帖子 (post)...")
            await import_favorites(sess, posts, fav_type="post")
        
        if artists:
            print("\n[2/2] 开始导入收藏作者 (artist)...")
            await import_favorites(sess, artists, fav_type="artist")

    print("\n✅ 导入工作结束！")

if __name__ == "__main__":
    asyncio.run(main())
