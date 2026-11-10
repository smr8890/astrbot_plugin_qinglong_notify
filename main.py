from __future__ import annotations

import asyncio
import ipaddress
import secrets

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_qinglong_notify"
BOUND_UMOS_KEY = "bound_umos"


def _split_message(text: str, max_length: int) -> list[str]:
    """Split a message into chunks that fit the platform limit.

    Args:
        text: Message text to split.
        max_length: Maximum characters per chunk. Non-positive means unlimited.

    Returns:
        Message chunks, preferring line boundaries and hard-cutting long lines.
    """
    if max_length <= 0 or len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > max_length:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:max_length])
            line = line[max_length:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= max_length:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks or [text]


@register(
    PLUGIN_NAME,
    "smr8890",
    "接收青龙面板自定义 Webhook 通知，并转发到 AstrBot 配置的消息渠道。",
    "v0.1.0",
    "https://github.com/smr8890/astrbot_plugin_qinglong_notify",
)
class QinglongNotifyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._background_tasks: set[asyncio.Task] = set()

        current_token = config.get("token", "")
        if not isinstance(current_token, str) or not current_token.strip():
            config["token"] = secrets.token_urlsafe(32)
            save_config = getattr(config, "save_config", None)
            if callable(save_config):
                try:
                    save_config()
                except Exception as exc:
                    self.logger.error(
                        "Failed to persist the generated plugin token: %s",
                        exc,
                    )
                else:
                    self.logger.info(
                        "Generated a plugin token. View it in the plugin "
                        "configuration page.",
                    )
            else:
                self.logger.warning(
                    "Generated a plugin token in memory but could not persist it.",
                )

        self.context.register_web_api(
            f"/{PLUGIN_NAME}/notify",
            self.handle_notify,
            ["POST"],
            "Receive Qinglong custom webhook notification",
        )

    async def _get_targets(self) -> list[str]:
        """Collect configured and plugin-bound UMOs without duplicates.

        Returns:
            Unified message origins in configuration order.
        """
        configured = self.config.get("target_umos", [])
        if isinstance(configured, str):
            configured = configured.splitlines()
        elif not isinstance(configured, list):
            configured = []

        bound = await self.get_kv_data(BOUND_UMOS_KEY, [])
        if not isinstance(bound, list):
            bound = []

        targets: list[str] = []
        for item in [*configured, *bound]:
            if not isinstance(item, str):
                continue
            target = item.strip()
            if target and target not in targets:
                targets.append(target)
        return targets

    async def _send_messages(
        self,
        targets: list[str],
        chunks: list[str],
    ) -> tuple[int, int]:
        """Send message chunks to every target with per-target error isolation.

        Args:
            targets: Unified message origins to deliver to.
            chunks: Message chunks to send to each target.

        Returns:
            A tuple of successful and failed target counts.
        """
        sent = 0
        failed = 0
        for target in targets:
            try:
                for chunk in chunks:
                    delivered = await self.context.send_message(
                        target,
                        MessageChain([Plain(text=chunk)]),
                    )
                    if not delivered:
                        raise RuntimeError("No platform matched the session")
                sent += 1
            except Exception as exc:
                failed += 1
                self.logger.error(
                    "Failed to send notification to %s: %s",
                    target,
                    exc,
                )
        return sent, failed

    async def handle_notify(self):
        """Handle a Qinglong webhook request and forward its content.

        Returns:
            A JSON response understood by AstrBot and Qinglong.
        """
        expected_token = self.config.get("token", "")
        expected_token = (
            expected_token if isinstance(expected_token, str) else str(expected_token)
        )
        if not expected_token:
            self.logger.warning(
                "Notification rejected: plugin token is not configured."
            )
            return error_response("插件 Token 未配置", status_code=403)

        provided_token = request.headers.get("X-Plugin-Token", "")
        if not provided_token or not secrets.compare_digest(
            provided_token.encode("utf-8"),
            expected_token.encode("utf-8"),
        ):
            self.logger.warning(
                "Notification rejected: invalid plugin token from %s.",
                request.client_host,
            )
            return error_response("插件 Token 无效", status_code=403)

        allowed_ips = self.config.get("allowed_ips", [])
        if isinstance(allowed_ips, str):
            allowed_ips = allowed_ips.splitlines()
        elif not isinstance(allowed_ips, list):
            allowed_ips = []
        if allowed_ips:
            client_ip = request.client_host
            try:
                client_address = ipaddress.ip_address(client_ip) if client_ip else None
            except ValueError:
                client_address = None

            allowed = False
            if client_address is not None:
                for entry in allowed_ips:
                    entry = str(entry).strip()
                    if not entry:
                        continue
                    try:
                        if "/" in entry:
                            if client_address in ipaddress.ip_network(
                                entry,
                                strict=False,
                            ):
                                allowed = True
                                break
                        elif client_address == ipaddress.ip_address(entry):
                            allowed = True
                            break
                    except ValueError:
                        self.logger.warning(
                            "Invalid allowed_ips entry ignored: %s",
                            entry,
                        )
            if not allowed:
                self.logger.warning(
                    "Notification rejected: source IP %s is not allowed.",
                    client_ip,
                )
                return error_response("来源 IP 不在允许列表中", status_code=403)

        content_type = (request.content_type or "").split(";", 1)[0].strip().lower()
        if content_type == "application/json":
            payload = await request.json(default=None)
            if not isinstance(payload, dict):
                return error_response("请求体必须是 JSON 对象", status_code=400)

            title_value = payload.get("title", "")
            content_value = payload.get("content", "")
            message_value = payload.get("message", "")
            title = (
                title_value if isinstance(title_value, str) else str(title_value or "")
            )
            content = (
                content_value
                if isinstance(content_value, str)
                else str(content_value or "")
            )
            if not title.strip() and not content.strip():
                content = (
                    message_value
                    if isinstance(message_value, str)
                    else str(message_value or "")
                )
        else:
            body = await request.body()
            if not body:
                return error_response("请求内容为空", status_code=400)

            raw = body.decode("utf-8", errors="replace")
            raw = raw.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
            title, separator, content = raw.partition("\n")
            if not separator:
                title, content = "", raw
            # Qinglong script notifications escape newlines inside the content.
            title = title.replace("\\n", "\n")
            content = content.replace("\\n", "\n")
            title = title.strip()
            content = content.strip()

        prefix = self.config.get("prefix", "")
        prefix = prefix if isinstance(prefix, str) else str(prefix or "")
        text = "\n".join(part for part in (title, content) if part)
        if prefix:
            text = f"{prefix}{text}"
        if not text.strip():
            return error_response("请求内容为空", status_code=400)

        try:
            max_length = int(self.config.get("max_message_length", 1500))
        except (TypeError, ValueError):
            max_length = 1500
        chunks = _split_message(text, max_length)

        try:
            targets = await self._get_targets()
        except Exception as exc:
            self.logger.error("Failed to read notification targets: %s", exc)
            return error_response("读取通知目标失败", status_code=500)

        if not targets:
            self.logger.warning("Notification received but no targets are configured.")
            return json_response({"status": "ok", "data": {"targets": 0}})

        if bool(self.config.get("background_send", True)):
            task = asyncio.create_task(self._send_messages(targets, chunks))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return json_response(
                {"status": "ok", "data": {"targets": len(targets)}},
            )

        sent, failed = await self._send_messages(targets, chunks)
        if failed:
            return error_response(
                f"转发失败：成功 {sent} 个，失败 {failed} 个",
                status_code=500,
                data={"targets": len(targets), "sent": sent, "failed": failed},
            )
        return json_response({"status": "ok", "data": {"targets": len(targets)}})

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("qlbind")
    async def qlbind(self, event: AstrMessageEvent):
        """Add the current session to the notification targets."""
        umo = event.unified_msg_origin
        if not umo:
            yield event.plain_result("无法获取当前会话 ID。")
            return

        if umo in await self._get_targets():
            yield event.plain_result("当前会话已在青龙通知目标中。")
            return

        bound = await self.get_kv_data(BOUND_UMOS_KEY, [])
        if not isinstance(bound, list):
            bound = []
        bound.append(umo)
        await self.put_kv_data(BOUND_UMOS_KEY, bound)
        yield event.plain_result(f"已添加当前会话到青龙通知目标：\n{umo}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("qlunbind")
    async def qlunbind(self, event: AstrMessageEvent):
        """Remove the current session from the plugin-bound targets."""
        umo = event.unified_msg_origin
        bound = await self.get_kv_data(BOUND_UMOS_KEY, [])
        if not isinstance(bound, list):
            bound = []

        if umo not in bound:
            if umo in await self._get_targets():
                yield event.plain_result(
                    "当前会话由插件配置 target_umos 提供，请到插件配置中移除。",
                )
            else:
                yield event.plain_result("当前会话不在青龙通知目标中。")
            return

        await self.put_kv_data(
            BOUND_UMOS_KEY,
            [item for item in bound if item != umo],
        )
        if umo in await self._get_targets():
            yield event.plain_result(
                "已从插件绑定列表移除，但当前会话仍由配置 target_umos 提供。",
            )
        else:
            yield event.plain_result("已从青龙通知目标移除当前会话。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("qltargets")
    async def qltargets(self, event: AstrMessageEvent):
        """List all effective notification targets."""
        targets = await self._get_targets()
        if not targets:
            yield event.plain_result("当前没有配置任何青龙通知目标。")
            return

        lines = [f"{index}. {target}" for index, target in enumerate(targets, start=1)]
        yield event.plain_result(
            f"青龙通知目标（{len(targets)}）：\n" + "\n".join(lines),
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("qltest")
    async def qltest(self, event: AstrMessageEvent):
        """Send a test notification to all effective targets."""
        targets = await self._get_targets()
        if not targets:
            yield event.plain_result("当前没有配置任何青龙通知目标。")
            return

        prefix = self.config.get("prefix", "")
        prefix = prefix if isinstance(prefix, str) else str(prefix or "")
        text = f"{prefix}测试消息\n这是一条来自 AstrBot 的青龙通知转发测试消息。"
        try:
            max_length = int(self.config.get("max_message_length", 1500))
        except (TypeError, ValueError):
            max_length = 1500

        sent, failed = await self._send_messages(
            targets,
            _split_message(text, max_length),
        )
        yield event.plain_result(
            f"测试消息发送完成：成功 {sent} 个，失败 {failed} 个。"
        )

    async def terminate(self):
        """Cancel pending background deliveries when the plugin is unloaded."""
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
