"""AsyncGeminiAgent - 通用视觉模型 Agent，使用 OpenAI 兼容的 function calling。

支持 Gemini、GPT-4o、Claude 等任何支持 vision + tool use 的模型，
通过 OpenAI 兼容 API 端点接入。
"""

import asyncio
import json
import time
import traceback
from collections.abc import AsyncGenerator
from typing import Any

from AutoGLM_GUI.actions import ActionResult
from AutoGLM_GUI.agents.base import AsyncAgentBase
from AutoGLM_GUI.logger import logger
from AutoGLM_GUI.model import MessageBuilder
from AutoGLM_GUI.trace import trace_span

from .action_mapper import tool_call_to_action
from .prompts import get_system_prompt
from .tools import DEVICE_TOOLS

_ACTIONS_NEED_SCREEN_SIZE = {"Tap", "Double Tap", "Long Press", "Swipe"}


class AsyncGeminiAgent(AsyncAgentBase):
    """通用视觉模型 Agent，使用 function calling 而非自定义格式解析。"""

    def _get_default_system_prompt(self, lang: str) -> str:
        return get_system_prompt(lang)

    def _prepare_initial_context(
        self, task: str, screenshot_base64: str, current_app: str
    ) -> None:
        self._context.append(
            MessageBuilder.create_user_message(
                text=f"{task}\n\nCurrent app: {current_app}",
                image_base64=screenshot_base64,
            )
        )

    async def _execute_step(self) -> AsyncGenerator[dict[str, Any], None]:
        """执行单步：调用 LLM → 解析 tool call → 执行动作。"""
        self._step_count += 1

        # 1. 获取截图（非首步）
        if self._step_count > 1:
            try:
                with trace_span(
                    "step.capture_screenshot",
                    attrs={
                        "step": self._step_count,
                        "agent_type": self.__class__.__name__,
                    },
                ):
                    screenshot = await asyncio.to_thread(self.device.get_screenshot)
                with trace_span(
                    "step.get_current_app",
                    attrs={
                        "step": self._step_count,
                        "agent_type": self.__class__.__name__,
                    },
                ):
                    current_app = await asyncio.to_thread(self.device.get_current_app)
            except Exception as e:
                logger.error(f"Failed to get device info: {e}")
                yield {"type": "error", "data": {"message": f"Device error: {e}"}}
                yield {
                    "type": "step",
                    "data": {
                        "step": self._step_count,
                        "thinking": "",
                        "action": None,
                        "success": False,
                        "finished": True,
                        "message": f"Device error: {e}",
                    },
                }
                return

            with trace_span(
                "step.build_message",
                attrs={"step": self._step_count, "agent_type": self.__class__.__name__},
            ):
                self._context.append(
                    MessageBuilder.create_user_message(
                        text=f"Current app: {current_app}",
                        image_base64=screenshot.base64_data,
                    )
                )

        # 2. 调用 LLM with tools
        llm_duration_ms = None
        try:
            with trace_span(
                "step.llm",
                attrs={
                    "step": self._step_count,
                    "agent_type": self.__class__.__name__,
                    "model_name": self.model_config.model_name,
                    "message_count": len(self._context),
                },
            ):
                llm_started = time.monotonic()
                thinking, tool_name, tool_args = await self._call_llm_with_tools()
                llm_duration_ms = int((time.monotonic() - llm_started) * 1000)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"LLM error: {e}")
            if self.agent_config.verbose:
                logger.debug(traceback.format_exc())
            yield {"type": "error", "data": {"message": f"Model error: {e}"}}
            yield {
                "type": "step",
                "data": {
                    "step": self._step_count,
                    "thinking": "",
                    "action": None,
                    "success": False,
                    "finished": True,
                    "message": f"Model error: {e}",
                },
            }
            return

        if thinking:
            yield {"type": "thinking", "data": {"chunk": thinking}}

        # 3. 转换 tool call → action
        with trace_span(
            "step.parse_action",
            attrs={"step": self._step_count, "agent_type": self.__class__.__name__},
        ):
            action = tool_call_to_action(tool_name, tool_args)

        if self.agent_config.verbose:
            logger.debug(f"🎯 Tool call: {tool_name}({tool_args})")
            logger.debug(f"   Action: {json.dumps(action, ensure_ascii=False)}")

        # 4. 执行 action
        screenshot = None
        batch_results = None
        action_duration_ms = None
        tool_feedback: dict[str, Any]
        try:
            action_started = time.monotonic()
            if action.get("_metadata") == "batch":
                result, screenshot, batch_results = await self._execute_batch_action(
                    action
                )
                tool_feedback = {
                    "success": result.success,
                    "message": result.message or "OK",
                    "batch_results": batch_results,
                }
            else:
                if action.get("action") in _ACTIONS_NEED_SCREEN_SIZE:
                    with trace_span(
                        "step.capture_screenshot",
                        attrs={
                            "step": self._step_count,
                            "agent_type": self.__class__.__name__,
                            "purpose": "pre_action",
                        },
                    ):
                        screenshot = await asyncio.to_thread(
                            self.device.get_screenshot
                        )
                    screen_width = screenshot.width
                    screen_height = screenshot.height
                else:
                    screen_width = 1000
                    screen_height = 1000

                with trace_span(
                    "step.execute_action",
                    attrs={
                        "step": self._step_count,
                        "agent_type": self.__class__.__name__,
                        "action_name": action.get("action"),
                        "action_type": action.get("_metadata"),
                    },
                ):
                    result = await asyncio.to_thread(
                        self.action_handler.execute,
                        action,
                        screen_width,
                        screen_height,
                    )
                    tool_feedback = {
                        "success": result.success,
                        "message": result.message or "OK",
                    }
            action_duration_ms = int((time.monotonic() - action_started) * 1000)
        except Exception as e:
            logger.error(f"Action execution error: {e}")
            result = ActionResult(success=False, should_finish=True, message=str(e))
            tool_feedback = {"success": False, "message": str(e)}
            action_duration_ms = (
                int((time.monotonic() - action_started) * 1000)
                if "action_started" in locals()
                else None
            )

        # 5. 更新上下文
        with trace_span(
            "step.update_context",
            attrs={"step": self._step_count, "agent_type": self.__class__.__name__},
        ):
            if len(self._context) > 1:
                self._context[-1] = MessageBuilder.remove_images_from_message(
                    self._context[-1]
                )

            self._context.append(
                {
                    "role": "assistant",
                    "content": thinking or "",
                    "tool_calls": [
                        {
                            "id": f"call_{self._step_count}",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args),
                            },
                        }
                    ],
                }
            )
            self._context.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{self._step_count}",
                    "content": json.dumps(tool_feedback, ensure_ascii=False),
                }
            )

        # 6. 检查完成
        finished = action.get("_metadata") == "finish" or result.should_finish

        yield {
            "type": "step",
            "data": {
                "step": self._step_count,
                "thinking": thinking,
                "action": action,
                "success": result.success,
                "finished": finished,
                "message": result.message or action.get("message"),
                "screenshot": screenshot.base64_data if screenshot else None,
                "batch_results": batch_results,
                "timings": {
                    "llm_ms": llm_duration_ms,
                    "action_ms": action_duration_ms,
                },
            },
        }

    async def _execute_batch_action(
        self, action: dict[str, Any]
    ) -> tuple[ActionResult, Any | None, list[dict[str, Any]]]:
        actions = action.get("actions")
        if not isinstance(actions, list) or not actions:
            return ActionResult(False, False, "Batch has no actions"), None, []

        needs_screen_size = any(
            item.get("action") in _ACTIONS_NEED_SCREEN_SIZE for item in actions
        )
        screenshot = None
        if needs_screen_size:
            with trace_span(
                "batch.capture_screenshot",
                attrs={"step": self._step_count, "agent_type": self.__class__.__name__},
            ):
                screenshot = await asyncio.to_thread(self.device.get_screenshot)
            screen_width = screenshot.width
            screen_height = screenshot.height
        else:
            screen_width = 1000
            screen_height = 1000

        stop_on_error = bool(action.get("stop_on_error", True))
        step_timeout = max(
            0.5, min(float(action.get("step_timeout_ms") or 5000) / 1000.0, 10.0)
        )
        total_timeout = max(
            1.0, min(float(action.get("total_timeout_ms") or 20000) / 1000.0, 30.0)
        )
        started = time.monotonic()
        results: list[dict[str, Any]] = []
        stopped_reason = ""

        for index, sub_action in enumerate(actions):
            if self._cancel_event.is_set():
                raise asyncio.CancelledError()

            elapsed = time.monotonic() - started
            remaining = total_timeout - elapsed
            if remaining <= 0:
                stopped_reason = "total_timeout"
                break

            timeout = min(step_timeout, remaining)
            step_started = time.monotonic()
            try:
                with trace_span(
                    "batch.execute_action",
                    attrs={
                        "step": self._step_count,
                        "batch_index": index,
                        "agent_type": self.__class__.__name__,
                        "action_name": sub_action.get("action"),
                    },
                ):
                    sub_result = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.action_handler.execute,
                            sub_action,
                            screen_width,
                            screen_height,
                        ),
                        timeout=timeout,
                    )
                duration_ms = int((time.monotonic() - step_started) * 1000)
                item = {
                    "index": index,
                    "action": sub_action,
                    "success": sub_result.success,
                    "finished": sub_result.should_finish,
                    "message": sub_result.message or "OK",
                    "duration_ms": duration_ms,
                    "timeout_ms": int(timeout * 1000),
                }
                results.append(item)
                if sub_result.should_finish:
                    stopped_reason = "action_finished"
                    break
                if not sub_result.success and stop_on_error:
                    stopped_reason = "action_failed"
                    break
            except asyncio.TimeoutError:
                duration_ms = int((time.monotonic() - step_started) * 1000)
                results.append(
                    {
                        "index": index,
                        "action": sub_action,
                        "success": False,
                        "finished": False,
                        "message": f"Action timed out after {int(timeout * 1000)}ms",
                        "duration_ms": duration_ms,
                        "timeout_ms": int(timeout * 1000),
                        "timed_out": True,
                    }
                )
                stopped_reason = "action_timeout"
                break
            except Exception as exc:
                duration_ms = int((time.monotonic() - step_started) * 1000)
                results.append(
                    {
                        "index": index,
                        "action": sub_action,
                        "success": False,
                        "finished": False,
                        "message": str(exc),
                        "duration_ms": duration_ms,
                        "timeout_ms": int(timeout * 1000),
                    }
                )
                if stop_on_error:
                    stopped_reason = "action_error"
                    break

        executed = len(results)
        success = bool(results) and all(item.get("success") for item in results)
        if executed < len(actions):
            success = False
        if not stopped_reason:
            stopped_reason = "completed"
        summary = {
            "executed": executed,
            "total": len(actions),
            "stopped_reason": stopped_reason,
            "success": success,
        }
        message = json.dumps(summary, ensure_ascii=False)
        return (
            ActionResult(success=success, should_finish=False, message=message),
            screenshot,
            results,
        )

    async def _call_llm_with_tools(self) -> tuple[str, str, dict[str, Any]]:
        """调用 LLM，返回 (thinking, tool_name, tool_args)。"""
        if self._cancel_event.is_set():
            raise asyncio.CancelledError()

        kwargs: dict[str, Any] = {
            "messages": self._context,
            "model": self.model_config.model_name,
            "temperature": self.model_config.temperature,
            "tools": DEVICE_TOOLS,
            "tool_choice": "required",
        }
        if self.model_config.max_tokens is not None:
            kwargs["max_tokens"] = self.model_config.max_tokens
        if self.model_config.top_p is not None:
            kwargs["top_p"] = self.model_config.top_p
        if self.model_config.frequency_penalty is not None:
            kwargs["frequency_penalty"] = self.model_config.frequency_penalty
        if self.model_config.extra_body:
            kwargs["extra_body"] = self.model_config.extra_body

        response = await self.openai_client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message

        thinking = message.content or ""

        if message.tool_calls and len(message.tool_calls) > 0:
            tool_call = message.tool_calls[0]
            tool_name = tool_call.function.name  # type: ignore[union-attr]
            try:
                tool_args = json.loads(tool_call.function.arguments)  # type: ignore[union-attr]
            except json.JSONDecodeError as e:
                logger.warning(
                    f"Failed to parse tool arguments for {tool_name}: {e}. "
                    f"Raw: {tool_call.function.arguments!r}"  # type: ignore[union-attr]
                )
                tool_args = {}
            return thinking, tool_name, tool_args

        logger.warning("Model did not return a tool call, treating as finish")
        return thinking, "finish", {"message": thinking or "No action returned"}
