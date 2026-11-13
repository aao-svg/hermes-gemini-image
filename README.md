# Hermes Gemini Image

独立、非官方的 Hermes 原生图片 Provider，通过操作者自备的 Anthropic Messages 兼容代理调用 `gemini-3.1-flash-image`。不是 Google 官方 Gemini API 插件，不包含反重力服务端、账号或额度。

## 提供什么

- Image Generation 显示 `Gemini (Messages proxy)`，Provider ID 为 `gemini-messages`，插件名 `gemini-image`。
- 文生图，以及最多 4 张本地参考图或 Data URI 的图片编辑请求。
- 请求使用 `POST <API root>/messages`、`x-api-key`、`anthropic-version: 2023-06-01`，非流式返回。只接受图片 content block 或文本内的 Base64 图片 Data URI。
- 不调用 `/images/generations`、`/chat/completions` 或 Google `generateContent`；不生成视频。
- 输出校验后保存到 Hermes 当前 profile 的生成图片缓存，供原生工具投递。

比例以提示词发送，不保证精确尺寸；不提供分辨率、种子、放大或视频参数。参考图不支持网络 URL，不上传图床。

## 插件默认思考设置

规则只有：**插件设置 ＞ 代理默认行为**。不继承主聊天思考设置，不提供单次参数，Agent 即使传入旧的 `thinking` 参数也不会覆盖插件设置。

- `high`（默认）：每次发送 `thinking: {type: adaptive}` 和 `output_config: {effort: high}`，`max_tokens` 上限为 16384，不混入固定 `budget_tokens`。
- `unspecified`（不指定）：不发送 `thinking` / `output_config`，输出上限保持 4096，由代理决定思考行为；不等于关闭思考，代理仍可能默认 High。

设置保存于 `plugins.entries.gemini-image.settings.thinking`。在 Hermes 插件中心中打开 `gemini-image` 的设置，选择“默认思考强度”：`high` 或 `unspecified`。这不是 Image Generation 后端列表中的单次工具参数。已验证 Hermes 原生字段描述能产生这两个选项；当前桌面实际点击链路尚未验收。

也可通过插件脚本切换，先 dry-run 再执行：

```bash
python "$PLUGIN_DIR/configure.py" --thinking unspecified --dry-run
python "$PLUGIN_DIR/configure.py" --thinking unspecified
python "$PLUGIN_DIR/configure.py" --check
```

改回 High 时将 `unspecified` 替换为 `high`。`--thinking` 单独修改这一个插件设置，不改服务地址、密钥、模型或视频，不重新启用插件，不生成。每次生成重新读取设置，已加载新版代码后修改档位无需重启；旧安装版本需先升级才能支持。

非法设置在联网前拒绝。返回 `thinking_requested` 表示本次使用的插件策略，`thinking_effective_verified: false` 表示插件不能逐次独立证明上游执行的档位。High 的一次授权测试已返回可解码图片，操作者提供的对应后台记录显示 High；这不保证其他代理版本相同，也不证明每张图质量提高。

## 安装与配置

先读 [安装手册](docs/install.md) 和 [Agent 规则](AGENTS.md)。只支持目标机实际检查通过的 Hermes `ImageGenProvider` / `PluginContext.register_image_gen_provider` 接口，并需要现有 httpx 和 Pillow。不修改 Hermes 核心、不自动安装依赖。

```bash
hermes plugins install aao-svg/hermes-gemini-image --no-deps
PLUGIN_DIR="${HERMES_HOME:-$HOME/.hermes}/plugins/gemini-image"
```

私有仓库需要先在目标机器配置 GitHub Git 读取权限；不要将 token 写进 URL。

推荐引用已存在的命名供应方（`PROVIDER_ID` 替换为实际名称）：

```bash
python "$PLUGIN_DIR/configure.py" --source-provider PROVIDER_ID --dry-run
python "$PLUGIN_DIR/configure.py" --source-provider PROVIDER_ID
python "$PLUGIN_DIR/configure.py" --check
hermes plugins validate "$PLUGIN_DIR" --json
```

供应方必须配置 `api_mode: anthropic_messages`，使用 `key_env` / `api_key_env` 引用已有密钥（不支持 `api_key` 字段，包括 `${VARIABLE}`）。插件只保存供应方名称，不复制密钥或服务地址；后续该供应方地址改变也会改变图片请求的目标，需由操作者维护信任。额外请求头、请求体或自定义 TLS 配置暂不支持，遇到此类设置会拒绝运行。

也可以使用独立地址和 `GEMINI_IMAGE_API_KEY`，见安装手册。真实地址由操作者提供，没有默认服务。

配置仅写入 `image_gen.gemini_messages`、`image_gen.model`、`image_gen.provider`，启用图片工具及本插件，不修改视频、聊天模型或其他供应方。`image_gen.gemini_messages` 会整体替换，防止旧地址与供应方引用混用。安装和配置不会重启服务、联网生成或消耗生成额度。

## 安全边界

- 不把密钥发到聊天、issue、日志或命令行参数；通过 Hermes 私密设置或自己的交互终端输入。
- 本地参考图先应用 Hermes 凭据文件保护，再校验真实格式；支持 PNG/JPEG/WebP，单张最多 10 MiB/40 MP，最多 4 张、合计 24 MiB。
- 返回体最多 32 MiB；输出图片最多 10 MiB/40 MP。只接受可完整解码图片，不下载返回的任意网络 URL。
- 服务请求禁止重定向、自动重试和协议回退。超时/断连可能已消耗额度，先查服务记录，不要自动重提。
- 返回固定脱敏错误，不转发上游错误正文、地址、请求头或异常内容。
- 不公开真实服务地址、凭据、聊天记录、生成图片或任务记录。

## 验证边界

High 的请求字段已做一次授权生图验收，返回 thinking 内容块及可解码图片，操作者提供的对应后台截图显示 High。插件默认设置的切换与单次参数隔离以离线测试验证，未为这次改动额外发起真实生成。自动测试不访问真实代理、不消耗额度。参考图编辑、其他代理实现、其他操作系统和其他 Hermes 版本需另行验证。

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

新会话中向 Hermes 请求调用 `image_generate`；默认图片 Provider 由本插件服务。真实生成需要操作者另行授权；不要把 `ready: true` 或插件校验当作出图证明。

## 结构与许可

- [IDEA.md](IDEA.md)：范围
- [docs/install.md](docs/install.md)：完整配置、Docker、检查与回退
- [AGENTS.md](AGENTS.md)：安装与维护边界
- `__init__.py`：Provider 与图片处理
- `settings.py`：显式地址和供应方引用校验
- `configure.py`：原生 CLI 配置与只读检查
- `tests/`：离线测试

MIT，见 [LICENSE](LICENSE)。本项目非 Google、Antigravity 或 Nous Research 官方项目，相关名称不表示背书。
