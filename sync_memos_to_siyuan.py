#!/usr/bin/env python3
"""将 Memos (v0.25.3) 笔记增量同步到思源笔记。

使用方式：
1) 配置环境变量（可放 .env 并用 systemd/cron 注入）：
   MEMOS_BASE_URL, MEMOS_TOKEN
   SIYUAN_BASE_URL, SIYUAN_TOKEN, SIYUAN_PARENT_ID
2) 运行：python3 sync_memos_to_siyuan.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional



DEFAULT_STATE_FILE = ".memos_siyuan_sync_state.json"
DEFAULT_TIMEOUT = 20

from urllib import error, parse, request


def http_json(method: str, url: str, token_type: str, token: str, params: Optional[Dict[str, Any]] = None, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if params:
        url = f"{url}?{parse.urlencode(params)}"

    headers = {"Authorization": f"{token_type} {token}"}
    data = None
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(json_body).encode("utf-8")

    req = request.Request(url=url, method=method, headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raise SyncError(f"HTTP {exc.code} {url}: {exc.read().decode('utf-8', errors='ignore')}") from exc
    except error.URLError as exc:
        raise SyncError(f"请求失败 {url}: {exc}") from exc

    try:
        return json.loads(raw)
    except Exception as exc:
        raise SyncError(f"响应不是合法 JSON: {url} => {raw[:200]}") from exc



class SyncError(RuntimeError):
    pass


@dataclass
class Config:
    memos_base_url: str
    memos_token: str
    siyuan_base_url: str
    siyuan_token: str
    siyuan_parent_id: str
    state_file: Path
    page_size: int
    include_private: bool

    @staticmethod
    def from_env(args: argparse.Namespace) -> "Config":
        def need(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise SyncError(f"缺少环境变量：{name}")
            return value

        return Config(
            memos_base_url=need("MEMOS_BASE_URL").rstrip("/"),
            memos_token=need("MEMOS_TOKEN"),
            siyuan_base_url=need("SIYUAN_BASE_URL").rstrip("/"),
            siyuan_token=need("SIYUAN_TOKEN"),
            siyuan_parent_id=need("SIYUAN_PARENT_ID"),
            state_file=Path(args.state_file),
            page_size=args.page_size,
            include_private=args.include_private,
        )


class MemosClient:
    def __init__(self, base_url: str, token: str, page_size: int):
        self.base_url = base_url
        self.token = token
        self.page_size = page_size

    def list_memos(self) -> List[Dict[str, Any]]:
        memos: List[Dict[str, Any]] = []
        page_token = ""

        while True:
            params = {"pageSize": self.page_size}
            if page_token:
                params["pageToken"] = page_token

            data = http_json(
                "GET",
                f"{self.base_url}/api/v1/memos",
                token_type="Bearer",
                token=self.token,
                params=params,
            )

            if isinstance(data, list):
                memos.extend(data)
                break

            batch = data.get("memos", [])
            memos.extend(batch)
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break

        return memos


class SiYuanClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = http_json(
            "POST",
            f"{self.base_url}{endpoint}",
            token_type="Token",
            token=self.token,
            json_body=payload,
        )
        if data.get("code") != 0:
            raise SyncError(f"思源接口失败 {endpoint}: {data}")
        return data

    def append_markdown(self, parent_id: str, markdown: str) -> str:
        data = self._post(
            "/api/block/appendBlock",
            {"dataType": "markdown", "data": markdown, "parentID": parent_id},
        )
        do_ops = data.get("data", {}).get("doOperations", [])
        if not do_ops or "id" not in do_ops[0]:
            raise SyncError(f"无法从 appendBlock 返回值解析 block id: {data}")
        return do_ops[0]["id"]

    def update_markdown(self, block_id: str, markdown: str) -> None:
        self._post(
            "/api/block/updateBlock",
            {"dataType": "markdown", "data": markdown, "id": block_id},
        )


def parse_memo_id(memo: Dict[str, Any]) -> str:
    name = str(memo.get("name", "")).strip()
    if name.startswith("memos/"):
        return name.split("/")[-1]
    if name:
        return name
    return str(memo.get("id", ""))


def format_ts(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts


def memo_to_markdown(memo: Dict[str, Any]) -> str:
    memo_id = parse_memo_id(memo)
    create_time = format_ts(str(memo.get("createTime", "")))
    update_time = format_ts(str(memo.get("updateTime", "")))
    visibility = memo.get("visibility", "")
    content = str(memo.get("content", "")).strip()

    header = [
        f"## Memos #{memo_id}",
        "",
        f"- 可见性：`{visibility}`" if visibility else "",
        f"- 创建时间：{create_time}" if create_time else "",
        f"- 更新时间：{update_time}" if update_time else "",
        "",
        content,
        "",
        "---",
    ]
    return "\n".join([x for x in header if x != ""])


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"memos": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SyncError(f"状态文件解析失败：{path} ({exc})") from exc


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def filter_memos(memos: Iterable[Dict[str, Any]], include_private: bool) -> List[Dict[str, Any]]:
    out = []
    for memo in memos:
        if memo.get("state") == "ARCHIVED":
            continue
        vis = memo.get("visibility", "")
        if vis == "PRIVATE" and not include_private:
            continue
        if not str(memo.get("content", "")).strip():
            continue
        out.append(memo)
    return out


def run_sync(cfg: Config) -> Dict[str, int]:
    memos_client = MemosClient(cfg.memos_base_url, cfg.memos_token, cfg.page_size)
    siyuan_client = SiYuanClient(cfg.siyuan_base_url, cfg.siyuan_token)

    state = load_state(cfg.state_file)
    mapping: Dict[str, Dict[str, str]] = state.setdefault("memos", {})

    memos = filter_memos(memos_client.list_memos(), cfg.include_private)

    created = 0
    updated = 0
    skipped = 0

    for memo in memos:
        memo_id = parse_memo_id(memo)
        if not memo_id:
            continue

        md = memo_to_markdown(memo)
        fp = fingerprint(md)
        old = mapping.get(memo_id, {})

        if old.get("fingerprint") == fp and old.get("block_id"):
            skipped += 1
            continue

        block_id = old.get("block_id", "")
        if block_id:
            siyuan_client.update_markdown(block_id, md)
            updated += 1
        else:
            block_id = siyuan_client.append_markdown(cfg.siyuan_parent_id, md)
            created += 1

        mapping[memo_id] = {
            "block_id": block_id,
            "fingerprint": fp,
            "update_time": str(memo.get("updateTime", "")),
            "synced_at": str(int(time.time())),
        }

    save_state(cfg.state_file, state)
    return {"created": created, "updated": updated, "skipped": skipped, "total": len(memos)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="将 Memos 笔记增量同步到思源笔记")
    p.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="本地状态文件路径")
    p.add_argument("--page-size", type=int, default=50, help="Memos 拉取分页大小")
    p.add_argument("--include-private", action="store_true", help="同步 PRIVATE 笔记")
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        cfg = Config.from_env(args)
        result = run_sync(cfg)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(
        "[OK] sync done: "
        f"total={result['total']} created={result['created']} updated={result['updated']} skipped={result['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
