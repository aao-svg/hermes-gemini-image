# Agent 规则

修改代码或安装前先读 README.md、docs/install.md 和本文件。

- 独立、非官方图片插件；不修改 Hermes 核心、主聊天模型、视频设置或 Muse 插件。
- 先确认目标 profile / HERMES_HOME、实际 Python 与 CLI。当前环境检查不能代替别的 profile。
- 服务地址必须由操作者明确提供，或由其明确选择已有命名供应方；不猜地址，不探测候选端点。
- 同名插件存在（plugins/gemini-image 或 plugins/image_gen/gemini-image，含列表中的冲突）就停止，询问保留、升级还是迁移；不 force，不删除、禁用或覆盖。
- 不读取、打印、记录 .env/auth.json 或凭据文件；不向操作者索要聊天明文密钥。已有密钥仅通过 Hermes scoped credential API 用于请求，检查只输出布尔值。
- 秘密通过 Hermes 设置或操作者自己的可信 TTY 中 configure.py --set-key 保存；Agent 不代输，不通过命令行、管道或临时文件传递。
- 目标 Hermes 导入接口/httpx/Pillow 失败就停止；不 raw pip，不修改核心绕过。
- 使用官方 Git 安装器 --no-deps；安全扫描拦截就停止，不绕过。
- 先 dry-run，展示非密钥命令，再正式配置。只通过 Hermes CLI 写配置，不能手改 config.yaml。
- 不自动重启，不自动真实生成。此类操作必须单独获得操作者同意。安装、ready 检查、校验和测试均不应生成付费媒体。
- 请求超时或断开不自动重试；告知可能已消耗额度，人工查服务记录后再决定。
- 公开仓库不包含真实服务域名、部署 IP、绝对运行路径、密钥、Cookie、聊天记录、媒体 URL、生成素材或运行收据。示例只用保留域名和明确离线/回环夹具；占位域名不可配置。
- 测试只用离线 HTTP 夹具；先失败后实现，覆盖响应大小、无图、畸形 Base64、凭据保护、输入/输出校验和不重试。
- 文档使用中文，检查本地链接；不声称任意 Hermes 版本兼容或未经实测的真实出图。
- 未经授权不 commit/push/公开/发布 release。

完成报告：目标 home/profile、安装路径、执行的非密钥命令、Provider/模型与密钥存在布尔值、插件校验、未改视频的验证结果，以及未做的真实生成/重启/其他平台验收。不要公开 API root 或完整配置。
