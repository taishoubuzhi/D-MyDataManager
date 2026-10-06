"""HuggingFace 仓库探测与下载地址展开：URL 模板、文件清单、体积预估。

`huggingface_hub` 是可选依赖：装了就用它列目录，没装就退化为 `urllib` 直连 HF 的
公开 HTTP API；两条路都只读元信息，不下载权重。
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from app.sdk import storage
from app.sdk.console import console_for

from ..settings import DEFAULT_BASE_URL, ModelSettings
from .downloader import (
    USER_AGENT,
    DownloadError,
    build_opener,
    describe_error,
    to_int,
    timeout_seconds,
)

_console = console_for("lib.model")

__all__ = ["guess_total_size", "hub_file_list", "resolve_urls"]

#: 默认分支
DEFAULT_REVISION = "main"
#: 没配 base_url 时的 HF 主站
HF_BASE_URL = DEFAULT_BASE_URL


def _mirror_list(mirrors) -> list[str]:
    """把 mirrors 参数规整成字符串列表；支持 None / 单个串 / 可迭代。"""
    if mirrors is None:
        return []
    if isinstance(mirrors, str):
        raw = mirrors.split(",")
    else:
        raw = list(mirrors)
    return [str(item).strip().rstrip("/") for item in raw if str(item).strip()]


def _expand(base: str, repo: str, file: str, revision: str) -> str:
    """展开一个地址：带占位符的当完整模板，否则按 HF 的 `resolve` 路径拼。"""
    text = str(base or "").strip().rstrip("/")
    if not text:
        return ""
    if "{repo}" in text or "{file}" in text or "{revision}" in text:
        return text.format(repo=repo, file=file, revision=revision)
    return f"{text}/{repo}/resolve/{revision}/{file}"


def resolve_urls(base_url: str, mirrors, repo: str, file: str, revision: str = "main") -> list[str]:
    """把「主站 + 镜像」展开成下载地址列表，顺序就是回退顺序。

    `base_url` / 每个镜像既可以是域名（拼 `{base}/{repo}/resolve/{revision}/{file}`），
    也可以是自带 `{repo}`/`{file}`/`{revision}` 占位符的完整模板（ModelScope 等）。
    """
    repo_id = str(repo or "").strip().strip("/")
    name = str(file or "").strip().strip("/")
    rev = str(revision or "").strip() or DEFAULT_REVISION
    if not repo_id or not name:
        raise DownloadError("下载地址缺少仓库或文件名")
    found: list[str] = []
    for base in [str(base_url or "").strip(), *_mirror_list(mirrors)]:
        url = _expand(base, repo_id, name, rev)
        if url and url not in found:
            found.append(url)
    return found


def hub_file_list(repo: str, revision: str = "main", settings: ModelSettings | None = None) -> list[dict]:
    """列 HF 仓库的文件，返回 `[{"path", "size"}]`（按路径排序）。

    优先用可选的 `huggingface_hub`；没有或失败时退化为公开 HTTP API。
    """
    repo_id = str(repo or "").strip().strip("/")
    if not repo_id:
        raise DownloadError("仓库名不能为空")
    rev = str(revision or "").strip() or DEFAULT_REVISION
    try:
        return _files_via_package(repo_id, rev)
    except ImportError:
        _console.debug("未安装 huggingface_hub，改用 HTTP API 列目录")
    except Exception as exc:
        _console.warning(f"huggingface_hub 列目录失败，改用 HTTP API：{describe_error(exc)}")
    try:
        return _files_via_api(repo_id, rev, settings)
    except DownloadError:
        raise
    except Exception as exc:
        raise DownloadError(f"列仓库文件失败：{repo_id}（{describe_error(exc)}）") from exc


def _files_via_package(repo: str, revision: str) -> list[dict]:
    """用 huggingface_hub 的 tree 接口列文件；装了才会走到这里。"""
    from huggingface_hub import HfApi

    files: list[dict] = []
    for entry in HfApi().list_repo_tree(repo, revision=revision, recursive=True):
        if not hasattr(entry, "size"):
            continue
        path = str(getattr(entry, "path", "") or "")
        if path:
            files.append({"path": path, "size": int(getattr(entry, "size", 0) or 0)})
    if not files:
        raise DownloadError(f"仓库里没有文件：{repo}")
    return sorted(files, key=lambda item: item["path"])


def _api_base(settings: ModelSettings | None) -> str:
    """列仓库文件用的 API 根：**和下载源保持一致**。

    选「HF-Mirror 镜像」时下载走镜像，这里也必须打镜像的 API —— 只看 `base_url`（官方）
    的话，官方连不上就永远列不出仓库文件，配套文件（config / 分词器）也就补不齐。
    """
    if settings is None:
        return HF_BASE_URL
    for attr in ("download_base", "base_url"):
        value = str(getattr(settings, attr, "") or "").strip()
        if value:
            return value.rstrip("/")
    return HF_BASE_URL


def _files_via_api(repo: str, revision: str, settings: ModelSettings | None) -> list[dict]:
    """公开 HTTP API：revision 接口拿 `siblings`（路径），tree 接口拿大小。"""
    base = _api_base(settings)
    payload = _get_json(f"{base}/api/models/{repo}/revision/{revision}", settings)
    siblings = payload.get("siblings") if isinstance(payload, dict) else None
    paths = [
        str(item.get("rfilename"))
        for item in (siblings or [])
        if isinstance(item, dict) and item.get("rfilename")
    ]
    sizes = _tree_sizes(base, repo, revision, settings)
    names = sorted(set(paths) | set(sizes)) if (paths or sizes) else []
    if not names:
        raise DownloadError(f"仓库里没有文件：{repo}")
    return [{"path": name, "size": int(sizes.get(name, 0) or 0)} for name in names]


def _tree_sizes(base: str, repo: str, revision: str, settings: ModelSettings | None) -> dict[str, int]:
    """tree 接口（`?recursive=true`）里的 path → size；失败就返回空表。"""
    try:
        payload = _get_json(f"{base}/api/models/{repo}/tree/{revision}?recursive=true", settings)
    except Exception as exc:
        _console.debug(f"读仓库文件树失败：{describe_error(exc)}")
        return {}
    sizes: dict[str, int] = {}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or str(item.get("type") or "") != "file":
            continue
        path = str(item.get("path") or "")
        if path:
            sizes[path] = int(item.get("size") or 0)
    return sizes


def _get_json(url: str, settings: ModelSettings | None) -> Any:
    """GET 一个 JSON 接口；网络错误与坏 JSON 都抛 DownloadError。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with build_opener(settings).open(request, timeout=timeout_seconds(settings)) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        exc.close()
        raise DownloadError(f"HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"连接失败：{getattr(exc, 'reason', exc)}（{url}）") from exc
    try:
        return storage.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DownloadError(f"下载源返回的不是 JSON：{url}") from exc


def guess_total_size(
    repo: str,
    file: str,
    revision: str = "main",
    settings: ModelSettings | None = None,
) -> int:
    """尽力而为地预估文件大小：HEAD 拿 `Content-Length`，沿镜像回退，拿不到返回 0。"""
    base = getattr(settings, "base_url", "") if settings is not None else ""
    mirrors = getattr(settings, "mirrors", ()) if settings is not None else ()
    try:
        urls = resolve_urls(base or HF_BASE_URL, mirrors, repo, file, revision)
    except DownloadError:
        return 0
    for url in urls:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with build_opener(settings).open(request, timeout=timeout_seconds(settings)) as response:
                size = to_int(response.headers.get("Content-Length"))
        except Exception as exc:
            _console.debug(f"预估体积失败（{url}）：{describe_error(exc)}")
            continue
        if size:
            return size
    return 0
