"""
无水印解析工具 - 云端版
部署到 Render（Free Tier），前端部署到 GitHub Pages
"""
import os
import re
import json
import time
from pathlib import Path
from typing import Optional

# ── FastAPI / uvicorn ────────────────────────────────────────────────
from fastapi import FastAPI, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import requests

# ── 部署路径配置 ──────────────────────────────────────────────────────
# cloud/server.py 在 cloud/ 目录，前端 dist 在 cloud/dist/ 目录
APP_DIR = Path(__file__).parent
DIST_DIR = APP_DIR / "dist"
PORT = int(os.environ.get("PORT", "10000"))

MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.douyin.com/",
}

PC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

XHS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.xiaohongshu.com/",
}

BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

# ══════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════

def _safe_first(lst, default=""):
    if lst and isinstance(lst, list):
        return lst[0] or default
    return default

def _safe_get(obj, *keys, default=None):
    cur = obj
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return default
    return cur if cur is not None else default

def extract_url_from_text(text: str) -> str:
    pattern = r'https?://[^\s\u4e00-\u9fa5，。！？【】「」（）【】]+'
    matches = re.findall(pattern, text)
    if matches:
        return matches[0].rstrip('，。！？】）')
    return text.strip()

def detect_platform(url: str) -> str:
    lower = url.lower()
    if any(x in lower for x in ['douyin.com', 'iesdouyin.com', 'v.douyin.com']):
        return 'douyin'
    if any(x in lower for x in ['xiaohongshu.com', 'xhslink.com', 'xhs.cn']):
        return 'xiaohongshu'
    if any(x in lower for x in ['bilibili.com', 'b23.tv']):
        return 'bilibili'
    if any(x in lower for x in ['kuaishou.com', 'gifshow.com', 'v.kuaishou.com']):
        return 'kuaishou'
    if any(x in lower for x in ['weibo.com', 'weibo.cn']):
        return 'weibo'
    if any(x in lower for x in ['weixin.qq.com', 'channels.weixin.qq.com']):
        return 'wechat'
    return 'unknown'

def _fmt_count(n: int) -> str:
    if not n:
        return ""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)

# ══════════════════════════════════════════════════════════════════════
# 抖音解析
# ══════════════════════════════════════════════════════════════════════

def _resolve_douyin_video_id(url: str, session: requests.Session) -> str:
    resp = session.get(url, allow_redirects=True, timeout=12)
    real_url = resp.url
    m = re.search(r'/video/(\d+)', real_url)
    if m:
        return m.group(1)
    html = resp.text
    for pattern in [
        r'"itemId"\s*:\s*"(\d+)"',
        r'"awemeId"\s*:\s*"(\d+)"',
        r'"id"\s*:\s*"(\d+)"',
    ]:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    m = re.search(r'[?&]item_id=(\d+)', real_url)
    if m:
        return m.group(1)
    m = re.search(r'content="https://www\.iesdouyin\.com/share/video/(\d+)"', html)
    if m:
        return m.group(1)
    raise ValueError(
        f"无法提取 video_id，抖音链接可能需要登录或已过期。重定向地址: {real_url[:80]}"
    )

def parse_douyin(share_text: str) -> dict:
    url = extract_url_from_text(share_text)
    session = requests.Session()
    session.headers.update(MOBILE_HEADERS)
    video_id = _resolve_douyin_video_id(url, session)
    page_url = f"https://www.iesdouyin.com/share/video/{video_id}"
    page_resp = session.get(page_url, timeout=10)
    page_resp.raise_for_status()
    html = page_resp.text

    m = re.search(r'window\._ROUTER_DATA\s*=\s*(.*?)</script>', html, re.DOTALL)
    if not m:
        raise ValueError("页面结构变化，未找到 _ROUTER_DATA，抖音可能已更新页面")

    json_data = json.loads(m.group(1).strip())
    loader_data = json_data.get("loaderData", {})
    video_info_res = None
    for key in loader_data:
        node = loader_data[key]
        if isinstance(node, dict) and "videoInfoRes" in node:
            video_info_res = node["videoInfoRes"]
            break
    if not video_info_res:
        for suffix in [f"video_{video_id}/page", f"note_{video_id}/page"]:
            if suffix in loader_data:
                video_info_res = loader_data[suffix].get("videoInfoRes")
                if video_info_res:
                    break
    if not video_info_res:
        raise ValueError("未找到 videoInfoRes 节点，页面结构可能已变化")

    item_list = video_info_res.get("item_list") or []
    if not item_list:
        raise ValueError("item_list 为空，视频可能不存在或已被删除")

    data = item_list[0]
    title = data.get("desc") or "抖音视频"
    author_obj = data.get("author") or {}
    author = author_obj.get("nickname") or ""
    author_avatar = _safe_first(_safe_get(author_obj, "avatar_thumb", "url_list", default=[]))
    cover = _safe_first(_safe_get(data, "video", "cover", "url_list", default=[]))
    duration_ms = _safe_get(data, "video", "duration", default=0) or 0
    duration = f"{duration_ms // 1000 // 60:02d}:{duration_ms // 1000 % 60:02d}" if duration_ms else ""
    stats = data.get("statistics") or {}
    media_items = []
    images = data.get("images") or []
    if images:
        for idx, img in enumerate(images):
            url_list = (img or {}).get("url_list") or []
            if url_list:
                media_items.append({
                    "type": "image",
                    "url": url_list[0],
                    "title": f"图片 {idx + 1}",
                })
    else:
        play_url_list = _safe_get(data, "video", "play_addr", "url_list", default=[]) or []
        if play_url_list:
            no_wm_url = play_url_list[0].replace("playwm", "play")
            media_items.append({
                "type": "video",
                "url": no_wm_url,
                "quality": "无水印",
                "title": "无水印视频",
            })
        bit_rate_list = _safe_get(data, "video", "bit_rate", default=[]) or []
        for i, br in enumerate((bit_rate_list or [])[:2]):
            br_url_list = _safe_get(br or {}, "play_addr", "url_list", default=[]) or []
            if br_url_list:
                media_items.append({
                    "type": "video",
                    "url": br_url_list[0].replace("playwm", "play"),
                    "quality": f"备用 {i + 1}",
                    "title": f"备用清晰度 {i + 1}",
                })

    return {
        "platform": "douyin",
        "title": title,
        "cover": cover,
        "author": author,
        "authorAvatar": author_avatar,
        "mediaItems": media_items,
        "description": title,
        "duration": duration,
        "views": _fmt_count(stats.get("play_count") or 0),
        "likes": _fmt_count(stats.get("digg_count") or 0),
    }

# ══════════════════════════════════════════════════════════════════════
# 小红书解析
# ══════════════════════════════════════════════════════════════════════

def parse_xiaohongshu(share_text: str) -> dict:
    url = extract_url_from_text(share_text)
    session = requests.Session()
    session.headers.update(XHS_HEADERS)
    resp = session.get(url, allow_redirects=True, timeout=10)
    real_url = resp.url
    note_id = ""
    for pattern in [r'/explore/([a-f0-9]+)', r'noteId=([a-f0-9]+)', r'/([a-f0-9]{24})']:
        m = re.search(pattern, real_url)
        if m:
            note_id = m.group(1)
            break
    html = resp.text
    title = ""
    cover = ""
    author = ""
    author_avatar = ""
    description = ""
    media_items = []
    title_m = re.search(r'<title[^>]*>([^<]+)</title>', html)
    if title_m:
        title = title_m.group(1).strip().replace(' - 小红书', '').replace(' | 小红书', '')
    og_image = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    if og_image:
        cover = og_image.group(1)
    og_desc = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html)
    if og_desc:
        description = og_desc.group(1)
    init_state_m = re.search(
        r'window\.__INITIAL_STATE__\s*=\s*({.*?})\s*(?:</script>|;)', html, re.DOTALL
    )
    if init_state_m:
        try:
            state = json.loads(init_state_m.group(1))
            note_map = _safe_get(state, "note", "noteDetailMap", default={}) or {}
            note_detail = (note_map.get(note_id) or {}).get("note") or {}
            if not note_detail:
                for v in note_map.values():
                    note_detail = (v or {}).get("note") or {}
                    if note_detail:
                        break
            if note_detail:
                title = note_detail.get("title") or title
                description = note_detail.get("desc") or description
                user = note_detail.get("user") or {}
                author = user.get("nickname") or ""
                author_avatar = user.get("avatar") or ""
                image_list = note_detail.get("imageList") or []
                for idx, img in enumerate(image_list):
                    img = img or {}
                    img_url = img.get("urlDefault") or img.get("url") or ""
                    if img_url:
                        media_items.append({
                            "type": "image",
                            "url": img_url,
                            "title": f"图片 {idx + 1}",
                        })
                video = note_detail.get("video") or {}
                if video:
                    stream = _safe_get(video, "media", "stream", default={}) or {}
                    for fmt in ["h265", "h264", "av1"]:
                        streams = stream.get(fmt) or []
                        if streams:
                            v_url = (streams[0] or {}).get("masterUrl") or ""
                            if v_url:
                                media_items.append({
                                    "type": "video",
                                    "url": v_url,
                                    "quality": "原画无水印",
                                    "title": "视频",
                                })
                            break
        except Exception:
            pass
    if not title:
        title = "小红书笔记"
    if not cover and media_items and media_items[0]["type"] == "image":
        cover = media_items[0]["url"]
    return {
        "platform": "xiaohongshu",
        "title": title,
        "cover": cover,
        "author": author,
        "authorAvatar": author_avatar,
        "mediaItems": media_items,
        "description": description,
    }

# ══════════════════════════════════════════════════════════════════════
# B站解析
# ══════════════════════════════════════════════════════════════════════

def parse_bilibili(share_text: str) -> dict:
    url = extract_url_from_text(share_text)
    session = requests.Session()
    session.headers.update(BILI_HEADERS)
    resp = session.get(url, allow_redirects=True, timeout=10)
    real_url = resp.url
    bv_m = re.search(r'/(BV[a-zA-Z0-9]+)', real_url)
    av_m = re.search(r'/av(\d+)', real_url)
    bvid = bv_m.group(1) if bv_m else ""
    aid = av_m.group(1) if av_m else ""
    title = "B站视频"
    cover = ""
    author = ""
    description = ""
    duration = ""
    views = ""
    likes = ""
    if bvid or aid:
        api_url = "https://api.bilibili.com/x/web-interface/view"
        params = {"bvid": bvid} if bvid else {"aid": aid}
        try:
            api_resp = session.get(api_url, params=params, timeout=10)
            api_data = api_resp.json()
            if api_data.get("code") == 0:
                d = api_data["data"]
                title = d.get("title", title)
                cover = d.get("pic", "")
                author = d.get("owner", {}).get("name", "")
                description = d.get("desc", "")
                dur = d.get("duration", 0)
                duration = f"{dur // 60:02d}:{dur % 60:02d}"
                stat = d.get("stat", {})
                views = _fmt_count(stat.get("view") or 0)
                likes = _fmt_count(stat.get("like") or 0)
                bvid = bvid or d.get("bvid", "")
        except Exception:
            pass
    video_page_url = f"https://www.bilibili.com/video/{bvid}" if bvid else real_url
    media_items = [
        {
            "type": "video",
            "url": video_page_url,
            "quality": "点击下载",
            "title": "点击前往视频页（需配合下载工具）",
        }
    ]
    if cover:
        media_items.append({
            "type": "image",
            "url": cover,
            "title": "视频封面",
        })
    return {
        "platform": "bilibili",
        "title": title,
        "cover": cover,
        "author": author,
        "authorAvatar": "",
        "mediaItems": media_items,
        "description": description,
        "duration": duration,
        "views": views,
        "likes": likes,
    }

# ══════════════════════════════════════════════════════════════════════
# 快手解析
# ══════════════════════════════════════════════════════════════════════

def parse_kuaishou(share_text: str) -> dict:
    url = extract_url_from_text(share_text)
    session = requests.Session()
    session.headers.update(MOBILE_HEADERS)
    resp = session.get(url, allow_redirects=True, timeout=10)
    html = resp.text
    title = ""
    cover = ""
    author = ""
    media_items = []
    title_m = re.search(r'<title[^>]*>([^<]+)</title>', html)
    if title_m:
        title = title_m.group(1).strip()
    og_image = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    if og_image:
        cover = og_image.group(1)
    video_url_m = re.search(r'"photoUrl"\s*:\s*"([^"]+)"', html)
    if not video_url_m:
        video_url_m = re.search(r'"srcNoMark"\s*:\s*"([^"]+)"', html)
    if not video_url_m:
        video_url_m = re.search(r'<video[^>]+src="([^"]+)"', html)
    if video_url_m:
        media_items.append({
            "type": "video",
            "url": video_url_m.group(1).replace('\\u002F', '/'),
            "quality": "无水印",
            "title": "无水印视频",
        })
    if not title:
        title = "快手视频"
    return {
        "platform": "kuaishou",
        "title": title,
        "cover": cover,
        "author": author,
        "authorAvatar": "",
        "mediaItems": media_items,
        "description": title,
    }

# ── 静态文件服务（SPA 前端）─────────────────────────────────────────────
if DIST_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")

# ══════════════════════════════════════════════════════════════════════
# FastAPI 应用
# ══════════════════════════════════════════════════════════════════════

app = FastAPI(title="无水印解析工具", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "cloud"}

# ── 媒体代理下载（云端版核心接口）─────────────────────────────────────
@app.get("/api/proxy")
async def proxy(url: str):
    """代理请求媒体资源，解决跨域和直链失效问题"""
    import urllib.parse
    target = urllib.parse.unquote(url)
    if not target.startswith("http"):
        return {"error": "无效 URL"}, 400
    platform_headers = {}
    lower = target.lower()
    if "douyin.com" in lower or "iesdouyin.com" in lower:
        platform_headers = dict(MOBILE_HEADERS)
    elif "bilibili.com" in lower:
        platform_headers = dict(BILI_HEADERS)
    elif "xiaohongshu.com" in lower:
        platform_headers = dict(XHS_HEADERS)
    else:
        platform_headers = {"User-Agent": MOBILE_HEADERS["User-Agent"], "Referer": "https://www.google.com/"}
    try:
        resp = requests.get(target, headers=platform_headers, timeout=30, stream=True)
        resp.raise_for_status()
    except Exception as e:
        return {"error": f"代理失败：{str(e)[:100]}"}, 502
    return StreamingResponse(
        resp.iter_content(chunk_size=65536),
        media_type=resp.headers.get("Content-Type", "video/mp4"),
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Range",
            "Content-Length": str(resp.headers.get("Content-Length", "")),
            "Content-Range": resp.headers.get("Content-Range", ""),
            "Accept-Ranges": "bytes",
        },
    )

# ── 直接返回无水印媒体 URL（让前端通过 <video>/<img> 直接使用）───────────
class ParseRequest(BaseModel):
    url: str

@app.post("/api/parse")
async def parse(req: ParseRequest):
    """解析分享链接，返回无水印媒体信息"""
    raw = req.url.strip()
    if not raw:
        return {"success": False, "error": "链接不能为空"}
    url = extract_url_from_text(raw)
    platform = detect_platform(url)
    try:
        if platform == "douyin":
            result = parse_douyin(raw)
        elif platform == "xiaohongshu":
            result = parse_xiaohongshu(raw)
        elif platform == "bilibili":
            result = parse_bilibili(raw)
        elif platform == "kuaishou":
            result = parse_kuaishou(raw)
        else:
            return {
                "success": False,
                "error": f"暂不支持该平台，目前支持：抖音、小红书、B站、快手",
            }
        if not result.get("mediaItems"):
            return {
                "success": False,
                "error": "未找到媒体资源，链接可能已失效或平台有更新",
            }
        return {"success": True, "data": result}
    except requests.exceptions.Timeout:
        return {"success": False, "error": "请求超时，请检查网络"}
    except requests.exceptions.ConnectionError as e:
        return {"success": False, "error": f"网络连接失败：{str(e)[:100]}"}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        import traceback
        print(f"[Error] {e}\n{traceback.format_exc()}")
        return {"success": False, "error": f"解析出错：{type(e).__name__}: {str(e)[:200]}"}

# ── 后端代理下载（完整文件流，供前端 fetch + Blob 下载）──────────────────
class DownloadRequest(BaseModel):
    url: str
    filename: str = ""

@app.post("/api/download")
async def download_media(req: DownloadRequest):
    """代理下载媒体文件，通过后端中转解决跨域，返回文件流供前端下载"""
    url = req.url.strip()
    if not url:
        return {"success": False, "error": "url 不能为空"}
    filename = req.filename.strip() or ""

    # 云端需要自己生成文件名（不能依赖客户端传入路径）
    import hashlib
    if not filename:
        key = hashlib.md5(url.encode()).hexdigest()[:8]
        ext = ".mp4" if "mp4" in url.lower() or "video" in url.lower() else ".jpg"
        filename = f"media_{key}_{int(time.time())}{ext}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.douyin.com/",
        }
        r = requests.get(url, headers=headers, timeout=60, stream=True)
        r.raise_for_status()

        def stream():
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return StreamingResponse(
            stream(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename.encode('utf-8').decode('latin1')}",
                "Content-Length": r.headers.get("Content-Length", ""),
            },
        )
    except requests.exceptions.Timeout:
        return {"success": False, "error": "下载超时，请检查网络"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "无法连接到资源地址，请检查链接是否有效"}
    except Exception as e:
        return {"success": False, "error": f"下载失败：{str(e)[:100]}"}

# ══════════════════════════════════════════════════════════════════════
# 启动入口（uvicorn 直接运行）
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
