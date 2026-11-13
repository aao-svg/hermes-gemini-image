# Hermes Gemini Image

独立、非官方的 Hermes 原生图片 Provider 插件。通过操作者自备的 Anthropic Messages 兼容代理调用 `gemini-3.1-flash-image`，不提供代理服务或账号。

目标：文生图及参考图编辑；引用已存在的命名供应方，或显式设置 API root 与独立密钥。仅更改图片生成设置，保持视频和主聊天模型不变。

边界：不包含真实部署地址、凭据、生成素材或聊天记录；不自动重启或真实生成；不承诺视频生成、指定分辨率或任意 Hermes 版本兼容。

开发与验证入口见 [README.md](README.md) 和 [docs/install.md](docs/install.md)。
