# 青龙通知转发（astrbot_plugin_qinglong_notify）

接收 [青龙面板](https://github.com/whyour/qinglong) 的自定义 Webhook 通知，并通过 AstrBot 转发到 QQ、Telegram、企业微信等已接入的消息渠道。

## 功能

- `POST` 接收青龙脚本版或面板版的自定义 Webhook。
- 支持 `text/plain` 和 `application/json` 请求体。
- 支持多目标转发、去重和单目标失败隔离。
- 支持后台转发，避免青龙等待超时。
- 支持长消息按换行拆分。
- 可选来源 IP / CIDR 白名单。

## 安装

将本目录放入 AstrBot 的 `data/plugins/` 下，重启 AstrBot 或在 WebUI 中重载插件。

本插件需要 AstrBot `4.29.0` 及以上版本，无额外 Python 依赖。

## AstrBot 配置

在 AstrBot WebUI 的插件配置页填写：

| 配置项 | 说明 |
|---|---|
| `token` | 插件访问 Token，对应青龙请求头 `X-Plugin-Token`。留空时插件首次加载会自动生成随机 Token，可在 WebUI 配置页点击眼睛图标查看。 |
| `target_umos` | 通知目标 UMO，每行一个。也可以直接在目标会话中发送 `/qlbind` 添加。 |
| `prefix` | 消息前缀，默认空。 |
| `max_message_length` | 单条消息最大长度，超过后优先按换行拆分；`0` 表示不限制。 |
| `background_send` | 是否收到通知后立即返回 200 并在后台发送。 |
| `allowed_ips` | 允许的来源 IP 或 CIDR，每行一个；留空表示不限制。 |

### 通知目标

推荐在目标群聊或私聊中发送：

```text
/qlbind
```

命令会把当前会话的 UMO 保存到插件 KV，并在后续通知中自动使用。

也可以手动填写 `target_umos`。UMO 必须是 AstrBot 的 `unified_msg_origin`，不是平台原始群号或用户号。

### AstrBot OpenAPI Key

青龙请求本插件路由时，需要同时通过两层鉴权：

1. AstrBot OpenAPI Key：放在 `X-API-Key` 请求头。
2. 插件 Token：放在 `X-Plugin-Token` 请求头。

请在 AstrBot WebUI 中打开系统设置，进入 OpenAPI，权限选择 `plugin`，创建一个 API Key。

## 青龙面板配置

### 配置文件 `config.sh`

```bash
export WEBHOOK_URL="http://<AstrBot 的 IP>:6185/api/v1/plugins/extensions/astrbot_plugin_qinglong_notify/notify"
export WEBHOOK_BODY='$title'$'\n''$content'
export WEBHOOK_HEADERS="Content-Type: text/plain"$'\n'"X-API-Key: <OpenAPI Key>"$'\n'"X-Plugin-Token: <your_plugin_token>"
export WEBHOOK_METHOD="POST"
export WEBHOOK_CONTENT_TYPE="text/plain"
```

`WEBHOOK_BODY` 双引号写法也可以，但必须转义 `$`：

```bash
export WEBHOOK_BODY="\$title\n\$content"
```

不要写成：

```bash
export WEBHOOK_BODY="$title\n$content"
```

### 面板“通知设置 → 自定义 Webhook”

- `webhookMethod`：`POST`
- `webhookContentType`：`text/plain`
- `webhookUrl`：`http://<AstrBot 的 IP>:6185/api/v1/plugins/extensions/astrbot_plugin_qinglong_notify/notify`
- `webhookHeaders`：

  ```text
  Content-Type: text/plain
  X-API-Key: <OpenAPI Key>
  X-Plugin-Token: <your_plugin_token>
  ```

- `webhookBody`：

  ```text
  $title
  $content
  ```

## 命令

| 命令 | 作用 |
|---|---|
| `/qlbind` | 把当前会话加入通知目标（管理员） |
| `/qlunbind` | 从插件绑定的通知目标中移除当前会话（管理员） |
| `/qltargets` | 查看当前所有通知目标（管理员） |
| `/qltest` | 向所有通知目标发送测试消息（管理员） |

`/qlunbind` 只移除通过 `/qlbind` 添加的会话；如果会话来自配置项 `target_umos`，请到插件配置中删除。

## 安全说明

- `token` 在 WebUI 中会遮罩显示，但配置文件本身不加密。
- 如需更换 Token，清空配置项并重载插件即可自动生成新 Token。
- 不要在公开仓库中提交真实的 API Key、插件 Token 或青龙 `config.sh`。
- 建议为插件单独创建 API Key，并设置 `allowed_ips` 限制来源。
- 日志不会记录 `X-Plugin-Token` 或 `X-API-Key`。
