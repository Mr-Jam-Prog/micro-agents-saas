"""
Interactive utilities for MicroAgents Platform CLI.
Advanced prompts, autocomplete, syntax highlighting, and wizard flows.
"""

import asyncio
import json
import os
import re
import shlex
import sys
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import Callable, Coroutine, Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import pygments
from pygments.formatters import TerminalFormatter
from pygments.lexers import (
    PythonLexer,
    JsonLexer,
    YamlLexer,
    get_lexer_by_name,
    guess_lexer,
)
from prompt_toolkit import Application, HTML
from prompt_toolkit.application.current import get_app
from prompt_toolkit.auto_suggest import AutoSuggest, AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import (
    Completer,
    Completion,
    CompleteEvent,
    FuzzyCompleter,
    NestedCompleter,
    WordCompleter,
)
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.formatted_text import (
    FormattedText,
    StyleAndTextTuples,
    to_formatted_text,
)
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    ConditionalContainer,
    Container,
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    ScrollablePane,
    VSplit,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (
    AfterInput,
    BeforeInput,
    ConditionalProcessor,
    HighlightMatchingBracketProcessor,
    HighlightSelectionProcessor,
    PasswordProcessor,
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.lexers import Lexer, SimpleLexer
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.shortcuts import (
    button_dialog,
    checkboxlist_dialog,
    input_dialog,
    message_dialog,
    progress_bar,
    radiolist_dialog,
    yes_no_dialog,
)
from prompt_toolkit.styles import Style, merge_styles
from prompt_toolkit.validation import Validator, ValidationError
from prompt_toolkit.widgets import (
    Box,
    Button,
    Checkbox,
    CheckboxList,
    Dialog,
    Frame,
    HorizontalLine,
    Label,
    MenuContainer,
    MenuItem,
    ProgressBar,
    RadioList,
    TextArea,
    ValidationToolbar,
)
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from ..utils.formatting import Formatter, get_formatter

# Type variables
T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")

# Global console for rich output
_console = Console()


class ThemeMode(str, Enum):
    """Theme modes for interactive sessions."""
    
    DARK = "dark"
    LIGHT = "light"
    HIGH_CONTRAST = "high_contrast"
    CUSTOM = "custom"


class ValidationType(str, Enum):
    """Types of input validation."""
    
    REQUIRED = "required"
    EMAIL = "email"
    URL = "url"
    INTEGER = "integer"
    FLOAT = "float"
    REGEX = "regex"
    RANGE = "range"
    CUSTOM = "custom"


class SelectionMode(str, Enum):
    """Modes for selection prompts."""
    
    SINGLE = "single"
    MULTIPLE = "multiple"
    TOGGLE = "toggle"


@dataclass
class ThemeConfig:
    """Configuration for interactive themes."""
    
    mode: ThemeMode = ThemeMode.DARK
    colors: Dict[str, str] = field(default_factory=dict)
    styles: Dict[str, str] = field(default_factory=dict)
    
    def to_prompt_toolkit_style(self) -> Style:
        """Convert to prompt_toolkit Style."""
        if self.mode == ThemeMode.DARK:
            base_style = self._get_dark_theme()
        elif self.mode == ThemeMode.LIGHT:
            base_style = self._get_light_theme()
        elif self.mode == ThemeMode.HIGH_CONTRAST:
            base_style = self._get_high_contrast_theme()
        else:
            base_style = {}
        
        # Merge with custom colors and styles
        base_style.update(self.colors)
        base_style.update(self.styles)
        
        return Style.from_dict(base_style)
    
    def _get_dark_theme(self) -> Dict[str, str]:
        """Get dark theme configuration."""
        return {
            # Basic colors
            "": "#c0c0c0",
            "bg": "#1e1e1e",
            "fg": "#d4d4d4",
            
            # UI elements
            "dialog": "bg:#2d2d2d",
            "dialog.border": "#555555",
            "dialog.title": "bold #ffffff",
            "dialog.body": "#d4d4d4",
            "dialog.shadow": "bg:#000000",
            
            # Text areas
            "textarea": "#d4d4d4",
            "textarea.border": "#555555",
            "textarea.focused": "#d4d4d4",
            "textarea.focused.border": "#007acc",
            
            # Buttons
            "button": "bg:#0e639c #ffffff",
            "button.focused": "bg:#007acc #ffffff",
            "button.hover": "bg:#1177bb #ffffff",
            "button.disabled": "bg:#555555 #888888",
            
            # Lists
            "list": "#d4d4d4",
            "list.border": "#555555",
            "list.selected": "bg:#007acc #ffffff",
            "list.hover": "bg:#2a2d2e",
            "list.focused": "bg:#04395e",
            
            # Progress bars
            "progress-bar": "bg:#007acc",
            "progress-bar.background": "bg:#555555",
            "progress-bar.text": "#ffffff",
            
            # Validation
            "validation.error": "#f48771",
            "validation.success": "#73c991",
            "validation.warning": "#cca700",
            
            # Syntax highlighting
            "pygments.keyword": "#c586c0",
            "pygments.name": "#dcdcaa",
            "pygments.string": "#ce9178",
            "pygments.number": "#b5cea8",
            "pygments.comment": "#6a9955",
            "pygments.operator": "#d4d4d4",
            
            # Completions
            "completion-menu": "bg:#252526 #d4d4d4",
            "completion-menu.border": "#555555",
            "completion-menu.completion": "#d4d4d4",
            "completion-menu.completion.current": "bg:#007acc #ffffff",
            "completion-menu.completion.focused": "bg:#2a2d2e",
            
            # Toolbars
            "toolbar": "bg:#252526 #d4d4d4",
            "toolbar.text": "#d4d4d4",
            "toolbar.key": "bold #569cd6",
            
            # Scrollbars
            "scrollbar.background": "bg:#2d2d2d",
            "scrollbar.button": "bg:#555555",
            "scrollbar.arrow": "#d4d4d4",
            "scrollbar.slider": "bg:#555555",
        }
    
    def _get_light_theme(self) -> Dict[str, str]:
        """Get light theme configuration."""
        return {
            # Basic colors
            "": "#000000",
            "bg": "#ffffff",
            "fg": "#000000",
            
            # UI elements
            "dialog": "bg:#f0f0f0",
            "dialog.border": "#cccccc",
            "dialog.title": "bold #000000",
            "dialog.body": "#333333",
            "dialog.shadow": "bg:#888888",
            
            # Text areas
            "textarea": "#000000",
            "textarea.border": "#cccccc",
            "textarea.focused": "#000000",
            "textarea.focused.border": "#007acc",
            
            # Buttons
            "button": "bg:#e1e1e1 #000000",
            "button.focused": "bg:#007acc #ffffff",
            "button.hover": "bg:#d0d0d0 #000000",
            "button.disabled": "bg:#f0f0f0 #888888",
            
            # Lists
            "list": "#000000",
            "list.border": "#cccccc",
            "list.selected": "bg:#007acc #ffffff",
            "list.hover": "bg:#f0f0f0",
            "list.focused": "bg:#e1e1e1",
            
            # Progress bars
            "progress-bar": "bg:#007acc",
            "progress-bar.background": "bg:#cccccc",
            "progress-bar.text": "#000000",
            
            # Validation
            "validation.error": "#d93025",
            "validation.success": "#0d904f",
            "validation.warning": "#f9ab00",
            
            # Syntax highlighting
            "pygments.keyword": "#d73a49",
            "pygments.name": "#24292e",
            "pygments.string": "#032f62",
            "pygments.number": "#005cc5",
            "pygments.comment": "#6a737d",
            "pygments.operator": "#d73a49",
            
            # Completions
            "completion-menu": "bg:#ffffff #000000",
            "completion-menu.border": "#cccccc",
            "completion-menu.completion": "#000000",
            "completion-menu.completion.current": "bg:#007acc #ffffff",
            "completion-menu.completion.focused": "bg:#f0f0f0",
            
            # Toolbars
            "toolbar": "bg:#f0f0f0 #000000",
            "toolbar.text": "#333333",
            "toolbar.key": "bold #007acc",
            
            # Scrollbars
            "scrollbar.background": "bg:#f0f0f0",
            "scrollbar.button": "bg:#cccccc",
            "scrollbar.arrow": "#000000",
            "scrollbar.slider": "bg:#cccccc",
        }
    
    def _get_high_contrast_theme(self) -> Dict[str, str]:
        """Get high contrast theme configuration."""
        return {
            # Basic colors
            "": "#ffffff",
            "bg": "#000000",
            "fg": "#ffffff",
            
            # UI elements
            "dialog": "bg:#000000",
            "dialog.border": "#ffffff",
            "dialog.title": "bold #ffff00",
            "dialog.body": "#ffffff",
            "dialog.shadow": "bg:#888888",
            
            # Text areas
            "textarea": "#ffffff",
            "textarea.border": "#ffffff",
            "textarea.focused": "#ffffff",
            "textarea.focused.border": "#ffff00",
            
            # Buttons
            "button": "bg:#000000 #ffffff",
            "button.focused": "bg:#ffff00 #000000",
            "button.hover": "bg:#333333 #ffffff",
            "button.disabled": "bg:#000000 #888888",
            
            # Lists
            "list": "#ffffff",
            "list.border": "#ffffff",
            "list.selected": "bg:#ffff00 #000000",
            "list.hover": "bg:#333333",
            "list.focused": "bg:#222222",
            
            # Progress bars
            "progress-bar": "bg:#ffff00",
            "progress-bar.background": "bg:#ffffff",
            "progress-bar.text": "#000000",
            
            # Validation
            "validation.error": "#ff0000",
            "validation.success": "#00ff00",
            "validation.warning": "#ffff00",
            
            # Syntax highlighting
            "pygments.keyword": "#ffff00",
            "pygments.name": "#ffffff",
            "pygments.string": "#ff00ff",
            "pygments.number": "#00ffff",
            "pygments.comment": "#888888",
            "pygments.operator": "#ffff00",
            
            # Completions
            "completion-menu": "bg:#000000 #ffffff",
            "completion-menu.border": "#ffffff",
            "completion-menu.completion": "#ffffff",
            "completion-menu.completion.current": "bg:#ffff00 #000000",
            "completion-menu.completion.focused": "bg:#333333",
            
            # Toolbars
            "toolbar": "bg:#000000 #ffffff",
            "toolbar.text": "#ffffff",
            "toolbar.key": "bold #ffff00",
            
            # Scrollbars
            "scrollbar.background": "bg:#000000",
            "scrollbar.button": "bg:#ffffff",
            "scrollbar.arrow": "#000000",
            "scrollbar.slider": "bg:#ffffff",
        }


class MicroAgentsCompleter(Completer):
    """Advanced completer for MicroAgents commands with fuzzy matching."""
    
    def __init__(
        self,
        commands: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        enable_fuzzy: bool = True,
    ):
        self.commands = commands
        self.context = context or {}
        self.enable_fuzzy = enable_fuzzy
        
        # Build nested completer structure
        self.nested_completer = self._build_nested_completer()
        
        # Create fuzzy completer wrapper
        if enable_fuzzy:
            self.completer = FuzzyCompleter(self.nested_completer)
        else:
            self.completer = self.nested_completer
    
    def _build_nested_completer(self) -> NestedCompleter:
        """Build nested completer from commands structure."""
        completer_dict = {}
        
        def build_completions(node: Any, prefix: str = "") -> Dict[str, Any]:
            completions = {}
            
            if isinstance(node, dict):
                for key, value in node.items():
                    full_key = f"{prefix}{key}" if prefix else key
                    
                    # Add completion for this key
                    description = None
                    if isinstance(value, dict) and "_description" in value:
                        description = value["_description"]
                        del value["_description"]
                    
                    completions[key] = {
                        "completion": Completion(
                            key,
                            start_position=-len(prefix) if prefix else 0,
                            display=key,
                            display_meta=description,
                        ),
                        "children": build_completions(value, f"{full_key} "),
                    }
            
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, str):
                        completions[item] = Completion(
                            item,
                            start_position=-len(prefix) if prefix else 0,
                        )
            
            return completions
        
        # Build initial structure
        nested_structure = {}
        for key, value in self.commands.items():
            if isinstance(value, dict):
                nested_structure[key] = build_completions(value)
            else:
                nested_structure[key] = None
        
        return NestedCompleter.from_nested_dict(nested_structure)
    
    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Generator[Completion, None, None]:
        """Get completions for current document."""
        yield from self.completer.get_completions(document, complete_event)
        
        # Add context-aware completions
        if self.context:
            current_text = document.text_before_cursor
            
            # Suggest context variables
            for key, value in self.context.items():
                if f"${key}" in current_text or key in current_text:
                    continue
                
                if key.lower().startswith(current_text.lower()):
                    yield Completion(
                        key,
                        start_position=-len(current_text),
                        display=f"${key}",
                        display_meta=f"Context variable: {value}",
                    )


class SyntaxHighlightingLexer(Lexer):
    """Lexer for syntax highlighting in prompt toolkit."""
    
    def __init__(
        self,
        language: str = "python",
        custom_styles: Optional[Dict[str, str]] = None,
    ):
        self.language = language
        self.custom_styles = custom_styles or {}
        
        # Map languages to pygments lexers
        self.lexer_map = {
            "python": PythonLexer,
            "json": JsonLexer,
            "yaml": YamlLexer,
            "yml": YamlLexer,
            "bash": lambda: get_lexer_by_name("bash"),
            "shell": lambda: get_lexer_by_name("bash"),
            "sql": lambda: get_lexer_by_name("sql"),
            "toml": lambda: get_lexer_by_name("toml"),
            "markdown": lambda: get_lexer_by_name("markdown"),
            "md": lambda: get_lexer_by_name("markdown"),
        }
    
    def lex_document(self, document: Document) -> Generator[StyleAndTextTuples, None, None]:
        """Lex document for syntax highlighting."""
        try:
            # Get appropriate lexer
            if self.language in self.lexer_map:
                lexer_class = self.lexer_map[self.language]
                lexer = lexer_class()
            else:
                # Try to guess lexer from content
                lexer = guess_lexer(document.text)
            
            # Get tokens from pygments
            text = document.text
            tokens = list(pygments.lex(text, lexer))
            
            # Convert pygments tokens to prompt_toolkit formatted text
            for token_type, value in tokens:
                # Map pygments token types to style names
                style = self._map_token_to_style(str(token_type))
                
                # Apply custom styles if defined
                if style in self.custom_styles:
                    style = self.custom_styles[style]
                
                yield [(style, value)]
                
        except Exception:
            # Fallback to simple lexer on error
            yield [("", document.text)]
    
    def _map_token_to_style(self, token_type: str) -> str:
        """Map pygments token type to style name."""
        # Extract base token type
        base_type = token_type.split(".")[-1]
        
        # Map to style names
        style_map = {
            "Keyword": "pygments.keyword",
            "Name": "pygments.name",
            "String": "pygments.string",
            "Number": "pygments.number",
            "Comment": "pygments.comment",
            "Operator": "pygments.operator",
            "Punctuation": "",
            "Text": "",
            "Error": "class:validation.error",
            "Whitespace": "",
        }
        
        return style_map.get(base_type, "")


class ValidatorChain(Validator):
    """Chain multiple validators together."""
    
    def __init__(self, validators: List[Validator]):
        self.validators = validators
    
    def validate(self, document: Document) -> None:
        """Validate document through all validators."""
        errors = []
        
        for validator in self.validators:
            try:
                validator.validate(document)
            except ValidationError as e:
                errors.append(str(e))
        
        if errors:
            raise ValidationError(
                message="\n".join(errors),
                cursor_position=document.cursor_position,
            )


class RangeValidator(Validator):
    """Validate numeric range."""
    
    def __init__(
        self,
        min_value: Optional[Union[int, float]] = None,
        max_value: Optional[Union[int, float]] = None,
        inclusive: bool = True,
        allow_none: bool = False,
    ):
        self.min_value = min_value
        self.max_value = max_value
        self.inclusive = inclusive
        self.allow_none = allow_none
    
    def validate(self, document: Document) -> None:
        """Validate numeric range."""
        text = document.text
        
        if self.allow_none and text.strip() == "":
            return
        
        try:
            # Try to parse as float first (more general)
            value = float(text)
            
            # Check if it's actually an integer
            if text.isdigit() or (text.startswith('-') and text[1:].isdigit()):
                value = int(text)
            
        except ValueError:
            raise ValidationError(
                message="Please enter a valid number",
                cursor_position=document.cursor_position,
            )
        
        errors = []
        
        if self.min_value is not None:
            if self.inclusive:
                if value < self.min_value:
                    errors.append(f"Value must be >= {self.min_value}")
            else:
                if value <= self.min_value:
                    errors.append(f"Value must be > {self.min_value}")
        
        if self.max_value is not None:
            if self.inclusive:
                if value > self.max_value:
                    errors.append(f"Value must be <= {self.max_value}")
            else:
                if value >= self.max_value:
                    errors.append(f"Value must be < {self.max_value}")
        
        if errors:
            raise ValidationError(
                message="; ".join(errors),
                cursor_position=document.cursor_position,
            )


class RegexValidator(Validator):
    """Validate input against regex pattern."""
    
    def __init__(
        self,
        pattern: str,
        message: Optional[str] = None,
        flags: int = 0,
    ):
        self.pattern = re.compile(pattern, flags)
        self.message = message or f"Input must match pattern: {pattern}"
    
    def validate(self, document: Document) -> None:
        """Validate against regex pattern."""
        text = document.text
        
        if not self.pattern.match(text):
            raise ValidationError(
                message=self.message,
                cursor_position=document.cursor_position,
            )


class EmailValidator(Validator):
    """Validate email address format."""
    
    def __init__(self, message: Optional[str] = None):
        self.pattern = re.compile(
            r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        )
        self.message = message or "Please enter a valid email address"
    
    def validate(self, document: Document) -> None:
        """Validate email format."""
        text = document.text.strip()
        
        if not self.pattern.match(text):
            raise ValidationError(
                message=self.message,
                cursor_position=document.cursor_position,
            )


class URLValidator(Validator):
    """Validate URL format."""
    
    def __init__(self, message: Optional[str] = None):
        self.pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
            r'localhost|'  # localhost...
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE
        )
        self.message = message or "Please enter a valid URL"
    
    def validate(self, document: Document) -> None:
        """Validate URL format."""
        text = document.text.strip()
        
        if not self.pattern.match(text):
            raise ValidationError(
                message=self.message,
                cursor_position=document.cursor_position,
            )


class InputValidator:
    """Factory for creating validators."""
    
    @staticmethod
    def create(
        validation_type: ValidationType,
        **kwargs,
    ) -> Validator:
        """Create validator by type."""
        if validation_type == ValidationType.REQUIRED:
            return RequiredValidator(**kwargs)
        elif validation_type == ValidationType.EMAIL:
            return EmailValidator(**kwargs)
        elif validation_type == ValidationType.URL:
            return URLValidator(**kwargs)
        elif validation_type == ValidationType.INTEGER:
            return RangeValidator(**kwargs)
        elif validation_type == ValidationType.FLOAT:
            return RangeValidator(**kwargs)
        elif validation_type == ValidationType.REGEX:
            return RegexValidator(**kwargs)
        elif validation_type == ValidationType.RANGE:
            return RangeValidator(**kwargs)
        elif validation_type == ValidationType.CUSTOM:
            return kwargs.get("validator")
        else:
            raise ValueError(f"Unknown validation type: {validation_type}")


class RequiredValidator(Validator):
    """Validate that input is not empty."""
    
    def __init__(self, message: Optional[str] = None):
        self.message = message or "This field is required"
    
    def validate(self, document: Document) -> None:
        """Validate that input is not empty."""
        if not document.text.strip():
            raise ValidationError(
                message=self.message,
                cursor_position=document.cursor_position,
            )


class InteractiveSession:
    """Manages interactive session state and history."""
    
    def __init__(
        self,
        session_id: Optional[str] = None,
        history_file: Optional[Path] = None,
        max_history_size: int = 1000,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self.created_at = datetime.now()
        self.last_activity = self.created_at
        
        # History management
        self.history_file = history_file or Path.home() / ".microagents" / "history"
        self.max_history_size = max_history_size
        
        # Create history directory if needed
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize histories
        self.command_history = FileHistory(str(self.history_file))
        self.input_history = InMemoryHistory()
        self.selection_history: Dict[str, List[str]] = defaultdict(list)
        
        # Undo/redo stacks
        self.undo_stack: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self.redo_stack: Dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        
        # Session variables
        self.variables: Dict[str, Any] = {}
        self.context: Dict[str, Any] = {}
        
        # Key bindings registry
        self.key_bindings = KeyBindings()
        self._register_default_key_bindings()
        
        # Screen state
        self.screen_stack: List[Container] = []
        self.current_screen: Optional[Container] = None
        
        # Theme
        self.theme = ThemeConfig(mode=ThemeMode.DARK)
        self.style = self.theme.to_prompt_toolkit_style()
    
    def _register_default_key_bindings(self) -> None:
        """Register default key bindings."""
        kb = self.key_bindings
        
        # Navigation
        @kb.add(Keys.Up)
        def _(event: KeyPressEvent) -> None:
            event.current_buffer.auto_up()
        
        @kb.add(Keys.Down)
        def _(event: KeyPressEvent) -> None:
            event.current_buffer.auto_down()
        
        @kb.add(Keys.ControlP)
        def _(event: KeyPressEvent) -> None:
            event.current_buffer.auto_up()
        
        @kb.add(Keys.ControlN)
        def _(event: KeyPressEvent) -> None:
            event.current_buffer.auto_down()
        
        # Undo/redo
        @kb.add(Keys.ControlZ)
        def _(event: KeyPressEvent) -> None:
            self._undo(event)
        
        @kb.add(Keys.ControlY)
        @kb.add(Keys.ControlShiftZ)
        def _(event: KeyPressEvent) -> None:
            self._redo(event)
        
        # Screen navigation
        @kb.add(Keys.Escape)
        def _(event: KeyPressEvent) -> None:
            if self.screen_stack:
                self.pop_screen()
            else:
                event.app.exit()
        
        @kb.add(Keys.F1)
        def _(event: KeyPressEvent) -> None:
            self.show_help()
        
        @kb.add(Keys.F2)
        def _(event: KeyPressEvent) -> None:
            self.toggle_theme()
        
        # Accessibility shortcuts
        @kb.add(Keys.ControlU)
        def _(event: KeyPressEvent) -> None:
            # Increase contrast
            self.increase_contrast()
        
        @kb.add(Keys.ControlI)
        def _(event: KeyPressEvent) -> None:
            # Decrease contrast
            self.decrease_contrast()
    
    def _undo(self, event: KeyPressEvent) -> None:
        """Undo last action."""
        buffer_name = event.current_buffer.name
        if buffer_name in self.undo_stack and self.undo_stack[buffer_name]:
            previous_state = self.undo_stack[buffer_name].pop()
            self.redo_stack[buffer_name].append(event.current_buffer.text)
            event.current_buffer.text = previous_state
    
    def _redo(self, event: KeyPressEvent) -> None:
        """Redo last undone action."""
        buffer_name = event.current_buffer.name
        if buffer_name in self.redo_stack and self.redo_stack[buffer_name]:
            next_state = self.redo_stack[buffer_name].pop()
            self.undo_stack[buffer_name].append(event.current_buffer.text)
            event.current_buffer.text = next_state
    
    def push_screen(self, screen: Container) -> None:
        """Push a new screen onto the stack."""
        if self.current_screen:
            self.screen_stack.append(self.current_screen)
        self.current_screen = screen
    
    def pop_screen(self) -> Optional[Container]:
        """Pop the current screen from the stack."""
        old_screen = self.current_screen
        if self.screen_stack:
            self.current_screen = self.screen_stack.pop()
        else:
            self.current_screen = None
        return old_screen
    
    def show_help(self) -> None:
        """Show help screen."""
        help_text = """
        MicroAgents Platform - Interactive Help
        
        Navigation:
        - ↑/↓ or Ctrl+P/Ctrl+N: Navigate history
        - Tab: Auto-completion
        - Ctrl+Space: Force completion
        - Ctrl+R: Reverse search
        - Ctrl+S: Forward search
        
        Editing:
        - Ctrl+Z: Undo
        - Ctrl+Y or Ctrl+Shift+Z: Redo
        - Ctrl+A: Beginning of line
        - Ctrl+E: End of line
        - Ctrl+K: Cut to end of line
        - Ctrl+U: Cut from beginning of line
        - Ctrl+Y: Paste
        
        Screen Management:
        - Esc: Go back/exit
        - F1: Show this help
        - F2: Toggle theme
        - F3: Toggle fullscreen
        
        Accessibility:
        - Ctrl+U: Increase contrast
        - Ctrl+I: Decrease contrast
        - Ctrl+'+': Increase font size
        - Ctrl+'-': Decrease font size
        
        Press any key to continue...
        """
        
        help_dialog = Dialog(
            title="Help",
            body=Label(help_text),
            buttons=[Button(text="OK", handler=lambda: None)],
        )
        
        self.push_screen(help_dialog)
    
    def toggle_theme(self) -> None:
        """Toggle between dark and light themes."""
        if self.theme.mode == ThemeMode.DARK:
            self.theme.mode = ThemeMode.LIGHT
        elif self.theme.mode == ThemeMode.LIGHT:
            self.theme.mode = ThemeMode.HIGH_CONTRAST
        else:
            self.theme.mode = ThemeMode.DARK
        
        self.style = self.theme.to_prompt_toolkit_style()
        get_app().style = self.style
    
    def increase_contrast(self) -> None:
        """Increase contrast for accessibility."""
        if self.theme.mode != ThemeMode.HIGH_CONTRAST:
            self.theme.mode = ThemeMode.HIGH_CONTRAST
            self.style = self.theme.to_prompt_toolkit_style()
            get_app().style = self.style
    
    def decrease_contrast(self) -> None:
        """Decrease contrast for accessibility."""
        if self.theme.mode == ThemeMode.HIGH_CONTRAST:
            self.theme.mode = ThemeMode.DARK
            self.style = self.theme.to_prompt_toolkit_style()
            get_app().style = self.style
    
    def save_state(self) -> Dict[str, Any]:
        """Save session state."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "variables": self.variables,
            "context": self.context,
            "theme": self.theme.mode.value,
        }
    
    def load_state(self, state: Dict[str, Any]) -> None:
        """Load session state."""
        self.session_id = state.get("session_id", self.session_id)
        self.created_at = datetime.fromisoformat(state.get("created_at", self.created_at.isoformat()))
        self.last_activity = datetime.fromisoformat(state.get("last_activity", self.last_activity.isoformat()))
        self.variables = state.get("variables", {})
        self.context = state.get("context", {})
        
        theme_mode = state.get("theme", ThemeMode.DARK.value)
        self.theme.mode = ThemeMode(theme_mode)
        self.style = self.theme.to_prompt_toolkit_style()


class InteractivePrompt:
    """Main class for interactive prompts."""
    
    def __init__(
        self,
        session: Optional[InteractiveSession] = None,
        formatter: Optional[Formatter] = None,
    ):
        self.session = session or InteractiveSession()
        self.formatter = formatter or get_formatter()
        self.completer = MicroAgentsCompleter(self._get_commands())
    
    def _get_commands(self) -> Dict[str, Any]:
        """Get available commands for auto-completion."""
        return {
            "agent": {
                "list": {"_description": "List all agents"},
                "start": {"_description": "Start an agent"},
                "stop": {"_description": "Stop an agent"},
                "status": {"_description": "Check agent status"},
                "deploy": {"_description": "Deploy agents"},
            },
            "monitoring": {
                "metrics": {"_description": "Show metrics"},
                "alerts": {"_description": "Manage alerts"},
                "logs": {"_description": "View logs"},
            },
            "cost": {
                "analyze": {"_description": "Analyze costs"},
                "optimize": {"_description": "Optimize costs"},
                "report": {"_description": "Generate cost report"},
            },
            "security": {
                "scan": {"_description": "Run security scan"},
                "audit": {"_description": "Security audit"},
                "compliance": {"_description": "Check compliance"},
            },
            "config": {
                "get": {"_description": "Get configuration"},
                "set": {"_description": "Set configuration"},
                "list": {"_description": "List all configurations"},
            },
            "help": {"_description": "Show help"},
            "exit": {"_description": "Exit the CLI"},
            "quit": {"_description": "Quit the CLI"},
        }
    
    async def prompt(
        self,
        message: str = "> ",
        default: Optional[str] = None,
        completer: Optional[Completer] = None,
        validator: Optional[Validator] = None,
        password: bool = False,
        multiline: bool = False,
        syntax: Optional[str] = None,
        history: Optional[History] = None,
    ) -> str:
        """Display a text input prompt."""
        # Update session activity
        self.session.last_activity = datetime.now()
        
        # Create buffer
        buffer = Buffer(
            name="input_prompt",
            completer=completer or self.completer,
            validator=validator,
            history=history or self.session.command_history,
            accept_handler=self._handle_input_accept,
            tempfile_suffix=".txt" if multiline else None,
        )
        
        # Set default value
        if default:
            buffer.text = default
        
        # Create lexer for syntax highlighting
        lexer = None
        if syntax and not password:
            lexer = SyntaxHighlightingLexer(syntax)
        
        # Create input processor
        processors = []
        if password:
            processors.append(PasswordProcessor())
        
        if syntax and not password:
            processors.append(ConditionalProcessor(
                lexer,
                filter=Condition(lambda: not password),
            ))
        
        # Create window
        control = BufferControl(
            buffer=buffer,
            lexer=lexer,
            input_processors=processors,
        )
        
        window = Window(
            control,
            height=Dimension(min=3) if multiline else Dimension(min=1),
            wrap_lines=multiline,
            dont_extend_height=not multiline,
        )
        
        # Create layout
        layout = Layout(window)
        
        # Create application
        app = Application(
            layout=layout,
            key_bindings=self.session.key_bindings,
            style=self.session.style,
            full_screen=False,
            mouse_support=True,
            color_depth=ColorDepth.TRUE_COLOR,
        )
        
        # Run application
        try:
            result = await app.run_async()
            return result
        except KeyboardInterrupt:
            raise
        except Exception as e:
            raise Exception(f"Prompt failed: {e}")
    
    def _handle_input_accept(self, buffer: Buffer) -> bool:
        """Handle input acceptance."""
        # Save to undo stack
        buffer_name = buffer.name
        if buffer_name:
            self.session.undo_stack[buffer_name].append(buffer.text)
            self.session.redo_stack[buffer_name].clear()
        
        return True
    
    async def confirm(
        self,
        message: str,
        default: bool = False,
        yes_text: str = "Yes",
        no_text: str = "No",
    ) -> bool:
        """Display a confirmation dialog."""
        result = await self._run_dialog_async(
            yes_no_dialog,
            title="Confirmation",
            text=message,
            yes_text=yes_text,
            no_text=no_text,
        )
        return result
    
    async def select(
        self,
        message: str,
        choices: List[Tuple[str, str]],
        default: Optional[str] = None,
        mode: SelectionMode = SelectionMode.SINGLE,
        searchable: bool = True,
        multiselect_text: str = "Select items (Space to toggle, Enter to confirm)",
    ) -> Union[str, List[str]]:
        """Display a selection prompt."""
        if mode == SelectionMode.SINGLE:
            result = await self._run_dialog_async(
                radiolist_dialog,
                title="Selection",
                text=message,
                values=choices,
                default=default,
            )
            return result or ""
        
        elif mode == SelectionMode.MULTIPLE:
            result = await self._run_dialog_async(
                checkboxlist_dialog,
                title="Multiple Selection",
                text=message,
                values=choices,
                default=[],
            )
            return result or []
        
        else:
            raise ValueError(f"Unknown selection mode: {mode}")
    
    async def file_picker(
        self,
        title: str = "Select File",
        initial_directory: Optional[Path] = None,
        file_filter: Optional[Callable[[Path], bool]] = None,
        allow_multiple: bool = False,
    ) -> Union[Path, List[Path], None]:
        """Display a file picker dialog."""
        # This is a simplified implementation
        # In production, you'd use a proper file browser widget
        
        # Get directory listing
        directory = initial_directory or Path.cwd()
        files = []
        
        try:
            for item in directory.iterdir():
                if file_filter is None or file_filter(item):
                    files.append(item)
        except Exception:
            files = []
        
        # Create choices
        choices = []
        if directory.parent != directory:
            choices.append(("..", "Go up"))
        
        for file in sorted(files):
            if file.is_dir():
                icon = "📁"
                description = "Directory"
            else:
                icon = "📄"
                description = f"File ({file.stat().st_size:,} bytes)"
            
            choices.append((str(file), f"{icon} {file.name} - {description}"))
        
        # Show selection dialog
        selected = await self.select(
            f"{title}\nDirectory: {directory}",
            choices,
            mode=SelectionMode.SINGLE if not allow_multiple else SelectionMode.MULTIPLE,
            searchable=True,
        )
        
        if not selected:
            return None
        
        if isinstance(selected, str):
            path = Path(selected)
            if path.name == "..":
                return await self.file_picker(
                    title,
                    directory.parent,
                    file_filter,
                    allow_multiple,
                )
            elif path.is_dir():
                return await self.file_picker(
                    title,
                    path,
                    file_filter,
                    allow_multiple,
                )
            else:
                return path
        
        else:
            return [Path(p) for p in selected]
    
    async def table_select(
        self,
        title: str,
        data: List[Dict[str, Any]],
        columns: List[str],
        key_column: str = "id",
        searchable: bool = True,
        page_size: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """Display a table selection prompt."""
        # Create formatted table for display
        formatter = self.formatter
        
        # Create choices from data
        choices = []
        for i, row in enumerate(data):
            # Create display text
            display_parts = []
            for col in columns:
                if col in row:
                    value = str(row[col])
                    if len(value) > 30:
                        value = value[:27] + "..."
                    display_parts.append(f"{col}: {value}")
            
            display = " | ".join(display_parts)
            value = str(row.get(key_column, i))
            choices.append((value, display))
        
        # Add pagination if needed
        if len(choices) > page_size:
            # Group into pages
            pages = []
            for i in range(0, len(choices), page_size):
                page_choices = choices[i:i + page_size]
                pages.append((str(i // page_size), f"Page {(i // page_size) + 1}"))
            
            # First ask for page
            page_choice = await self.select(
                f"{title}\nSelect page:",
                pages,
                mode=SelectionMode.SINGLE,
                searchable=False,
            )
            
            if not page_choice:
                return None
            
            page_idx = int(page_choice)
            choices = choices[page_idx * page_size:(page_idx + 1) * page_size]
        
        # Select item
        selected_key = await self.select(
            title,
            choices,
            mode=SelectionMode.SINGLE,
            searchable=searchable,
        )
        
        if not selected_key:
            return None
        
        # Find and return selected item
        for row in data:
            if str(row.get(key_column, "")) == selected_key:
                return row
        
        return None
    
    async def wizard(
        self,
        steps: List[Dict[str, Any]],
        title: str = "Setup Wizard",
        on_step_change: Optional[Callable[[int, Dict[str, Any]], None]] = None,
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Display a wizard with multiple steps."""
        results = {}
        current_step = 0
        
        while current_step < len(steps):
            step = steps[current_step]
            step_title = step.get("title", f"Step {current_step + 1}")
            step_description = step.get("description", "")
            
            # Show step header
            header = f"[bold]{title}[/bold]\n\n"
            header += f"Step {current_step + 1} of {len(steps)}: {step_title}\n"
            if step_description:
                header += f"\n{step_description}\n"
            
            self.formatter.print(header)
            
            # Call step change callback
            if on_step_change:
                on_step_change(current_step, step)
            
            # Process step based on type
            step_type = step.get("type", "input")
            step_id = step.get("id", f"step_{current_step}")
            
            if step_type == "input":
                result = await self.prompt(
                    message=step.get("message", "Enter value: "),
                    default=step.get("default"),
                    validator=step.get("validator"),
                    password=step.get("password", False),
                    multiline=step.get("multiline", False),
                    syntax=step.get("syntax"),
                )
                results[step_id] = result
            
            elif step_type == "select":
                choices = step.get("choices", [])
                if callable(choices):
                    choices = choices(results)
                
                result = await self.select(
                    message=step.get("message", "Select an option:"),
                    choices=choices,
                    default=step.get("default"),
                    mode=step.get("mode", SelectionMode.SINGLE),
                    searchable=step.get("searchable", True),
                )
                results[step_id] = result
            
            elif step_type == "confirm":
                result = await self.confirm(
                    message=step.get("message", "Please confirm:"),
                    default=step.get("default", False),
                    yes_text=step.get("yes_text", "Yes"),
                    no_text=step.get("no_text", "No"),
                )
                results[step_id] = result
            
            elif step_type == "file_picker":
                result = await self.file_picker(
                    title=step.get("title", "Select File"),
                    initial_directory=step.get("initial_directory"),
                    file_filter=step.get("file_filter"),
                    allow_multiple=step.get("allow_multiple", False),
                )
                results[step_id] = result
            
            elif step_type == "table_select":
                result = await self.table_select(
                    title=step.get("title", "Select Item"),
                    data=step.get("data", []),
                    columns=step.get("columns", []),
                    key_column=step.get("key_column", "id"),
                    searchable=step.get("searchable", True),
                    page_size=step.get("page_size", 20),
                )
                results[step_id] = result
            
            else:
                raise ValueError(f"Unknown step type: {step_type}")
            
            # Check if step has conditional next
            next_step = step.get("next")
            if callable(next_step):
                current_step = next_step(results)
            elif isinstance(next_step, int):
                current_step = next_step
            else:
                current_step += 1
        
        # Wizard complete
        if on_complete:
            on_complete(results)
        
        return results
    
    async def menu(
        self,
        title: str,
        items: List[Tuple[str, str, Optional[Callable]]],
        show_exit: bool = True,
    ) -> Optional[Any]:
        """Display an interactive menu."""
        while True:
            # Clear screen
            self.formatter.console.clear()
            
            # Show title
            self.formatter.print(f"[bold]{title}[/bold]\n")
            
            # Show menu items
            choices = []
            for i, (key, description, _) in enumerate(items):
                choices.append((str(i), f"{key}: {description}"))
            
            if show_exit:
                choices.append(("x", "Exit"))
            
            # Get selection
            selection = await self.select(
                "Select an option:",
                choices,
                mode=SelectionMode.SINGLE,
                searchable=False,
            )
            
            if not selection or selection == "x":
                break
            
            # Execute selected action
            idx = int(selection)
            if 0 <= idx < len(items):
                _, _, action = items[idx]
                if action:
                    result = action()
                    if asyncio.iscoroutinefunction(action):
                        result = await action()
                    return result
        
        return None
    
    def _run_dialog_async(self, dialog_func: Callable, **kwargs) -> Any:
        """Run a dialog function asynchronously."""
        # Note: prompt_toolkit dialogs are synchronous
        # In production, you might want to use asyncio.to_thread
        # For now, we'll run synchronously
        try:
            return dialog_func(**kwargs)
        except Exception as e:
            raise Exception(f"Dialog failed: {e}")


class AccessibilityProcessor(Processor):
    """Processor for accessibility features."""
    
    def __init__(self):
        self.highlight_current_line = True
        self.show_line_numbers = True
        self.highlight_matching_brackets = True
        self.font_size = 12
    
    def apply_transformation(self, transformation_input: TransformationInput) -> Transformation:
        """Apply accessibility transformations."""
        lineno = transformation_input.lineno
        fragments = transformation_input.fragments
        
        # Add line numbers
        if self.show_line_numbers:
            line_number = f"{lineno:4d} "
            fragments.insert(0, ("class:line-number", line_number))
        
        # Highlight current line
        if self.highlight_current_line and lineno == transformation_input.cursor_position.y:
            fragments = [("class:current-line", fragment[1]) for fragment in fragments]
        
        return Transformation(fragments)


# Global interactive prompt instance
_interactive_prompt: Optional[InteractivePrompt] = None


def get_interactive_prompt(
    session: Optional[InteractiveSession] = None,
    formatter: Optional[Formatter] = None,
) -> InteractivePrompt:
    """Get or create the global interactive prompt."""
    global _interactive_prompt
    
    if _interactive_prompt is None:
        _interactive_prompt = InteractivePrompt(session, formatter)
    
    return _interactive_prompt


# Convenience functions
async def prompt(
    message: str = "> ",
    **kwargs,
) -> str:
    """Convenience function for text input prompt."""
    ip = get_interactive_prompt()
    return await ip.prompt(message, **kwargs)


async def confirm(
    message: str,
    **kwargs,
) -> bool:
    """Convenience function for confirmation prompt."""
    ip = get_interactive_prompt()
    return await ip.confirm(message, **kwargs)


async def select(
    message: str,
    choices: List[Tuple[str, str]],
    **kwargs,
) -> Union[str, List[str]]:
    """Convenience function for selection prompt."""
    ip = get_interactive_prompt()
    return await ip.select(message, choices, **kwargs)


async def file_picker(
    **kwargs,
) -> Union[Path, List[Path], None]:
    """Convenience function for file picker."""
    ip = get_interactive_prompt()
    return await ip.file_picker(**kwargs)


async def table_select(
    **kwargs,
) -> Optional[Dict[str, Any]]:
    """Convenience function for table selection."""
    ip = get_interactive_prompt()
    return await ip.table_select(**kwargs)


async def wizard(
    steps: List[Dict[str, Any]],
    **kwargs,
) -> Dict[str, Any]:
    """Convenience function for wizard."""
    ip = get_interactive_prompt()
    return await ip.wizard(steps, **kwargs)


async def menu(
    title: str,
    items: List[Tuple[str, str, Optional[Callable]]],
    **kwargs,
) -> Optional[Any]:
    """Convenience function for interactive menu."""
    ip = get_interactive_prompt()
    return await ip.menu(title, items, **kwargs)


# Initialize interactive prompt on module import
get_interactive_prompt()

# Export public API
__all__ = [
    "get_interactive_prompt",
    "prompt",
    "confirm",
    "select",
    "file_picker",
    "table_select",
    "wizard",
    "menu",
    "ThemeMode",
    "ValidationType",
    "SelectionMode",
    "ThemeConfig",
    "MicroAgentsCompleter",
    "SyntaxHighlightingLexer",
    "ValidatorChain",
    "RangeValidator",
    "RegexValidator",
    "EmailValidator",
    "URLValidator",
    "RequiredValidator",
    "InputValidator",
    "InteractiveSession",
    "InteractivePrompt",
    "AccessibilityProcessor",
]