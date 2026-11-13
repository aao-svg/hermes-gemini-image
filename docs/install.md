# 安装与配置

> 定位：在操作者明确选择的 Hermes profile 安装图片 Provider。适用环境：具备原生图片插件接口、httpx 与 Pillow 的 Hermes；兼容性必须实机检查。前置依赖：自备有权使用的 Messages 兼容代理。

## 1. 确认目标与冲突

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
printf 'HERMES_HOME=%s\n' "$HERMES_HOME"
hermes config path
hermes config env-path
hermes profile list
hermes plugins list --plain
```

确认 profile。非默认 profile 先显式设置其实际 HERMES_HOME，后面所有命令使用同一值。

```bash
test -e "$HERMES_HOME/plugins/gemini-image" && printf 'EXISTS: top-level\n'
test -e "$HERMES_HOME/plugins/image_gen/gemini-image" && printf 'EXISTS: category\n'
```

任一目录或列表已有同名插件就停止，先讨论升级/迁移并备份配置；不要 force、删除或重复启用。

## 2. 核对运行时

使用目标 Hermes 实际的 Python（不是任意系统 Python）：

```bash
python - <<'PY'
import httpx
import PIL
from agent.image_gen_provider import ImageGenProvider, save_b64_image
from agent.file_safety import get_read_block_error
from agent.secret_scope import get_secret_str
from hermes_cli.config import load_config, reload_env, save_env_value_secure
from hermes_cli.plugins import PluginContext
assert callable(getattr(PluginContext, 'register_image_gen_provider', None))
print('image provider interface ok', httpx.__version__, PIL.__version__)
PY
```

任何 ImportError/AttributeError 就停止。不要以 Hermes 包版本号替代接口检查，不自动 pip 或修改核心。

## 3. Git 安装

```bash
hermes plugins install aao-svg/hermes-gemini-image --no-deps
PLUGIN_DIR="$HERMES_HOME/plugins/gemini-image"
test -f "$PLUGIN_DIR/plugin.yaml" && printf 'plugin installed\n'
```

私有仓库需在目标机器配置 GitHub Git 读取权限。`--no-deps` 不变更依赖，安装后保持禁用。安全扫描拦截则停止，不绕过。

## 4. 选择配置模式

### A. 引用已有供应方（推荐）

要求：操作者明确指定名称，供应方使用 `anthropic_messages` 协议、API root 以 `/v1` 结尾，密钥是 `key_env`/`api_key_env` 引用（不支持 `api_key` 字段，包括 `${VARIABLE}`）。插件不复用配置中的 inline 密钥。供应方字段严格使用 `base_url`、`api_mode: anthropic_messages`、`key_env`/`api_key_env`；不自动转换 camelCase、协议别名或 `url`/`api`。暂不支持额外请求头/请求体/自定义 TLS。

```bash
python "$PLUGIN_DIR/configure.py" --source-provider PROVIDER_ID --dry-run
python "$PLUGIN_DIR/configure.py" --source-provider PROVIDER_ID
```

将 `PROVIDER_ID` 替换为操作者选择的真实名称。不会复制服务地址或密钥；改动该供应方会影响后续图片目标。正式执行会只检查凭据是否存在，不联网验证额度。

### B. 独立服务地址与密钥

```bash
API_ROOT="https://example.com/prefix/v1"  # 仅说明格式，必须替换为操作者提供的地址
python "$PLUGIN_DIR/configure.py" --base-url "$API_ROOT" --dry-run
python "$PLUGIN_DIR/configure.py" --base-url "$API_ROOT"
```

地址必须是 HTTPS（回环可 HTTP）、以 `/v1` 结尾；可保留操作者明确提供的路径前缀。不接受占位域名、URL 内嵌凭据、查询、片段、百分号或路径穿越。不自动补全、截断前缀或试探端点。

密钥由操作者自行通过 Hermes 图片设置中的 `GEMINI_IMAGE_API_KEY` 填入，或在自己的可信交互终端执行：

```bash
python "$PLUGIN_DIR/configure.py" --set-key
```

`--set-key` 单独运行只保存独立密钥，不修改配置；不能与 `--source-provider` 同用。不通过聊天、命令行参数、环境赋值、管道或临时文件传递真实密钥。

### 写入范围

脚本执行的命令只有：启用 `gemini-image`；整体设置 `image_gen.gemini_messages` 为供应方引用或独立地址；设置 `image_gen.model=gemini-3.1-flash-image`、`image_gen.provider=gemini-messages`；启用 `image_gen`。不写视频或聊天模型，不重启，不生成。

只读 dry-run 不解析密钥值。正式执行前导入插件检查兼容性。CLI 中途失败可能已写入部分图片设置，脚本不是配置事务；查看 `--check` 并只修复本次图片配置，不用完整旧配置覆盖其他改动。

## 5. 校验

```bash
python "$PLUGIN_DIR/configure.py" --check
hermes plugins validate "$PLUGIN_DIR" --json
hermes plugins list --enabled --plain
python -m unittest discover -s "$PLUGIN_DIR/tests" -p 'test_*.py' -v
```

`--check` 只输出 Provider、模型、地址有效性布尔、密钥存在布尔、注册/可用状态和 `ready`；未就绪退出码 2，不输出服务地址或密钥，不请求上游。`ready` 不是网络或真实出图证明。

`validate` 应返回 `ok: true`。内部 helper 兼容性警告需评估；导入/注册失败、安全扫描失败不能忽略。当前实现引用 `agent.file_safety.get_read_block_error` 以保护凭据文件，并在只读检查中使用内部插件发现/图片注册表；这些接口在开发运行时可用，但升级后可能变化，不能据此承诺其他版本兼容。

新会话中检查 Image Generation 出现 Gemini (Messages proxy)。常驻进程可能缓存旧插件列表；重复 enable 不等于热加载。先重新打开面板/新会话，仍无效才由操作者决定维护窗口重启真实存在的服务。不要自动重启。

真实图片生成必须单独授权，可能消耗额度；安装与离线校验不自动生成。超时/断连先查服务记录，不自动重发。

### 插件默认思考强度

`plugins.entries.gemini-image.settings.thinking` 默认 `high`；仅接受 `high` 和 `unspecified`，不提供单次覆盖，也不继承主聊天设置。缺少此项时仍为 High。

- `high`：每次发送 `thinking.type=adaptive`、`output_config.effort=high`，不设固定预算，输出上限 16384。
- `unspecified`：省略这两个思考字段，由代理决定，输出上限 4096；不是关闭思考。

Hermes 插件中心中打开 `gemini-image` 设置，使用“默认思考强度”枚举。原生设置字段已做离线验证，桌面实际操作仍需在升级后验收。每次生成重新读设置，无单次工具参数。

CLI 切换（只修改本项，不需重新输入地址或密钥）：

```bash
python "$PLUGIN_DIR/configure.py" --thinking unspecified --dry-run
python "$PLUGIN_DIR/configure.py" --thinking unspecified
python "$PLUGIN_DIR/configure.py" --check
```

改回默认 High：

```bash
python "$PLUGIN_DIR/configure.py" --thinking high --dry-run
python "$PLUGIN_DIR/configure.py" --thinking high
```

`--thinking` 不与地址、供应方、密钥或 `--check` 参数混用。正式设置只通过 `hermes config set plugins.entries.gemini-image.settings.thinking ...` 写入。`--check` 额外显示有效 `thinking`；非法设置会未就绪。已加载新版代码后档位切换无需重启，但只修改开发仓库不影响现有安装，升级须另行获得授权并走官方更新流程。

High 已有单次真实出图与操作者后台 High 记录证据；返回的 `thinking_requested` 仅是请求策略，不能当作每次上游执行确认。

## 6. Docker

下面只是布局示例：容器 `hermes`，Hermes `/opt/hermes`，服务用户 `hermes`，数据 `/opt/data`。先核对实际名称、挂载和运行用户；直接 Python 必须以服务用户执行，不能让 root 写入只有其可读的密钥。

```bash
docker exec hermes /opt/hermes/bin/hermes plugins install \
  aao-svg/hermes-gemini-image --no-deps

docker exec --user hermes -e PYTHONPATH=/opt/hermes hermes \
  /opt/hermes/.venv/bin/python /opt/data/plugins/gemini-image/configure.py \
  --source-provider PROVIDER_ID --dry-run
```

确认后去掉 `--dry-run` 正式执行。独立模式的密钥由人运行：

```bash
docker exec --user hermes -it -e PYTHONPATH=/opt/hermes hermes \
  /opt/hermes/.venv/bin/python /opt/data/plugins/gemini-image/configure.py --set-key
```

只读检查：

```bash
docker exec --user hermes -e PYTHONPATH=/opt/hermes hermes \
  /opt/hermes/.venv/bin/python /opt/data/plugins/gemini-image/configure.py --check
```

多 profile：在 docker exec 中再加 `-e HERMES_HOME=/actual/profile/path`，并将脚本路径改为该 profile 实际插件路径；先确认目录存在。

## 7. 回退和升级

切回已有 Muse 生图（仅当目标确实已安装且可用）：

```bash
hermes config set image_gen.model muse-image
hermes config set image_gen.provider muse
```

不改 `video_gen`。保持 Muse 原有插件启用，别为了切图片禁用共享 Muse 插件。

升级前备份并阅读变更，使用官方 `hermes plugins update gemini-image`，再运行检查与校验。没有明确授权不强制覆盖或卸载。

## 参考

- [README](../README.md)
- [Agent 规则](../AGENTS.md)
- [Hermes 图片 Provider 官方接口](https://hermes-agent.nousresearch.com/docs/developer-guide/image-gen-provider-plugin)
- [Hermes 插件官方指南](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
