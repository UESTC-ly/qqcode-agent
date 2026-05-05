"""Claude Engineer v3 的核心 CLI 调度模块。

本文件定义了项目最重要的 `Assistant` 类：它负责装配系统提示词、
维护会话历史、动态加载工具、调用 Anthropic Messages API、处理
模型返回的 `tool_use` 指令，并把工具结果重新回填给模型继续生成。

阅读这份文件时，可以始终抓住下面这条主线：

1. 启动阶段：读取配置、校验 API Key、初始化客户端、动态加载工具；
2. 对话阶段：把用户输入写入 `conversation_history`；
3. 推理阶段：把会话历史、系统提示词和工具 schema 一起发给模型；
4. 执行阶段：如果模型返回 `tool_use`，则动态导入并执行工具；
5. 回填阶段：把 `tool_result` 写回历史，再次请求模型生成最终回复；
6. 展示阶段：在 CLI 中输出文本、工具调用情况和 token 消耗。

如果你想理解 v3 主线是如何从“用户输入”走到“真实执行”的，
这里就是最核心的入口。
"""

import anthropic
import requests
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.spinner import Spinner
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from typing import List, Dict, Any
import importlib
import inspect
import pkgutil
import os
import json
import sys
import logging
from datetime import datetime
from pathlib import Path

from config import Config
from tools.base import BaseTool
from prompt_toolkit import prompt
from prompt_toolkit.styles import Style
from prompts.system_prompts import SystemPrompts

# 配置日志：默认只显示 ERROR 及以上级别，避免终端输出过于嘈杂。
logging.basicConfig(
    level=logging.ERROR,
    format='%(levelname)s: %(message)s'
)

ORANGE_CAT_ART = r"""
        /\_/\
   ____/ o o \
 /~____  =ø= /
(______)__m_m)
"""


def build_welcome_panel() -> Panel:
    """Build the QQ-themed CLI welcome surface."""
    left_side = Group(
        Align.center(Text("Welcome back, QQ is ready!", style="bold white")),
        Padding(
            Align.center(Text(ORANGE_CAT_ART.strip("\n"), style="bold orange1")),
            (1, 0),
        ),
        Align.center(
            Text(
                f"{Config.MODEL} · QQ coding mode",
                style="bold bright_black",
            )
        ),
        Align.center(Text(str(Path.cwd()), style="bright_black")),
    )

    right_side = Group(
        Text("Tips for getting started", style="bold orange1"),
        Text("Ask QQ to inspect, edit, test, or explain this repo.", style="white"),
        Text("Run /refresh after adding tools.", style="white"),
        Text("Run /compact when the context gets large.", style="white"),
        Text("─" * 52, style="orange3"),
        Text("QQ commands", style="bold orange1"),
        Text("/reset   clear conversation history", style="bright_black"),
        Text("/save    save conversation context", style="bright_black"),
        Text("/resume  restore saved context", style="bright_black"),
        Text("/quit    exit", style="bright_black"),
        Text("─" * 52, style="orange3"),
        Text("Recent activity", style="bold orange1"),
        Text(
            "No recent activity. QQ is ready to pounce on bugs. 🐾",
            style="bright_black",
        ),
    )

    body = Table.grid(expand=True)
    body.add_column(ratio=5)
    body.add_column(width=1)
    body.add_column(ratio=7)
    body.add_row(left_side, Text("\n".join(["│"] * 13), style="orange3"), right_side)

    return Panel(
        Padding(body, (1, 2)),
        title="[bold orange1] QQ Code [/bold orange1]",
        subtitle="[bright_black]A self-improving assistant framework with tool creation[/bright_black]",
        border_style="orange3",
        box=box.ROUNDED,
    )


def display_welcome(console: Console) -> None:
    """Print the shared QQ-themed welcome surface."""
    console.print(build_welcome_panel())
    console.print("[bold orange1]Available tools:[/bold orange1]")


class Assistant:
    """
    Claude Engineer v3 的核心调度器。
    它负责管理会话历史、动态加载工具、调用 Anthropic 模型、执行工具调用，并把工具结果重新组织进对话流。

    可以把这个类理解成四个子系统的总调度台：

    - **配置与客户端初始化**：把 `Config` 中的运行参数转成可执行状态；
    - **工具注册与发现**：扫描 `tools/` 目录，生成可供模型调用的工具 schema；
    - **模型交互与对话状态维护**：负责向 Anthropic 发起请求并维护上下文；
    - **工具执行与结果回填**：当模型发起 `tool_use` 时，真正完成本地执行并继续对话。

    对外最重要的方法是 `chat()`，对内最重要的方法是 `_get_completion()`。
    """

    def __init__(self):
        """
        初始化助手实例。
        这里会校验 API Key、创建 Anthropic 客户端、初始化运行时状态，并立即加载当前可用工具。

        初始化完成后，实例会拥有几类关键状态：

        - `self.client`：Anthropic API 客户端；
        - `self.conversation_history`：当前会话历史；
        - `self.tools`：当前已注册给模型的工具 schema 列表；
        - `self.total_tokens_used`：当前会话累计消耗的 token；
        - `self.thinking_enabled` / `self.temperature`：运行时行为参数。

        因此，`Assistant()` 的构造不是轻量对象创建，而是“建立一个可工作的对话代理”。
        """
        # 初始化前先校验关键环境变量，缺失时直接中止，避免后续调用出现隐蔽错误。
        self.provider = getattr(Config, "PROVIDER", "anthropic")
        if Config.using_openai_compat():
            if not getattr(Config, "OPENAI_API_KEY", None):
                raise ValueError("No OPENAI_API_KEY found in environment variables")
            self.client = None
        else:
            if not getattr(Config, 'ANTHROPIC_API_KEY', None):
                raise ValueError("No ANTHROPIC_API_KEY found in environment variables")

            # 初始化 Anthropic 客户端，后续所有模型调用都通过它发起。
            # 如果 .env 中配置了 ANTHROPIC_BASE_URL，则请求会走自定义兼容网关。
            self.client = anthropic.Anthropic(**Config.anthropic_client_kwargs())

        self.conversation_history: List[Dict[str, Any]] = []
        self.console = Console()

        self.thinking_enabled = getattr(Config, 'ENABLE_THINKING', False)
        self.temperature = getattr(Config, 'DEFAULT_TEMPERATURE', 0.7)
        self.total_tokens_used = 0
        self.total_tokens_spent = 0
        self.current_context_tokens = 0
        self.rolling_summary = ""
        self.compact_keep_recent_messages = 8
        self.auto_compact_ratio = 0.80
        self.reserved_output_tokens = Config.MAX_TOKENS
        self.context_save_dir = Config.BASE_DIR / "saved_contexts"
        self.current_context_path = None
        self.memory_file_path = Config.PROMPTS_DIR / "QQmemory.md"
        self.file_read_context_soft_ratio = 0.70
        self.file_read_context_hard_ratio = 0.85
        self.file_read_context_preview_chars = 12000
        self.file_read_context_summary_chars = 180

        self.tools = self._load_tools()

    def _execute_uv_install(self, package_name: str) -> bool:
        """
        通过 `uvpackagemanager` 工具安装缺失依赖。
        这是工具加载失败时的恢复路径：内部构造一个模拟 tool_use，请求统一的工具执行逻辑完成安装。

        设计上它没有直接调用 `subprocess("uv ...")`，而是复用已有工具执行链。
        这样做的好处是：

        - 安装逻辑与普通工具调用保持一致；
        - 输出展示、异常处理、日志风格都能复用；
        - 核心调度器不需要为“安装依赖”单独维护一套旁路逻辑。

        返回值为布尔值：

        - `True`：安装结果看起来成功，可继续尝试重新导入目标工具；
        - `False`：安装失败，应跳过当前工具。
        """
        class ToolUseMock:
            name = "uvpackagemanager"
            input = {
                "command": "install",
                "packages": [package_name]
            }

        result = self._execute_tool(ToolUseMock())
        if "Error" not in result and "failed" not in result.lower():
            self.console.print("[green]The package was installed successfully.[/green]")
            return True
        else:
            self.console.print(f"[red]Failed to install {package_name}. Output:[/red] {result}")
            return False

    def _load_tools(self) -> List[Dict[str, Any]]:
        """
        动态扫描并加载 `tools/` 目录下的工具。
        会清理旧模块缓存、导入每个工具模块、提取 `BaseTool` 子类，并在缺依赖时尝试引导安装。

        这个函数最终返回的不是工具实例列表，而是“工具声明列表”：

        ```python
        [
            {
                "name": ...,
                "description": ...,
                "input_schema": ...
            }
        ]
        ```

        这份列表会在 `_get_completion()` 里作为 `tools=self.tools` 发给模型，
        用来告诉模型“当前有哪些工具、各自做什么、接收什么参数”。
        """
        # tools 列表最终会作为 Anthropic tools schema 发给模型。
        tools = []
        tools_path = getattr(Config, 'TOOLS_DIR', None)

        if tools_path is None:
            self.console.print("[red]TOOLS_DIR not set in Config[/red]")
            return tools

        # 清除已缓存的工具模块，确保 refresh 后能重新加载最新实现。
        # 这里故意保留 `tools.base`，避免 `BaseTool` 类型身份变化影响 `issubclass` 判断。
        for module_name in list(sys.modules.keys()):
            if module_name.startswith('tools.') and module_name != 'tools.base':
                del sys.modules[module_name]

        try:
            # 按文件系统中的模块顺序扫描 `tools/` 目录；每个 `.py` 文件都可能提供一个或多个工具类。
            for module_info in pkgutil.iter_modules([str(tools_path)]):
                if module_info.name == 'base':
                    continue

                # 尝试导入当前工具模块。
                try:
                    module = importlib.import_module(f'tools.{module_info.name}')
                    self._extract_tools_from_module(module, tools)
                except ImportError as e:
                    # 如果工具缺少依赖，则引导用户通过 uv 工具安装。
                    missing_module = self._parse_missing_dependency(str(e))
                    self.console.print(f"\n[yellow]Missing dependency:[/yellow] {missing_module} for tool {module_info.name}")
                    user_response = input(f"Would you like to install {missing_module}? (y/n): ").lower()

                    if user_response == 'y':
                        success = self._execute_uv_install(missing_module)
                        if success:
                            # 安装成功后，立即重试导入该工具模块。
                            try:
                                module = importlib.import_module(f'tools.{module_info.name}')
                                self._extract_tools_from_module(module, tools)
                            except Exception as retry_err:
                                self.console.print(f"[red]Failed to load tool after installation: {str(retry_err)}[/red]")
                        else:
                            self.console.print(f"[red]Installation of {missing_module} failed. Skipping this tool.[/red]")
                    else:
                        self.console.print(f"[yellow]Skipping tool {module_info.name} due to missing dependency[/yellow]")
                except Exception as mod_err:
                    # 单个工具模块出错时只打印错误，不让整个工具系统失效。
                    self.console.print(f"[red]Error loading module {module_info.name}:[/red] {str(mod_err)}")
        except Exception as overall_err:
            # 外层兜底：目录扫描级别出错时，至少把问题打印出来，避免静默失败。
            self.console.print(f"[red]Error in tool loading process:[/red] {str(overall_err)}")

        return tools

    def _parse_missing_dependency(self, error_str: str) -> str:
        """
        从 ImportError 文本中提取缺失依赖名。
        便于后续把缺失的包名交给 `uvpackagemanager` 安装。

        这是一个很小但很关键的辅助函数：它把“人类可读的异常文本”
        变成“后续安装逻辑可以消费的包名字符串”。

        例如：

        - 输入：`"No module named 'validators'"`
        - 输出：`"validators"`

        如果错误格式不是标准的 `No module named ...`，则退化为原样返回，
        这样至少不会因为误判而把错误信息截断成错误的包名。
        """
        if "No module named" in error_str:
            parts = error_str.split("No module named")
            missing_module = parts[-1].strip(" '\"")
        else:
            missing_module = error_str
        return missing_module

    def _extract_tools_from_module(self, module, tools: List[Dict[str, Any]]) -> None:
        """
        从一个工具模块中提取所有合法工具类。
        只有 `BaseTool` 的子类才会被实例化并注册到工具列表中。

        注意这个函数虽然返回值是 `None`，但它会**原地修改**传入的 `tools` 列表。
        也就是说，它的核心产出不是 `return`，而是 `append(...)`。

        它负责完成两件事：

        1. 在模块中筛选出真正的工具类；
        2. 把工具类压缩成模型需要的 schema 结构。

        这一步是“Python 类”到“模型工具声明”的转换层。
        """
        for name, obj in inspect.getmembers(module):
            # 只有满足以下条件的对象才算真正的工具：
            # 1) 是类；2) 继承自 BaseTool；3) 不是抽象基类 BaseTool 本身。
            if (inspect.isclass(obj) and issubclass(obj, BaseTool) and obj != BaseTool):
                try:
                    # 实例化的目的不是立即执行工具，而是读取 name / description / input_schema。
                    tool_instance = obj()
                    tools.append({
                        "name": tool_instance.name,
                        "description": tool_instance.description,
                        "input_schema": tool_instance.input_schema
                    })
                    # 成功注册后立即打印，便于在 CLI 启动或 refresh 时观察系统当前可用能力。
                    self.console.print(f"[green]Loaded tool:[/green] {tool_instance.name}")
                except Exception as tool_init_err:
                    # 工具初始化失败时只影响当前工具，不中断其他工具注册。
                    self.console.print(f"[red]Error initializing tool {name}:[/red] {str(tool_init_err)}")

    def refresh_tools(self):
        """
        重新扫描工具目录并刷新工具列表。
        除了重载工具外，还会把本轮新增的工具名称和描述打印出来。

        这个方法主要服务于 CLI 中的 `refresh` 命令。
        它的工作方式不是“增量加载”，而是：

        1. 先记录当前工具名集合；
        2. 调用 `_load_tools()` 完整重扫；
        3. 比较新旧集合，找出新增工具；
        4. 把新增工具的人类可读描述打印出来。

        这样做的好处是实现简单、行为稳定，代价是每次刷新都会完整重载全部工具。
        """
        current_tool_names = {tool['name'] for tool in self.tools}
        self.tools = self._load_tools()
        new_tool_names = {tool['name'] for tool in self.tools}
        new_tools = new_tool_names - current_tool_names

        if new_tools:
            self.console.print("\n")
            for tool_name in new_tools:
                tool_info = next((t for t in self.tools if t['name'] == tool_name), None)
                if tool_info:
                    description_lines = tool_info['description'].strip().split('\n')
                    formatted_description = '\n    '.join(line.strip() for line in description_lines)
                    self.console.print(f"[bold green]NEW[/bold green] 🔧 [cyan]{tool_name}[/cyan]:\n    {formatted_description}")
        else:
            self.console.print("\n[yellow]No new tools found[/yellow]")

    def display_available_tools(self):
        """
        打印当前已加载的工具列表。
        这是 CLI 启动后和 reset 后展示可用能力的入口。

        这里展示的是已经成功注册到 `self.tools` 的工具名，
        而不是 `tools/` 目录下的全部文件。也就是说：

        - 文件存在但导入失败的工具不会出现在这里；
        - 缺少依赖、初始化失败的工具也不会出现在这里。

        因此，这个方法展示的是“当前真实可用能力清单”。
        """
        self.console.print("\n[bold cyan]Available tools:[/bold cyan]")
        tool_names = [tool['name'] for tool in self.tools]
        if tool_names:
            formatted_tools = ", ".join([f"🔧 [cyan]{name}[/cyan]" for name in tool_names])
        else:
            formatted_tools = "No tools available."
        self.console.print(formatted_tools)
        self.console.print("\n---")

    def _display_tool_usage(self, tool_name: str, input_data: Dict, result: str):
        """
        在终端中格式化展示一次工具调用。
        当配置开启 `SHOW_TOOL_USAGE` 时，会打印工具名、输入参数和执行结果。

        这是一个纯展示方法，不参与业务逻辑判断。
        它的价值在于把原本分散的“工具名 / 输入 / 输出”统一放进一个 Rich Panel，
        让开发者在 CLI 里更容易观察代理到底做了什么。
        """
        if not getattr(Config, 'SHOW_TOOL_USAGE', False):
            return

        # 清理输入数据中的大体积二进制/base64 内容，避免终端展示失控。
        cleaned_input = self._clean_data_for_display(input_data)
        
        # 同样清理工具返回结果，提升可读性。
        cleaned_result = self._clean_data_for_display(result)

        tool_info = f"""[cyan]📥 Input:[/cyan] {json.dumps(cleaned_input, indent=2)}
[cyan]📤 Result:[/cyan] {cleaned_result}"""
        
        panel = Panel(
            tool_info,
            title=f"Tool used: {tool_name}",
            title_align="left",
            border_style="cyan",
            padding=(1, 2)
        )
        self.console.print(panel)

    def _clean_data_for_display(self, data):
        """
        清理待展示的数据。
        主要用于裁剪大体积 base64 或嵌套结构，避免工具日志在终端中过长。

        这个函数只服务于“终端展示层”，不会修改真实业务数据，也不会回写到对话历史。
        你可以把它理解为一个日志友好型视图转换器。
        """
        if isinstance(data, str):
            try:
                # 优先尝试按 JSON 解析，这样可以递归清理结构化数据。
                parsed_data = json.loads(data)
                return self._clean_parsed_data(parsed_data)
            except json.JSONDecodeError:
                # 对长字符串额外检查是否包含 base64 内容。
                if len(data) > 1000 and ';base64,' in data:
                    return "[base64 data omitted]"
                return data
        elif isinstance(data, dict):
            return self._clean_parsed_data(data)
        else:
            return data

    def _clean_parsed_data(self, data):
        """
        递归清洗结构化数据。
        会遍历字典和列表，把明显的大字段替换成占位文本。

        之所以需要递归，是因为工具结果经常是嵌套结构：

        - 外层是字典
        - 内层包含列表
        - 列表元素里又可能带有图片、base64、长文本

        如果只做浅层处理，终端日志仍然很容易失控。
        """
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                # 针对常见图片字段做更激进的裁剪，避免终端刷屏。
                if key in ['data', 'image', 'source'] and isinstance(value, str):
                    if len(value) > 1000 and (';base64,' in value or value.startswith('data:')):
                        cleaned[key] = "[base64 data omitted]"
                    else:
                        cleaned[key] = value
                else:
                    cleaned[key] = self._clean_parsed_data(value)
            return cleaned
        elif isinstance(data, list):
            return [self._clean_parsed_data(item) for item in data]
        elif isinstance(data, str) and len(data) > 1000 and ';base64,' in data:
            return "[base64 data omitted]"
        return data

    @staticmethod
    def _safe_context_name(name: str, default: str = "context") -> str:
        """Convert user-provided context names into safe file stems."""
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name.strip())
        cleaned = "-".join(part for part in cleaned.split("-") if part)
        return cleaned or default

    def _context_payload(self, **extra: Any) -> Dict[str, Any]:
        """Build the serializable payload for saved conversation contexts."""
        payload = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "total_tokens_used": getattr(self, "total_tokens_used", 0),
            "total_tokens_spent": getattr(self, "total_tokens_spent", getattr(self, "total_tokens_used", 0)),
            "current_context_tokens": getattr(self, "current_context_tokens", 0),
            "rolling_summary": getattr(self, "rolling_summary", ""),
            "conversation_history": getattr(self, "conversation_history", []),
        }
        payload.update(extra)
        return payload

    def _write_context_payload(self, path: Path, payload: Dict[str, Any]) -> str:
        """Write a context payload and remember it as the current context file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        self.current_context_path = path
        return str(path)

    def save_context(self) -> str:
        """Persist the current conversation context to a timestamped JSON file."""
        save_dir = Path(getattr(self, "context_save_dir", Config.BASE_DIR / "saved_contexts"))
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        save_path = save_dir / f"context-{timestamp}.json"
        return self._write_context_payload(save_path, self._context_payload())

    def _latest_context_path(self) -> Path:
        """Return the newest saved context file."""
        save_dir = Path(getattr(self, "context_save_dir", Config.BASE_DIR / "saved_contexts"))
        candidates = sorted(save_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
        if not candidates:
            raise FileNotFoundError(f"No saved context files found in {save_dir}")
        return candidates[-1]

    def resume_context(self, context_path: str = "") -> str:
        """Load conversation history and token usage from a saved context JSON file."""
        path = Path(context_path).expanduser() if context_path.strip() else self._latest_context_path()
        if not path.exists():
            raise FileNotFoundError(f"Context file not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        history = data.get("conversation_history", [])
        if not isinstance(history, list):
            raise ValueError("Invalid context file: conversation_history must be a list")
        self.conversation_history = history
        self.total_tokens_used = int(data.get("total_tokens_used", 0) or 0)
        self.total_tokens_spent = int(data.get("total_tokens_spent", self.total_tokens_used) or 0)
        self.current_context_tokens = int(data.get("current_context_tokens", 0) or 0)
        self.rolling_summary = str(data.get("rolling_summary", "") or "")
        self.current_context_path = path
        return str(path)

    def fork_context(self, name: str = "") -> str:
        """Save the current context as a new branch-like context file."""
        save_dir = Path(getattr(self, "context_save_dir", Config.BASE_DIR / "saved_contexts"))
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        safe_name = self._safe_context_name(name, "fork")
        fork_path = save_dir / f"{safe_name}-{timestamp}.json"
        current_path = getattr(self, "current_context_path", None)
        return self._write_context_payload(
            fork_path,
            self._context_payload(forked_from=str(current_path) if current_path else None),
        )

    def rename_context(self, name: str) -> str:
        """Rename the current saved context file to a user-provided safe name."""
        if not name.strip():
            raise ValueError("/rename requires a new context name")
        current_path = getattr(self, "current_context_path", None)
        if not current_path:
            raise ValueError("No current context file. Use /save or /resume first.")
        current_path = Path(current_path)
        if not current_path.exists():
            raise FileNotFoundError(f"Current context file not found: {current_path}")
        new_path = current_path.with_name(f"{self._safe_context_name(name)}.json")
        if new_path != current_path and new_path.exists():
            raise FileExistsError(f"Context file already exists: {new_path}")
        current_path.rename(new_path)
        self.current_context_path = new_path
        return str(new_path)

    def _memory_summary_prompt(self) -> str:
        """Build the prompt used to extract durable global memory candidates."""
        context = json.dumps(
            getattr(self, "conversation_history", []),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        return f"""You are extracting durable global memory for this repository assistant.
Only include facts, preferences, conventions, or decisions that are likely useful across future sessions.
Do not include transient task chatter, one-off errors, secrets, credentials, or bulky code/output.
Return one candidate per line as concise Markdown bullets. If nothing is worth saving, return an empty string.

Current conversation context:
{context}
"""

    def _summarize_memory_candidate(self) -> str:
        """Ask the configured LLM to summarize durable memory candidates from context."""
        prompt_text = self._memory_summary_prompt()
        if Config.using_openai_compat():
            payload = {
                "model": Config.MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Extract durable repository memory as one concise Markdown bullet per line.",
                    },
                    {"role": "user", "content": prompt_text},
                ],
                "max_tokens": min(1000, Config.MAX_TOKENS),
                "temperature": 0,
            }
            data = self._post_openai_chat_completion(payload)
            usage = data.get("usage")
            if isinstance(usage, dict):
                self._update_openai_token_usage(usage)
                self._display_token_usage(usage)
            choices = data.get("choices") or []
            if not choices:
                return ""
            return str((choices[0].get("message") or {}).get("content") or "").strip()

        if not self.client:
            return ""
        response = self.client.messages.create(
            model=Config.MODEL,
            max_tokens=min(1000, Config.MAX_TOKENS),
            temperature=0,
            messages=[{"role": "user", "content": prompt_text}],
            system="Extract durable repository memory as one concise Markdown bullet per line.",
        )
        if hasattr(response, "usage") and response.usage:
            memory_tokens = response.usage.input_tokens + response.usage.output_tokens
            self.total_tokens_used += memory_tokens
            self.total_tokens_spent = getattr(self, "total_tokens_spent", 0) + memory_tokens
            self.current_context_tokens = memory_tokens
            self._display_token_usage(response.usage)
        return self._extract_text_content(response).strip()

    def _parse_memory_candidates(self, candidate_text: str) -> List[str]:
        """Split LLM memory output into selectable candidate bullets."""
        candidates: List[str] = []
        for raw_line in candidate_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(('-', '*')):
                line = line[1:].strip()
            else:
                parts = line.split('.', 1)
                if len(parts) == 2 and parts[0].strip().isdigit():
                    line = parts[1].strip()
            if line:
                candidates.append(line)
        if not candidates and candidate_text.strip():
            candidates.append(candidate_text.strip())
        return candidates

    def _select_memory_candidates(self, candidates: List[str], selection: str) -> List[str]:
        """Select memory candidates from a user selection string."""
        normalized = selection.strip().lower()
        if not normalized or normalized in {"n", "no", "none", "cancel"}:
            return []
        if normalized in {"y", "yes", "all", "a"}:
            return candidates

        selected: List[str] = []
        for part in normalized.replace("，", ",").split(','):
            part = part.strip()
            if not part:
                continue
            if part.isdigit():
                index = int(part) - 1
                if 0 <= index < len(candidates):
                    selected.append(candidates[index])
        return selected

    def _format_memory_candidates(self, candidates: List[str]) -> str:
        """Render numbered memory candidates for user selection."""
        return "\n".join(f"{index}. {candidate}" for index, candidate in enumerate(candidates, 1))

    def _memory_items_from_text(self, text: str) -> List[str]:
        """Normalize markdown-ish memory text into unique bullet items."""
        items: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(('-', '*')):
                line = line[1:].strip()
            elif line.startswith("##"):
                continue
            if line and line not in items:
                items.append(line)
        return items

    def _memory_category_for_item(self, item: str) -> str:
        """Choose a stable QQmemory.md category for one memory item."""
        lowered = item.lower()
        if any(word in lowered for word in ["context", "memory", "artifact", "token", "compact"]):
            return "Context & Memory"
        if any(word in lowered for word in ["/", "command", "convention", "style", "prefer", "use"]):
            return "Project Conventions"
        return "General"

    def _render_memory_document(self, existing_text: str, new_items: List[str]) -> str:
        """Render categorized, deduplicated global memory."""
        categorized: Dict[str, List[str]] = {}
        for item in self._memory_items_from_text(existing_text):
            category = self._memory_category_for_item(item)
            categorized.setdefault(category, [])
            if item not in categorized[category]:
                categorized[category].append(item)

        for item in new_items:
            normalized = item.strip()
            if normalized.startswith(('-', '*')):
                normalized = normalized[1:].strip()
            if not normalized:
                continue
            category = self._memory_category_for_item(normalized)
            categorized.setdefault(category, [])
            if normalized not in categorized[category]:
                categorized[category].append(normalized)

        category_order = ["Project Conventions", "Context & Memory", "General"]
        for category in sorted(categorized):
            if category not in category_order:
                category_order.append(category)

        parts = ["# QQ Memory"]
        for category in category_order:
            items = categorized.get(category, [])
            if not items:
                continue
            parts.append(f"## {category}")
            parts.extend(f"- {item}" for item in items)
        return "\n\n".join(parts) + "\n"

    def _append_global_memory(self, candidate: str) -> str:
        """Merge confirmed memory into prompts/QQmemory.md by category without duplicates."""
        memory_path = Path(getattr(self, "memory_file_path", Config.PROMPTS_DIR / "QQmemory.md"))
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        existing = memory_path.read_text(encoding="utf-8") if memory_path.exists() else ""
        new_items = self._memory_items_from_text(candidate)
        rendered = self._render_memory_document(existing, new_items)
        memory_path.write_text(rendered, encoding="utf-8")
        return str(memory_path)

    def memory_command(self) -> str:
        """Summarize durable memory candidates and let the user choose what to save."""
        candidate_text = self._summarize_memory_candidate().strip()
        candidates = self._parse_memory_candidates(candidate_text)
        if not candidates:
            return "No global memory candidate found."

        rendered_candidates = self._format_memory_candidates(candidates)
        self.console.print("\n[bold cyan]Memory candidates:[/bold cyan]")
        self.console.print(rendered_candidates)
        answer = prompt(
            "Save which memories to global QQmemory.md? "
            "Enter numbers like 1,3, 'all', or press Enter to cancel: "
        )
        selected = self._select_memory_candidates(candidates, answer)
        if not selected:
            return f"Memory candidates:\n{rendered_candidates}\n\nMemory not saved."

        selected_markdown = "\n".join(f"- {candidate}" for candidate in selected)
        memory_path = self._append_global_memory(selected_markdown)
        return f"Memory saved to {memory_path}"

    def _handle_local_command(self, user_input: str) -> str | None:
        """Handle slash-prefixed local commands; return None for normal prompts."""
        stripped = user_input.strip()
        if not stripped.startswith("/"):
            return None

        command_text = stripped[1:]
        command, _, args = command_text.partition(" ")
        command = command.lower()
        args = args.strip()

        try:
            if command == "refresh":
                self.refresh_tools()
                return "Tools refreshed successfully!"
            if command == "reset":
                self.reset()
                return "Conversation reset!"
            if command == "save":
                saved_path = self.save_context()
                return f"Context saved to {saved_path}"
            if command == "resume":
                resumed_path = self.resume_context(args)
                return f"Context resumed from {resumed_path}"
            if command == "fork":
                forked_path = self.fork_context(args)
                return f"Context forked to {forked_path}"
            if command == "rename":
                renamed_path = self.rename_context(args)
                return f"Context renamed to {renamed_path}"
            if command == "memory":
                return self.memory_command()
            if command == "compact":
                return self.compact_context()
            if command == "quit":
                return "Goodbye!"
        except Exception as e:
            return f"Error: {str(e)}"

        return f"Unknown local command: /{command}"

    def _estimate_tokens_for_text(self, text: str) -> int:
        """Cheap request-side token estimate; avoids waiting for provider usage."""
        return max(1, len(text) // 4)

    def _estimate_context_tokens(self, system_prompt: str | None = None) -> int:
        """Estimate current request context tokens from system prompt and messages."""
        payload = {
            "system": system_prompt if system_prompt is not None else self._system_prompt(),
            "rolling_summary": getattr(self, "rolling_summary", ""),
            "messages": getattr(self, "conversation_history", []),
            "tools": getattr(self, "tools", []),
        }
        return self._estimate_tokens_for_text(json.dumps(payload, ensure_ascii=False, default=str))

    def _refresh_current_context_tokens(self, system_prompt: str | None = None) -> int:
        """Refresh the current-context token estimate and return it."""
        self.current_context_tokens = self._estimate_context_tokens(system_prompt)
        return self.current_context_tokens

    def _summarize_messages_for_compact(self, messages: List[Dict[str, Any]]) -> str:
        """Create a deterministic compact summary for older messages."""
        if not messages:
            return getattr(self, "rolling_summary", "")
        snippets = []
        for message in messages:
            role = message.get("role", "unknown") if isinstance(message, dict) else "unknown"
            content = message.get("content", "") if isinstance(message, dict) else message
            text = json.dumps(content, ensure_ascii=False, default=str)
            if len(text) > 240:
                text = text[:240] + "..."
            snippets.append(f"- {role}: {text}")
        previous = getattr(self, "rolling_summary", "").strip()
        compacted = "\n".join(snippets)
        if previous:
            return f"{previous}\n{compacted}"
        return compacted

    def compact_context(self) -> str:
        """Compact old conversation history into a rolling summary."""
        keep = max(1, int(getattr(self, "compact_keep_recent_messages", 8)))
        history = getattr(self, "conversation_history", [])
        if len(history) <= keep:
            self._refresh_current_context_tokens()
            return "Context compacted: nothing to compact."
        old_messages = history[:-keep]
        self.rolling_summary = self._summarize_messages_for_compact(old_messages)
        self.conversation_history = history[-keep:]
        self._refresh_current_context_tokens()
        return f"Context compacted: summarized {len(old_messages)} old messages; kept {keep} recent messages."

    def _ensure_context_budget(self, system_prompt: str | None = None) -> None:
        """Compact before a model request if estimated context is above budget."""
        limit = max(1, int(getattr(Config, "MAX_CONVERSATION_TOKENS", 1)))
        current = self._refresh_current_context_tokens(system_prompt)
        if current / limit >= float(getattr(self, "auto_compact_ratio", 0.80)):
            self.compact_context()
            self._refresh_current_context_tokens(system_prompt)

    def _context_usage_ratio(self) -> float:
        """Return the approximate current context usage ratio."""
        limit = max(1, int(getattr(Config, "MAX_CONVERSATION_TOKENS", 1)))
        if hasattr(self, "current_context_tokens"):
            used = max(0, int(getattr(self, "current_context_tokens", 0)))
            if not used and not getattr(self, "conversation_history", []):
                used = max(0, int(getattr(self, "total_tokens_used", 0)))
            elif not used:
                used = self._refresh_current_context_tokens()
        else:
            used = max(0, int(getattr(self, "total_tokens_used", 0)))
        return used / limit

    def _artifact_dir(self) -> Path:
        """Return the directory used for large context artifacts."""
        return Path(getattr(self, "context_save_dir", Config.BASE_DIR / "saved_contexts")) / "artifacts"

    def _save_context_artifact(self, content: Any, suffix: str = ".json") -> str:
        """Persist full tool output outside the model context and return its path."""
        artifact_dir = self._artifact_dir()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        artifact_path = artifact_dir / f"artifact-{timestamp}{suffix}"
        if isinstance(content, str):
            artifact_text = content
        else:
            artifact_text = json.dumps(content, ensure_ascii=False, indent=2, default=str)
        artifact_path.write_text(artifact_text, encoding="utf-8")
        return str(artifact_path)

    def _parse_file_read_result(self, result: Any) -> Dict[str, Any]:
        """Parse filecontentreadertool JSON output into a path -> content mapping."""
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _extract_file_paths_from_read_result(self, result: Any) -> List[str]:
        """Return file paths included in a file read result."""
        return list(self._parse_file_read_result(result).keys())

    def _minimal_file_summary(self, file_contents: Dict[str, Any]) -> str:
        """Build a tiny file-path-focused summary for critical context pressure."""
        lines = []
        max_summary_chars = int(getattr(self, "file_read_context_summary_chars", 180))
        for path, content in file_contents.items():
            text = str(content)
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            if len(first_line) > max_summary_chars:
                first_line = first_line[:max_summary_chars] + "..."
            lines.append(f"- {path}: {len(text)} chars; preview={first_line!r}")
        return "\n".join(lines)

    def _prepare_tool_result_for_context(self, tool_use: Any, result: Any) -> Any:
        """Reduce large file-read tool results before storing them in conversation history."""
        tool_name = self._block_attr(tool_use, "name")
        if tool_name != "filecontentreadertool":
            return result

        file_contents = self._parse_file_read_result(result)
        if not file_contents:
            return result

        ratio = self._context_usage_ratio()
        if ratio < float(getattr(self, "file_read_context_soft_ratio", 0.70)):
            return result

        artifact_path = self._save_context_artifact(file_contents)
        file_paths = list(file_contents.keys())
        total_chars = sum(len(str(content)) for content in file_contents.values())

        if ratio >= float(getattr(self, "file_read_context_hard_ratio", 0.85)):
            summary = self._minimal_file_summary(file_contents)
            return (
                "[file read result minimized]\n"
                f"context_usage_ratio: {ratio:.2%}\n"
                f"files_read: {len(file_paths)}\n"
                f"total_chars: {total_chars}\n"
                f"artifact_path: {artifact_path}\n"
                "files:\n"
                f"{summary}"
            )

        preview_limit = int(getattr(self, "file_read_context_preview_chars", 12000))
        preview_file_contents = {}
        per_file_limit = min(800, max(200, preview_limit // max(1, len(file_contents))))
        for path, content in file_contents.items():
            text = str(content)
            if len(text) > per_file_limit:
                text = (
                    text[:per_file_limit]
                    + f"\n...[truncated; full content in artifact: {artifact_path}]"
                )
            preview_file_contents[path] = text
        preview = json.dumps(preview_file_contents, ensure_ascii=False, indent=2, default=str)
        if len(preview) > preview_limit:
            preview = preview[:preview_limit] + "\n...[truncated]"
        return (
            "[file read result truncated]\n"
            f"context_usage_ratio: {ratio:.2%}\n"
            f"files_read: {len(file_paths)}\n"
            f"total_chars: {total_chars}\n"
            f"artifact_path: {artifact_path}\n"
            "preview:\n"
            f"{preview}"
        )

    def _tool_result_file_paths(self, tool_result: Dict[str, Any]) -> List[str]:
        """Return tracked file paths from a stored file-read tool_result."""
        if tool_result.get("source_tool_name") != "filecontentreadertool":
            return []
        paths = tool_result.get("file_paths")
        return paths if isinstance(paths, list) else []

    def _drop_previous_file_read_results(self, file_paths: List[str]) -> None:
        """Replace older file read results for matching paths with compact stale markers."""
        target_paths = set(file_paths)
        if not target_paths:
            return

        for message in getattr(self, "conversation_history", []):
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                old_paths = set(self._tool_result_file_paths(block))
                overlap = sorted(target_paths & old_paths)
                if not overlap:
                    continue
                block["content"] = [
                    {
                        "type": "text",
                        "text": f"[stale file read omitted; newer read kept for: {', '.join(overlap)}]",
                    }
                ]
                block["stale"] = True

    def _make_anthropic_tool_result(self, tool_use: Any, result: Any) -> Dict[str, Any]:
        """Build a tool_result block after context-size management."""
        processed_result = self._prepare_tool_result_for_context(tool_use, result)
        tool_result = {
            "type": "tool_result",
            "tool_use_id": self._block_attr(tool_use, "id"),
        }
        if self._block_attr(tool_use, "name") == "filecontentreadertool":
            file_paths = self._extract_file_paths_from_read_result(result)
            self._drop_previous_file_read_results(file_paths)
            tool_result["source_tool_name"] = "filecontentreadertool"
            tool_result["file_paths"] = file_paths

        if isinstance(processed_result, (list, dict)):
            tool_result["content"] = processed_result
        else:
            tool_result["content"] = [{"type": "text", "text": str(processed_result)}]
        return tool_result

    def _execute_tool(self, tool_use):
        """
        执行一次模型发起的工具调用。
        它会按工具名动态导入模块、找到匹配工具实例、执行 `execute()`，并统一处理异常与展示。

        这一层和 `_load_tools()` 的区别非常重要：

        - `_load_tools()` 负责“让模型看见工具”；
        - `_execute_tool()` 负责“真正把工具跑起来”。

        因此这里处理的是运行时执行，而不是注册时扫描。
        """
        tool_name = self._block_attr(tool_use, "name")
        tool_input = self._block_attr(tool_use, "input", {}) or {}
        tool_result = None

        try:
            # 根据模型返回的工具名再次动态导入模块；这意味着工具名需要与模块名保持一致。
            module = importlib.import_module(f'tools.{tool_name}')
            tool_instance = self._find_tool_instance_in_module(module, tool_name)

            if not tool_instance:
                tool_result = f"Tool not found: {tool_name}"
            else:
                # 使用模型给出的输入参数执行目标工具。
                try:
                    result = tool_instance.execute(**tool_input)
                    # 如果工具返回结构化数据，则原样保留，不强行转成纯文本。
                    tool_result = result
                except Exception as exec_err:
                    tool_result = f"Error executing tool '{tool_name}': {str(exec_err)}"
        except ImportError:
            tool_result = f"Failed to import tool: {tool_name}"
        except Exception as e:
            tool_result = f"Error executing tool: {str(e)}"

        # 无论成功失败，最终都走同一套展示逻辑，保证 CLI 观测体验一致。
        self._display_tool_usage(tool_name, tool_input, 
            json.dumps(tool_result) if not isinstance(tool_result, str) else tool_result)
        return tool_result

    def _find_tool_instance_in_module(self, module, tool_name: str):
        """
        在模块中查找与目标名称匹配的工具实例。
        通过比较 `candidate_tool.name` 和模型要求的工具名来完成匹配。

        这里再次扫描模块，是因为执行阶段需要的是“可调用实例”，
        而不是注册阶段保存下来的 schema 字典。

        这也说明本项目没有维护长期驻留的工具实例池，而是按需实例化工具。
        好处是简单；代价是每次调用都要重新构造实例。
        """
        for name, obj in inspect.getmembers(module):
            if (inspect.isclass(obj) and issubclass(obj, BaseTool) and obj != BaseTool):
                candidate_tool = obj()
                if candidate_tool.name == tool_name:
                    return candidate_tool
        return None



    @staticmethod
    def _block_attr(content_block: Any, attr_name: str, default: Any = None) -> Any:
        """Return an attribute from SDK objects or dict-compatible content blocks."""
        if isinstance(content_block, dict):
            return content_block.get(attr_name, default)
        return getattr(content_block, attr_name, default)

    @staticmethod
    def _block_type(content_block: Any) -> Any:
        """Return a content block type from SDK objects or dict-compatible responses."""
        return Assistant._block_attr(content_block, "type")

    @staticmethod
    def _block_text(content_block: Any) -> Any:
        """Return text from SDK objects or dict-compatible responses."""
        return Assistant._block_attr(content_block, "text")

    @classmethod
    def _extract_text_content(cls, response: Any) -> str:
        """Extract final assistant text from all text content blocks.

        Anthropic-compatible providers may return non-text blocks such as
        thinking/reasoning before the final text block. Reading only
        response.content[0].text can therefore surface None even when useful
        text exists later in the response.
        """
        content = getattr(response, "content", None)
        if not isinstance(content, list):
            return ""

        text_parts = []
        for content_block in content:
            if cls._block_type(content_block) == "text":
                text = cls._block_text(content_block)
                if text:
                    text_parts.append(str(text))

        return "\n".join(text_parts)

    def _display_token_usage(self, usage):
        """
        展示当前累计 token 使用情况。
        会按总量计算使用百分比、剩余 token，并以文本进度条方式输出。

        这里展示的是**当前整个会话累计值**，而不是本次单请求值。
        也就是说，用户看到的是“这段对话已经花了多少 token”，
        而不是“刚刚这一轮花了多少 token”。
        """
        context_tokens = getattr(self, "current_context_tokens", self.total_tokens_used)
        total_spent = getattr(self, "total_tokens_spent", self.total_tokens_used)
        used_percentage = (context_tokens / Config.MAX_CONVERSATION_TOKENS) * 100
        remaining_tokens = max(0, Config.MAX_CONVERSATION_TOKENS - context_tokens)

        self.console.print(f"\nContext: {context_tokens:,} / {Config.MAX_CONVERSATION_TOKENS:,}")
        self.console.print(f"Total spent: {total_spent:,}")

        bar_width = 40
        filled = int(used_percentage / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)

        color = "green"
        if used_percentage > 75:
            color = "yellow"
        if used_percentage > 90:
            color = "red"

        self.console.print(f"[{color}][{bar}] {used_percentage:.1f}%[/{color}]")

        if remaining_tokens < 20000:
            self.console.print(f"[bold red]Warning: Only {remaining_tokens:,} tokens remaining![/bold red]")

        self.console.print("---")


    def _global_memory_prompt(self) -> str:
        """Read durable global memory from prompts/QQmemory.md if it exists."""
        memory_path = Path(getattr(self, "memory_file_path", Config.PROMPTS_DIR / "QQmemory.md"))
        if not memory_path.exists():
            return ""
        try:
            return memory_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logging.error(f"Error reading global memory: {str(e)}")
            return ""

    def _system_prompt(self) -> str:
        """Build the system prompt, including durable global memory when present."""
        parts = [SystemPrompts.DEFAULT, SystemPrompts.TOOL_USAGE]
        rolling_summary = getattr(self, "rolling_summary", "").strip()
        if rolling_summary:
            parts.append(f"Rolling conversation summary:\n{rolling_summary}")
        memory = self._global_memory_prompt()
        if memory:
            parts.append(
                "Global memory from QQmemory.md:\n"
                "These are durable user/project preferences and decisions confirmed by the user.\n"
                f"{memory}"
            )
        return "\n\n".join(part for part in parts if part)

    def _openai_messages(self, system_prompt: str) -> List[Dict[str, Any]]:
        """Build OpenAI-compatible messages, including system instructions."""
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(self.conversation_history)
        return messages

    def _openai_tools(self) -> List[Dict[str, Any]]:
        """Convert Anthropic-style tool schemas to OpenAI function tools."""
        openai_tools: List[Dict[str, Any]] = []
        for tool in self.tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object"}),
                },
            })
        return openai_tools

    def _update_openai_token_usage(self, usage: Dict[str, Any]) -> None:
        """Update token totals from OpenAI-compatible usage dictionaries."""
        total_tokens = usage.get("total_tokens")
        if total_tokens is None:
            total_tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        total = int(total_tokens or prompt_tokens + completion_tokens or 0)
        self.total_tokens_used += total
        self.total_tokens_spent = getattr(self, "total_tokens_spent", 0) + total
        self.current_context_tokens = prompt_tokens + completion_tokens if prompt_tokens else self._refresh_current_context_tokens()

    def _openai_payload(self, system_prompt: str) -> Dict[str, Any]:
        """Build a Chat Completions payload for OpenAI-compatible providers."""
        payload: Dict[str, Any] = {
            "model": Config.MODEL,
            "messages": self._openai_messages(system_prompt),
            "max_tokens": min(
                Config.MAX_TOKENS,
                Config.MAX_CONVERSATION_TOKENS - getattr(self, "current_context_tokens", 0),
            ),
            "temperature": self.temperature,
        }
        tools = self._openai_tools()
        if tools:
            payload["tools"] = tools
        return payload

    def _post_openai_chat_completion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Call an OpenAI-compatible Chat Completions endpoint."""
        response = requests.post(
            Config.openai_chat_completions_url(),
            headers={
                "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    def _tool_use_from_openai_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Convert an OpenAI tool call to the local tool-use shape."""
        function = tool_call.get("function", {})
        raw_arguments = function.get("arguments") or "{}"
        try:
            tool_input = json.loads(raw_arguments)
        except json.JSONDecodeError:
            tool_input = {}
        return {
            "type": "tool_use",
            "id": tool_call.get("id"),
            "name": function.get("name"),
            "input": tool_input,
        }

    def _get_openai_completion(self) -> str:
        """Get one completion from an OpenAI-compatible Chat Completions API."""
        try:
            system_prompt = self._system_prompt()
            self._ensure_context_budget(system_prompt)
            data = self._post_openai_chat_completion(self._openai_payload(system_prompt))

            usage = data.get("usage")
            if isinstance(usage, dict):
                self._update_openai_token_usage(usage)
                self._display_token_usage(usage)

            if getattr(self, "current_context_tokens", self.total_tokens_used) >= Config.MAX_CONVERSATION_TOKENS:
                self.console.print("\n[bold red]Token limit reached! Please reset the conversation.[/bold red]")
                return "Token limit reached! Please type 'reset' to start a new conversation."

            choices = data.get("choices") or []
            if not choices:
                return "No response choices available."

            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                assistant_message = {"role": "assistant", "content": message.get("content")}
                assistant_message["tool_calls"] = tool_calls
                self.conversation_history.append(assistant_message)

                for tool_call in tool_calls:
                    tool_use = self._tool_use_from_openai_tool_call(tool_call)
                    result = self._execute_tool(tool_use)
                    processed_result = self._prepare_tool_result_for_context(tool_use, result)
                    if self._block_attr(tool_use, "name") == "filecontentreadertool":
                        self._drop_previous_file_read_results(
                            self._extract_file_paths_from_read_result(result)
                        )
                    if isinstance(processed_result, (dict, list)):
                        tool_content = json.dumps(processed_result, ensure_ascii=False)
                    else:
                        tool_content = str(processed_result)
                    self.conversation_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": tool_content,
                    })
                return self._get_openai_completion()

            content = message.get("content")
            if content:
                self.conversation_history.append({
                    "role": "assistant",
                    "content": content,
                })
                return str(content)

            return "No response content available."

        except Exception as e:
            logging.error(f"Error in _get_openai_completion: {str(e)}")
            return f"Error: {str(e)}"

    def _get_completion(self):
        """
        向 Anthropic 获取一次完整回复。
        这是项目最核心的方法：既处理普通文本回复，也处理 `tool_use` 分支，并通过递归完成工具链调用。

        这个方法的核心职责可以拆成四步：

        1. 发起模型请求；
        2. 更新 token 统计；
        3. 如果模型要求用工具，则执行工具并把结果回填；
        4. 如果模型已给出最终文本，则返回最终结果。

        从“代理系统”视角看，这里才是真正的执行主循环。
        """
        if Config.using_openai_compat():
            return self._get_openai_completion()

        try:
            # 第一步：把当前会话历史、可用工具和系统提示词一起发送给模型。
            system_prompt = self._system_prompt()
            self._ensure_context_budget(system_prompt)
            response = self.client.messages.create(
                model=Config.MODEL,
                max_tokens=min(
                    Config.MAX_TOKENS,
                    Config.MAX_CONVERSATION_TOKENS - getattr(self, "current_context_tokens", 0)
                ),
                temperature=self.temperature,
                tools=self.tools,
                messages=self.conversation_history,
                system=system_prompt
            )

            # 根据 API 返回的 usage 更新累计 token 统计。
            # 这里统计的是会话累计值，因此每一轮都会在此前基础上继续增加。
            if hasattr(response, 'usage') and response.usage:
                message_tokens = response.usage.input_tokens + response.usage.output_tokens
                self.total_tokens_used += message_tokens
                self.total_tokens_spent = getattr(self, "total_tokens_spent", 0) + message_tokens
                self.current_context_tokens = response.usage.input_tokens + response.usage.output_tokens
                self._display_token_usage(response.usage)

            if getattr(self, "current_context_tokens", self.total_tokens_used) >= Config.MAX_CONVERSATION_TOKENS:
                self.console.print("\n[bold red]Token limit reached! Please reset the conversation.[/bold red]")
                return "Token limit reached! Please type 'reset' to start a new conversation."

            # 第二步：如果模型要求使用工具，则进入工具执行分支。
            # Anthropic 在这种情况下不会直接给出最终自然语言，而是要求宿主先执行工具。
            content_blocks = getattr(response, 'content', None)
            has_tool_use = (
                isinstance(content_blocks, list)
                and any(self._block_type(content_block) == "tool_use" for content_block in content_blocks)
            )
            if response.stop_reason == "tool_use" or has_tool_use:
                self.console.print("\n[bold yellow]  Handling Tool Use...[/bold yellow]\n")

                tool_results = []
                if content_blocks and isinstance(content_blocks, list):
                    # 遍历模型请求的全部工具调用并逐个执行。
                    for content_block in content_blocks:
                        if self._block_type(content_block) == "tool_use":
                            result = self._execute_tool(content_block)
                            
                            # 文件读取等大结果在进入上下文前会先按当前压力降级。
                            tool_results.append(self._make_anthropic_tool_result(content_block, result))

                    # 把本轮 assistant/tool 往返写回历史，再递归继续对话。
                    # 先记录 assistant 发出的 tool_use，再记录 tool_result，保持消息序列完整。
                    self.conversation_history.append({
                        "role": "assistant",
                        "content": response.content
                    })
                    self.conversation_history.append({
                        "role": "user",
                        "content": tool_results
                    })
                    return self._get_completion()  # 递归继续对话，直到模型给出最终自然语言回复。

                else:
                    self.console.print("[red]No tool content received despite 'tool_use' stop reason.[/red]")
                    return "Error: No tool content received"

            # 走到这里说明模型已经给出了最终自然语言回复。
            # 这是一次完整工具链结束后的正常收束分支。
            if (getattr(response, 'content', None) and 
                isinstance(response.content, list) and 
                response.content):
                final_content = self._extract_text_content(response)
                self.conversation_history.append({
                    "role": "assistant",
                    "content": response.content
                })
                if final_content:
                    return final_content

                stop_reason = getattr(response, "stop_reason", "unknown")
                self.console.print("[red]No text content in final response.[/red]")
                return f"No text content returned. stop_reason={stop_reason}"
            else:
                self.console.print("[red]No content in final response.[/red]")
                return "No response content available."

        except Exception as e:
            logging.error(f"Error in _get_completion: {str(e)}")
            return f"Error: {str(e)}"

    def chat(self, user_input):
        """
        处理一轮用户输入。
        会先拦截本地命令，再把普通文本或多模态输入写入历史，并触发模型调用。

        `chat()` 是对外最重要的统一入口：

        - CLI 调用它；
        - Web 端也间接调用它；
        - 文本输入和多模态输入都通过它进入系统。

        因此你可以把它理解为“本地交互层”到“代理执行层”的总入口。
        """
        # 文本模式下仅支持 / 前缀本地命令，避免和普通 prompt 混淆。
        # 第一步：先处理本地命令；这些命令不会发送给模型。
        if isinstance(user_input, str):
            command_response = self._handle_local_command(user_input)
            if command_response is not None:
                return command_response

        try:
            # 第二步：把用户输入写入历史，保证后续模型调用能看到完整上下文。
            # 先把用户输入写入会话历史，再统一交给模型处理。
            self.conversation_history.append({
                "role": "user",
                "content": user_input  # 这里既可能是纯文本字符串，也可能是多模态内容列表。
            })

            # 如果开启 thinking，则在等待模型期间显示加载动画。
            # 第三步：根据配置决定是否显示思考动画，然后真正请求模型。
            if self.thinking_enabled:
                with Live(Spinner('dots', text='Thinking...', style="cyan"), 
                         refresh_per_second=10, transient=True):
                    response = self._get_completion()
            else:
                response = self._get_completion()

            return response

        except Exception as e:
            logging.error(f"Error in chat: {str(e)}")
            return f"Error: {str(e)}"

    def reset(self):
        """
        重置助手状态。
        会清空会话历史和 token 统计，并重新展示欢迎文本与可用工具列表。

        这个方法不会销毁 `Assistant` 实例本身，也不会重新创建客户端；
        它只是把“对话状态”恢复到初始状态，相当于一次软重置。
        """
        self.conversation_history = []
        self.total_tokens_used = 0
        self.total_tokens_spent = 0
        self.current_context_tokens = 0
        self.rolling_summary = ""
        self.console.print("\n[bold green]🔄 Assistant memory has been reset![/bold green]")

        display_welcome(self.console)
        self.display_available_tools()


def main():
    """
    CLI 程序入口。
    负责初始化 Assistant、展示欢迎信息，并在循环中持续读取用户输入。

    这个函数不做复杂推理，它主要承担三层职责：

    - **启动职责**：创建 `Assistant` 并打印欢迎信息；
    - **交互职责**：循环读取用户输入；
    - **分发职责**：把输入交给 `assistant.chat()`，并把结果打印回终端。

    所以它更像一个“命令行壳层”，而不是业务核心。
    """
    console = Console()
    style = Style.from_dict({'prompt': 'ansimagenta'})

    # 启动 CLI 前先初始化 Assistant；如果密钥缺失，会在这里直接报错。
    # 也就是说，CLI 的真正业务能力全部托管在 Assistant 内部，而不是 main 自己实现。
    try:
        assistant = Assistant()
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {str(e)}")
        console.print("Please ensure ANTHROPIC_API_KEY is set correctly.")
        return

    display_welcome(console)
    assistant.display_available_tools()

    # CLI 主循环：读取用户输入，处理本地命令，或把请求交给 Assistant。
    while True:
        try:
            user_input = prompt("You: ", style=style).strip()

            command = user_input.strip().lower()
            if command == '/quit':
                console.print("\n[bold blue]👋 Goodbye![/bold blue]")
                break

            response = assistant.chat(user_input)
            console.print("\n[bold orange1]QQ:[/bold orange1]")
            if isinstance(response, str):
                safe_response = response.replace('[', '\\[').replace(']', '\\]')
                console.print(safe_response)
            else:
                console.print(str(response))

        except KeyboardInterrupt:
            continue
        except EOFError:
            break


if __name__ == "__main__":
    main()
