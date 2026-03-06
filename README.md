# Memos → 思源笔记 自动同步脚本

这个仓库提供一个单文件脚本，用于将 **Memos（v0.25.3）** 的笔记增量同步到思源笔记。

## 功能

- 从 Memos API 分页拉取笔记
- 过滤归档、空内容笔记
- 默认只同步非 `PRIVATE` 笔记（可选开启）
- 首次同步自动在思源指定父块下创建块
- 后续按内容哈希增量更新（不重复写入）
- 本地状态文件记录 `memo_id ↔ block_id`

## 1. 环境准备

只需要 Python 3.9+（使用标准库，无额外依赖）。

## 2. 配置环境变量

```bash
export MEMOS_BASE_URL="http://127.0.0.1:5230"
export MEMOS_TOKEN="你的_memos_token"

export SIYUAN_BASE_URL="http://127.0.0.1:6806"
export SIYUAN_TOKEN="你的_思源_api_token"

# 思源里你想作为“同步容器”的父块 ID
export SIYUAN_PARENT_ID="20240101010101-abcdefg"
```

## 3. 手动执行同步

```bash
python3 sync_memos_to_siyuan.py
```

可选参数：

```bash
python3 sync_memos_to_siyuan.py --include-private --page-size 100
```

## 4. 自动化（cron）

每 5 分钟执行一次：

```bash
*/5 * * * * MEMOS_BASE_URL="http://127.0.0.1:5230" MEMOS_TOKEN="xxx" SIYUAN_BASE_URL="http://127.0.0.1:6806" SIYUAN_TOKEN="yyy" SIYUAN_PARENT_ID="20240101010101-abcdefg" /usr/bin/python3 /path/to/sync_memos_to_siyuan.py >> /tmp/memos-sync.log 2>&1
```

## 注意事项

- 若你需要“删除同步”（Memos 删除后思源也删），可在此脚本基础上扩展。
- 当前版本以“块同步”为主，适合把 Memos 当作输入源汇总到思源某个文档/目录下。
