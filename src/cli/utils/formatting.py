"""
Rich formatting utilities for MicroAgents Platform CLI.
Professional tables, trees, progress bars, and responsive layouts.
"""

import json
import os
import re
import sys
import textwrap
import time
from collections import defaultdict
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

import pandas as pd
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.highlighter import Highlighter, NullHighlighter, RegexHighlighter
from rich.json import JSON as RichJSON
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.measure import Measurement
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.spinner import Spinner
from rich.status import Status
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Column, Table
from rich.text import Text
from rich.tree import Tree
from typing_extensions import Self

# Type variables
T = TypeVar("T")
K = TypeVar("K")
V = TypeVar("V")

# Global console instance
_console = Console(record=True)


class ColorTheme(str, Enum):
    """Color themes for the CLI."""
    
    DEFAULT = "default"
    DARK = "dark"
    LIGHT = "light"
    HIGH_CONTRAST = "high_contrast"
    BLUE = "blue"
    GREEN = "green"
    PURPLE = "purple"
    CUSTOM = "custom"


class ExportFormat(str, Enum):
    """Export formats for formatted output."""
    
    TERMINAL = "terminal"
    HTML = "html"
    PDF = "pdf"
    MARKDOWN = "markdown"
    JSON = "json"
    CSV = "csv"
    EXCEL = "excel"


class SortOrder(str, Enum):
    """Sort orders for tables."""
    
    ASCENDING = "asc"
    DESCENDING = "desc"
    NONE = "none"


@dataclass
class TableConfig:
    """Configuration for table formatting."""
    
    show_header: bool = True
    show_footer: bool = False
    show_lines: bool = False
    expand: bool = False
    safe_box: bool = True
    header_style: str = "bold cyan"
    border_style: str = "dim blue"
    row_styles: List[str] = field(default_factory=lambda: ["", "dim"])
    footer_style: str = "bold italic"
    caption: Optional[str] = None
    caption_style: str = "bold yellow"
    highlight: bool = False
    highlight_style: str = "bold yellow on dark_blue"
    zebra_stripes: bool = True
    padding: Tuple[int, int] = (0, 1)
    min_width: Optional[int] = None
    max_width: Optional[int] = None


@dataclass
class TreeConfig:
    """Configuration for tree formatting."""
    
    guide_style: str = "dim"
    expanded: bool = True
    highlight: bool = True
    highlight_style: str = "bold yellow"
    hide_root: bool = False
    indent_size: int = 2
    show_icons: bool = True
    icon_expanded: str = "▼ "
    icon_collapsed: str = "▶ "
    icon_leaf: str = "  "


@dataclass
class ProgressConfig:
    """Configuration for progress bars."""
    
    show_percentage: bool = True
    show_time: bool = True
    show_speed: bool = False
    show_eta: bool = True
    show_elapsed: bool = True
    show_remaining: bool = True
    show_transfer: bool = False
    show_spinner: bool = True
    refresh_per_second: float = 10.0
    expand: bool = False
    transient: bool = False
    auto_refresh: bool = True
    style: str = "bar.back"
    complete_style: str = "bar.complete"
    finished_style: str = "bar.finished"
    pulse_style: str = "bar.pulse"


@dataclass
class ThemeConfig:
    """Configuration for color themes."""
    
    name: ColorTheme
    primary: str = "#3498db"
    secondary: str = "#2ecc71"
    accent: str = "#e74c3c"
    success: str = "#27ae60"
    warning: str = "#f39c12"
    error: str = "#e74c3c"
    info: str = "#3498db"
    background: str = "#1a1a1a"
    foreground: str = "#ecf0f1"
    muted: str = "#7f8c8d"
    border: str = "#34495e"
    highlight: str = "#f1c40f"
    styles: Dict[str, Style] = field(default_factory=dict)
    
    def to_rich_styles(self) -> Dict[str, Style]:
        """Convert theme to Rich styles."""
        return {
            "primary": Style(color=self.primary),
            "secondary": Style(color=self.secondary),
            "accent": Style(color=self.accent),
            "success": Style(color=self.success, bold=True),
            "warning": Style(color=self.warning, bold=True),
            "error": Style(color=self.error, bold=True),
            "info": Style(color=self.info),
            "background": Style(bgcolor=self.background),
            "foreground": Style(color=self.foreground),
            "muted": Style(color=self.muted),
            "border": Style(color=self.border),
            "highlight": Style(color=self.highlight, bold=True),
            **self.styles,
        }


class SearchHighlighter(Highlighter):
    """Highlighter for search terms in text."""
    
    def __init__(self, search_terms: List[str], style: str = "bold yellow on dark_blue"):
        self.search_terms = search_terms
        self.style = style
        self.patterns = [re.compile(re.escape(term), re.IGNORECASE) for term in search_terms]
        super().__init__()
    
    def highlight(self, text: Text) -> None:
        """Highlight search terms in text."""
        for pattern in self.patterns:
            for match in pattern.finditer(text.plain):
                text.stylize(self.style, match.start(), match.end())


class AccessibilityHighlighter(Highlighter):
    """Highlighter for accessibility support."""
    
    def __init__(self):
        # Patterns for different semantic elements
        self.patterns = [
            (re.compile(r"\b(ERROR|FAILED|CRITICAL)\b"), "bold red"),
            (re.compile(r"\b(WARNING|CAUTION)\b"), "bold yellow"),
            (re.compile(r"\b(SUCCESS|PASSED|OK)\b"), "bold green"),
            (re.compile(r"\b(INFO|DEBUG)\b"), "bold blue"),
            (re.compile(r"\b(NOTE|TIP|IMPORTANT)\b"), "bold magenta"),
            (re.compile(r"\b(\d+\.\d+\.\d+|\d+\.\d+)\b"), "bold cyan"),  # Version numbers
            (re.compile(r"\b(https?://\S+|www\.\S+)\b"), "underline blue"),  # URLs
            (re.compile(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b"), "underline magenta"),  # Emails
        ]
        super().__init__()
    
    def highlight(self, text: Text) -> None:
        """Highlight semantic elements for accessibility."""
        for pattern, style in self.patterns:
            for match in pattern.finditer(text.plain):
                text.stylize(style, match.start(), match.end())


class ResponsiveLayout(Layout):
    """Responsive layout that adapts to terminal size."""
    
    def __init__(
        self,
        name: Optional[str] = None,
        size: Optional[int] = None,
        minimum_size: int = 1,
        ratio: int = 1,
        visible: bool = True,
    ):
        super().__init__(name=name, size=size, minimum_size=minimum_size, ratio=ratio, visible=visible)
        self._responsive_breakpoints = {
            "xs": 40,   # Extra small
            "sm": 60,   # Small
            "md": 80,   # Medium
            "lg": 120,  # Large
            "xl": 160,  # Extra large
        }
    
    def get_breakpoint(self) -> str:
        """Get current responsive breakpoint."""
        width = self.console.width if self.console else 80
        
        for breakpoint_name, breakpoint_width in sorted(
            self._responsive_breakpoints.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            if width >= breakpoint_width:
                return breakpoint_name
        
        return "xs"
    
    def adapt_layout(self) -> None:
        """Adapt layout based on terminal size."""
        breakpoint = self.get_breakpoint()
        
        if breakpoint in ["xs", "sm"]:
            # Stack layouts vertically for small screens
            self.split_column(*self.children)
        else:
            # Use horizontal split for larger screens
            self.split_row(*self.children)


class Paginator:
    """Paginator for large datasets."""
    
    def __init__(
        self,
        items: List[Any],
        page_size: int = 20,
        current_page: int = 1,
    ):
        self.items = items
        self.page_size = page_size
        self.current_page = current_page
        self.total_pages = max(1, (len(items) + page_size - 1) // page_size)
    
    @property
    def current_items(self) -> List[Any]:
        """Get items for current page."""
        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size
        return self.items[start:end]
    
    @property
    def has_previous(self) -> bool:
        """Check if there's a previous page."""
        return self.current_page > 1
    
    @property
    def has_next(self) -> bool:
        """Check if there's a next page."""
        return self.current_page < self.total_pages
    
    def next_page(self) -> bool:
        """Move to next page."""
        if self.has_next:
            self.current_page += 1
            return True
        return False
    
    def previous_page(self) -> bool:
        """Move to previous page."""
        if self.has_previous:
            self.current_page -= 1
            return True
        return False
    
    def go_to_page(self, page: int) -> bool:
        """Go to specific page."""
        if 1 <= page <= self.total_pages:
            self.current_page = page
            return True
        return False


class Formatter:
    """Main formatter class with utility methods."""
    
    def __init__(self, theme: Optional[ThemeConfig] = None):
        self.console = _console
        self.theme = theme or self._load_theme()
        self.styles = self.theme.to_rich_styles()
        self._progress_bars: Dict[str, Progress] = {}
        self._live_displays: Dict[str, Live] = {}
        self._status_panels: Dict[str, Status] = {}
        
        # Apply theme to console
        self._apply_theme()
    
    def _load_theme(self) -> ThemeConfig:
        """Load theme from environment or config file."""
        theme_name = os.getenv("MICROAGENTS_THEME", ColorTheme.DEFAULT)
        
        # Define built-in themes
        themes = {
            ColorTheme.DEFAULT: ThemeConfig(
                name=ColorTheme.DEFAULT,
                primary="#3498db",
                secondary="#2ecc71",
                accent="#e74c3c",
                success="#27ae60",
                warning="#f39c12",
                error="#e74c3c",
                info="#3498db",
                background="#1a1a1a",
                foreground="#ecf0f1",
                muted="#7f8c8d",
                border="#34495e",
                highlight="#f1c40f",
            ),
            ColorTheme.DARK: ThemeConfig(
                name=ColorTheme.DARK,
                primary="#9b59b6",
                secondary="#1abc9c",
                accent="#e74c3c",
                success="#2ecc71",
                warning="#f1c40f",
                error="#e74c3c",
                info="#3498db",
                background="#2c3e50",
                foreground="#ecf0f1",
                muted="#95a5a6",
                border="#7f8c8d",
                highlight="#f1c40f",
            ),
            ColorTheme.LIGHT: ThemeConfig(
                name=ColorTheme.LIGHT,
                primary="#2980b9",
                secondary="#27ae60",
                accent="#c0392b",
                success="#27ae60",
                warning="#f39c12",
                error="#c0392b",
                info="#2980b9",
                background="#ffffff",
                foreground="#2c3e50",
                muted="#7f8c8d",
                border="#bdc3c7",
                highlight="#f39c12",
            ),
            ColorTheme.HIGH_CONTRAST: ThemeConfig(
                name=ColorTheme.HIGH_CONTRAST,
                primary="#0000ff",
                secondary="#008000",
                accent="#ff0000",
                success="#008000",
                warning="#ffa500",
                error="#ff0000",
                info="#0000ff",
                background="#000000",
                foreground="#ffffff",
                muted="#808080",
                border="#ffffff",
                highlight="#ffff00",
            ),
        }
        
        return themes.get(theme_name, themes[ColorTheme.DEFAULT])
    
    def _apply_theme(self) -> None:
        """Apply theme to console."""
        # Console already initialized globally
        pass
    
    def create_table(
        self,
        title: Optional[str] = None,
        config: Optional[TableConfig] = None,
        columns: Optional[List[Union[str, Column]]] = None,
        data: Optional[List[Any]] = None,
        sort_by: Optional[Union[str, int]] = None,
        sort_order: SortOrder = SortOrder.ASCENDING,
        search_term: Optional[str] = None,
        paginate: bool = False,
        page_size: int = 20,
        export_format: ExportFormat = ExportFormat.TERMINAL,
    ) -> Union[Table, str]:
        """
        Create a professional table with advanced features.
        
        Args:
            title: Table title
            config: Table configuration
            columns: Column definitions
            data: Table data
            sort_by: Column to sort by (name or index)
            sort_order: Sort order
            search_term: Term to highlight
            paginate: Whether to paginate results
            page_size: Items per page
            export_format: Output format
            
        Returns:
            Rich Table object or exported string
        """
        config = config or TableConfig()
        
        # Create table
        table = Table(
            show_header=config.show_header,
            show_footer=config.show_footer,
            show_lines=config.show_lines,
            expand=config.expand,
            safe_box=config.safe_box,
            border_style=config.border_style,
            title=title,
            caption=config.caption,
            caption_style=config.caption_style,
            padding=config.padding,
            min_width=config.min_width,
            max_width=config.max_width,
        )
        
        # Add columns
        if columns:
            for col in columns:
                if isinstance(col, str):
                    table.add_column(
                        col,
                        header_style=config.header_style,
                        footer_style=config.footer_style,
                    )
                elif isinstance(col, Column):
                    table.add_column(
                        col.header,
                        style=col.style,
                        justify=col.justify,
                        overflow=col.overflow,
                        width=col.width,
                        min_width=col.min_width,
                        max_width=col.max_width,
                        ratio=col.ratio,
                        no_wrap=col.no_wrap,
                    )
        
        # Prepare data
        if data:
            # Sort data if requested
            if sort_by is not None:
                data = self._sort_data(data, sort_by, sort_order)
            
            # Apply search highlighting
            highlighter = None
            if search_term:
                highlighter = SearchHighlighter([search_term], config.highlight_style)
            
            # Paginate if requested
            if paginate:
                paginator = Paginator(data, page_size)
                data = paginator.current_items
            
            # Add rows
            for i, row in enumerate(data):
                row_style = config.row_styles[i % len(config.row_styles)] if config.zebra_stripes else ""
                
                # Convert row to rich text
                rich_row = []
                for cell in row:
                    if isinstance(cell, (str, int, float, bool)):
                        text = Text(str(cell), style=row_style)
                        if highlighter:
                            highlighter.highlight(text)
                        rich_row.append(text)
                    elif isinstance(cell, Text):
                        if row_style:
                            cell.stylize(row_style)
                        if highlighter:
                            highlighter.highlight(cell)
                        rich_row.append(cell)
                    else:
                        rich_row.append(Text(str(cell), style=row_style))
                
                table.add_row(*rich_row)
            
            # Add footer with pagination info
            if paginate:
                paginator = Paginator(data, page_size)
                footer_text = f"Page {paginator.current_page} of {paginator.total_pages}"
                if hasattr(table, "columns"):
                    table.columns[0].footer = Text(footer_text, style=config.footer_style)
        
        # Export if requested
        if export_format != ExportFormat.TERMINAL:
            return self._export_table(table, export_format)
        
        return table
    
    def _sort_data(
        self,
        data: List[Any],
        sort_by: Union[str, int],
        sort_order: SortOrder,
    ) -> List[Any]:
        """Sort table data."""
        if not data:
            return data
        
        # Determine sort key
        if isinstance(sort_by, int):
            sort_key = lambda x: x[sort_by] if sort_by < len(x) else ""
        else:
            # Assume data is list of dicts
            sort_key = lambda x: x.get(sort_by, "")
        
        # Sort data
        reverse = sort_order == SortOrder.DESCENDING
        return sorted(data, key=sort_key, reverse=reverse)
    
    def _export_table(self, table: Table, format: ExportFormat) -> str:
        """Export table to different formats."""
        if format == ExportFormat.HTML:
            return self._table_to_html(table)
        elif format == ExportFormat.MARKDOWN:
            return self._table_to_markdown(table)
        elif format == ExportFormat.JSON:
            return self._table_to_json(table)
        elif format == ExportFormat.CSV:
            return self._table_to_csv(table)
        else:
            raise ValueError(f"Unsupported export format: {format}")
    
    def _table_to_html(self, table: Table) -> str:
        """Convert table to HTML."""
        # This is a simplified implementation
        # In production, use a proper HTML renderer
        html_lines = ["<table>"]
        
        # Add header
        if table.show_header and hasattr(table, "columns"):
            html_lines.append("  <thead>")
            html_lines.append("    <tr>")
            for column in table.columns:
                html_lines.append(f"      <th>{column.header}</th>")
            html_lines.append("    </tr>")
            html_lines.append("  </thead>")
        
        # Add body
        html_lines.append("  <tbody>")
        for row in table.rows:
            html_lines.append("    <tr>")
            for cell in row:
                html_lines.append(f"      <td>{cell}</td>")
            html_lines.append("    </tr>")
        html_lines.append("  </tbody>")
        
        html_lines.append("</table>")
        return "\n".join(html_lines)
    
    def _table_to_markdown(self, table: Table) -> str:
        """Convert table to Markdown."""
        if not hasattr(table, "columns") or not hasattr(table, "rows"):
            return ""
        
        # Create header
        headers = [col.header for col in table.columns]
        md_lines = [" | ".join(headers)]
        md_lines.append(" | ".join(["---"] * len(headers)))
        
        # Create rows
        for row in table.rows:
            cells = [str(cell) for cell in row]
            md_lines.append(" | ".join(cells))
        
        return "\n".join(md_lines)
    
    def _table_to_json(self, table: Table) -> str:
        """Convert table to JSON."""
        if not hasattr(table, "columns") or not hasattr(table, "rows"):
            return "[]"
        
        result = []
        headers = [col.header for col in table.columns]
        
        for row in table.rows:
            row_dict = {}
            for i, cell in enumerate(row):
                if i < len(headers):
                    row_dict[headers[i]] = str(cell)
            result.append(row_dict)
        
        return json.dumps(result, indent=2)
    
    def _table_to_csv(self, table: Table) -> str:
        """Convert table to CSV."""
        if not hasattr(table, "columns") or not hasattr(table, "rows"):
            return ""
        
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write header
        headers = [col.header for col in table.columns]
        writer.writerow(headers)
        
        # Write rows
        for row in table.rows:
            cells = [str(cell) for cell in row]
            writer.writerow(cells)
        
        return output.getvalue()
    
    def create_tree(
        self,
        root_label: str,
        data: Dict[str, Any],
        config: Optional[TreeConfig] = None,
        highlight_keys: Optional[List[str]] = None,
    ) -> Tree:
        """
        Create a hierarchical tree view.
        
        Args:
            root_label: Label for root node
            data: Hierarchical data
            config: Tree configuration
            highlight_keys: Keys to highlight
            
        Returns:
            Rich Tree object
        """
        config = config or TreeConfig()
        
        # Create tree
        tree = Tree(
            root_label,
            guide_style=config.guide_style,
            expanded=config.expanded,
            highlight=config.highlight,
            hide_root=config.hide_root,
        )
        
        # Add data recursively
        self._add_tree_nodes(tree, data, config, highlight_keys or [], 0)
        
        return tree
    
    def _add_tree_nodes(
        self,
        parent: Union[Tree, Tree],
        data: Any,
        config: TreeConfig,
        highlight_keys: List[str],
        depth: int,
    ) -> None:
        """Recursively add nodes to tree."""
        if isinstance(data, dict):
            for key, value in data.items():
                # Determine icon
                icon = config.icon_leaf
                if isinstance(value, (dict, list)):
                    icon = config.icon_collapsed if not config.expanded else config.icon_expanded
                
                # Create node label
                label = f"{icon}{key}" if config.show_icons else key
                node_text = Text(label)
                
                # Highlight if key matches
                if any(highlight_key.lower() in key.lower() for highlight_key in highlight_keys):
                    node_text.stylize(config.highlight_style)
                
                # Add node
                if isinstance(value, (dict, list)):
                    node = parent.add(node_text)
                    self._add_tree_nodes(node, value, config, highlight_keys, depth + 1)
                else:
                    value_str = str(value)
                    node_text.append(f": {value_str}")
                    parent.add(node_text)
        
        elif isinstance(data, list):
            for i, item in enumerate(data):
                label = f"[{i}]"
                node_text = Text(label)
                
                node = parent.add(node_text)
                self._add_tree_nodes(node, item, config, highlight_keys, depth + 1)
        
        else:
            parent.add(Text(str(data)))
    
    def create_progress_bar(
        self,
        task_id: str,
        total: float = 100.0,
        description: str = "Processing...",
        config: Optional[ProgressConfig] = None,
        auto_start: bool = True,
    ) -> TaskID:
        """
        Create a progress bar with advanced features.
        
        Args:
            task_id: Unique identifier for the progress bar
            total: Total value for completion
            description: Task description
            config: Progress bar configuration
            auto_start: Whether to auto-start the progress bar
            
        Returns:
            TaskID for controlling the progress bar
        """
        config = config or ProgressConfig()
        
        # Create progress instance if not exists
        if task_id not in self._progress_bars:
            progress = Progress(
                SpinnerColumn() if config.show_spinner else None,
                TextColumn("[progress.description]{task.description}"),
                BarColumn(
                    style=config.style,
                    complete_style=config.complete_style,
                    finished_style=config.finished_style,
                    pulse_style=config.pulse_style,
                ),
                TaskProgressColumn() if config.show_percentage else None,
                TimeElapsedColumn() if config.show_elapsed else None,
                TimeRemainingColumn() if config.show_remaining else None,
                DownloadColumn() if config.show_transfer else None,
                TransferSpeedColumn() if config.show_speed else None,
                expand=config.expand,
                transient=config.transient,
                refresh_per_second=config.refresh_per_second,
                auto_refresh=config.auto_refresh,
            )
            self._progress_bars[task_id] = progress
            
            # Start the progress display
            if auto_start:
                live = Live(progress, console=self.console, refresh_per_second=config.refresh_per_second)
                self._live_displays[task_id] = live
                live.start()
        
        progress = self._progress_bars[task_id]
        
        # Add task
        task = progress.add_task(description, total=total)
        
        return task
    
    def update_progress(
        self,
        task_id: str,
        progress_task: TaskID,
        advance: float = 1.0,
        description: Optional[str] = None,
        visible: Optional[bool] = None,
    ) -> None:
        """Update progress bar."""
        if task_id in self._progress_bars:
            progress = self._progress_bars[task_id]
            progress.update(progress_task, advance=advance, description=description, visible=visible)
    
    def complete_progress(self, task_id: str, progress_task: TaskID) -> None:
        """Complete and remove progress bar."""
        if task_id in self._progress_bars:
            progress = self._progress_bars[task_id]
            progress.update(progress_task, visible=False)
            progress.stop_task(progress_task)
            
            # Clean up if no more tasks
            if not progress.tasks:
                if task_id in self._live_displays:
                    self._live_displays[task_id].stop()
                    del self._live_displays[task_id]
                del self._progress_bars[task_id]
    
    def create_status_panel(
        self,
        panel_id: str,
        title: str,
        content: Union[str, Text, Markdown],
        style: str = "dim",
        border_style: str = "blue",
        expand: bool = False,
        padding: Tuple[int, int, int, int] = (1, 2),
    ) -> Panel:
        """
        Create a status panel.
        
        Args:
            panel_id: Unique identifier for the panel
            title: Panel title
            content: Panel content
            style: Content style
            border_style: Border style
            expand: Whether to expand to full width
            padding: Padding (top, right, bottom, left)
            
        Returns:
            Rich Panel object
        """
        # Convert content to renderable
        if isinstance(content, str):
            renderable = Text(content, style=style)
        elif isinstance(content, Text):
            renderable = content
        elif isinstance(content, Markdown):
            renderable = content
        else:
            renderable = Text(str(content), style=style)
        
        panel = Panel(
            renderable,
            title=title,
            border_style=border_style,
            expand=expand,
            padding=padding,
        )
        
        return panel
    
    def create_dashboard(
        self,
        panels: Dict[str, Panel],
        layout: Optional[ResponsiveLayout] = None,
        title: Optional[str] = None,
        auto_refresh: bool = False,
        refresh_interval: float = 1.0,
    ) -> Union[Layout, Live]:
        """
        Create a dashboard with multiple panels.
        
        Args:
            panels: Dictionary of panel_id -> Panel
            layout: Optional custom layout
            title: Dashboard title
            auto_refresh: Whether to auto-refresh
            refresh_interval: Refresh interval in seconds
            
        Returns:
            Layout or Live object for dynamic dashboards
        """
        # Create layout
        if layout is None:
            layout = ResponsiveLayout()
            layout.split_column(
                *[panels[panel_id] for panel_id in panels]
            )
        else:
            # Add panels to existing layout
            for panel_id, panel in panels.items():
                layout[panel_id] = panel
        
        # Apply responsive adaptation
        layout.adapt_layout()
        
        if auto_refresh:
            # Create auto-refreshing live display
            live = Live(
                layout,
                console=self.console,
                refresh_per_second=1.0 / refresh_interval,
                screen=False,
            )
            return live
        else:
            return layout
    
    def render_markdown(
        self,
        markdown_text: str,
        style: str = "default",
        code_theme: str = "monokai",
        inline_code_lexer: str = "python",
    ) -> Markdown:
        """
        Render markdown with syntax highlighting.
        
        Args:
            markdown_text: Markdown text to render
            style: Overall style
            code_theme: Code block theme
            inline_code_lexer: Inline code lexer
            
        Returns:
            Rich Markdown object
        """
        return Markdown(
            markdown_text,
            style=style,
            code_theme=code_theme,
            inline_code_lexer=inline_code_lexer,
        )
    
    def render_json(
        self,
        json_data: Union[str, Dict, List],
        indent: int = 2,
        highlight: bool = True,
        sort_keys: bool = True,
        ensure_ascii: bool = False,
    ) -> Union[RichJSON, Syntax]:
        """
        Pretty print JSON with syntax highlighting.
        
        Args:
            json_data: JSON data to render
            indent: Indentation level
            highlight: Whether to highlight syntax
            sort_keys: Whether to sort keys
            ensure_ascii: Whether to escape non-ASCII characters
            
        Returns:
            Rich JSON or Syntax object
        """
        if isinstance(json_data, str):
            try:
                json_data = json.loads(json_data)
            except json.JSONDecodeError:
                # Return as code if not valid JSON
                return Syntax(json_data, "json", theme="monokai")
        
        json_str = json.dumps(json_data, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
        
        if highlight:
            return Syntax(json_str, "json", theme="monokai")
        else:
            return RichJSON(json_str)
    
    def create_chart(
        self,
        data: Dict[str, Union[int, float]],
        chart_type: str = "bar",
        width: int = 50,
        height: int = 10,
        title: Optional[str] = None,
        show_values: bool = True,
        color: str = "blue",
    ) -> Text:
        """
        Create ASCII/Unicode charts.
        
        Args:
            data: Data to chart {label: value}
            chart_type: Type of chart (bar, line, pie)
            width: Chart width in characters
            height: Chart height in characters
            title: Chart title
            show_values: Whether to show values
            color: Chart color
            
        Returns:
            Rich Text object with chart
        """
        if not data:
            return Text("No data to display", style="dim")
        
        chart_lines = []
        
        if title:
            chart_lines.append(f" {title} ".center(width, "─"))
        
        if chart_type == "bar":
            chart_lines.extend(self._create_bar_chart(data, width, height, show_values, color))
        elif chart_type == "line":
            chart_lines.extend(self._create_line_chart(data, width, height, show_values, color))
        elif chart_type == "pie":
            chart_lines.extend(self._create_pie_chart(data, width, show_values, color))
        else:
            raise ValueError(f"Unsupported chart type: {chart_type}")
        
        chart_text = Text("\n".join(chart_lines), style=color)
        
        # Add legend
        if chart_type != "pie":
            chart_text.append("\n\n")
            for i, (label, value) in enumerate(data.items()):
                if i > 5:  # Limit legend items
                    chart_text.append("...", style="dim")
                    break
                chart_text.append("█ ", style=color)
                chart_text.append(f"{label}: {value}\n")
        
        return chart_text
    
    def _create_bar_chart(
        self,
        data: Dict[str, Union[int, float]],
        width: int,
        height: int,
        show_values: bool,
        color: str,
    ) -> List[str]:
        """Create bar chart."""
        max_value = max(data.values()) if data else 1
        chart_width = width - 10  # Leave space for labels
        
        lines = []
        
        # Calculate bar heights
        bar_heights = {}
        for label, value in data.items():
            if max_value > 0:
                bar_height = int((value / max_value) * height)
            else:
                bar_height = 0
            bar_heights[label] = min(bar_height, height)
        
        # Draw chart from top to bottom
        for y in range(height, 0, -1):
            line_parts = []
            for label, bar_height in bar_heights.items():
                if bar_height >= y:
                    line_parts.append("█" * 2)
                else:
                    line_parts.append("  ")
            
            # Truncate if too many bars
            max_bars = chart_width // 2
            if len(line_parts) > max_bars:
                line_parts = line_parts[:max_bars]
                line_parts.append("...")
            
            lines.append("".join(line_parts))
        
        # Add labels
        labels_line = []
        for label in list(bar_heights.keys())[:max_bars]:
            labels_line.append(label[:2])
        lines.append("".join(labels_line))
        
        return lines
    
    def _create_line_chart(
        self,
        data: Dict[str, Union[int, float]],
        width: int,
        height: int,
        show_values: bool,
        color: str,
    ) -> List[str]:
        """Create line chart."""
        values = list(data.values())
        max_value = max(values) if values else 1
        min_value = min(values) if values else 0
        
        chart_height = height
        chart_width = width - 10
        
        # Normalize values to chart height
        normalized = []
        for value in values:
            if max_value > min_value:
                norm = ((value - min_value) / (max_value - min_value)) * (chart_height - 1)
            else:
                norm = chart_height / 2
            normalized.append(int(norm))
        
        # Create empty grid
        grid = [[" " for _ in range(chart_width)] for _ in range(chart_height)]
        
        # Plot points
        for i, y in enumerate(normalized):
            if i < chart_width:
                grid[chart_height - y - 1][i] = "○"
        
        # Connect points with lines
        for i in range(len(normalized) - 1):
            if i < chart_width - 1:
                x1, y1 = i, chart_height - normalized[i] - 1
                x2, y2 = i + 1, chart_height - normalized[i + 1] - 1
                
                # Draw line between points
                self._draw_line(grid, x1, y1, x2, y2)
        
        # Convert grid to lines
        lines = []
        for row in grid:
            lines.append("".join(row))
        
        return lines
    
    def _draw_line(self, grid: List[List[str]], x1: int, y1: int, x2: int, y2: int) -> None:
        """Draw line between two points in grid."""
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy
        
        while True:
            if 0 <= x1 < len(grid[0]) and 0 <= y1 < len(grid):
                grid[y1][x1] = "·"
            
            if x1 == x2 and y1 == y2:
                break
            
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x1 += sx
            if e2 < dx:
                err += dx
                y1 += sy
    
    def _create_pie_chart(
        self,
        data: Dict[str, Union[int, float]],
        width: int,
        show_values: bool,
        color: str,
    ) -> List[str]:
        """Create pie chart."""
        total = sum(data.values())
        if total == 0:
            return ["No data"]
        
        # Calculate angles
        angles = {}
        current_angle = 0
        for label, value in data.items():
            percentage = value / total
            angle = percentage * 360
            angles[label] = (current_angle, current_angle + angle)
            current_angle += angle
        
        # Simplified pie chart representation
        lines = []
        lines.append("   ╭─────╮")
        lines.append("  ╭╯     ╰╮")
        lines.append(" ╭╯       ╰╮")
        lines.append("╭╯         ╰╮")
        lines.append("╰╮         ╭╯")
        lines.append(" ╰╮       ╭╯")
        lines.append("  ╰╮     ╭╯")
        lines.append("   ╰─────╯")
        
        return lines
    
    def export_content(
        self,
        content: Any,
        format: ExportFormat,
        filename: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Export content to various formats.
        
        Args:
            content: Content to export
            format: Export format
            filename: Optional filename to save to
            **kwargs: Format-specific options
            
        Returns:
            Exported content as string
        """
        if format == ExportFormat.HTML:
            exported = self._export_html(content, **kwargs)
        elif format == ExportFormat.PDF:
            exported = self._export_pdf(content, **kwargs)
        elif format == ExportFormat.MARKDOWN:
            exported = self._export_markdown(content, **kwargs)
        elif format == ExportFormat.JSON:
            exported = self._export_json(content, **kwargs)
        elif format == ExportFormat.CSV:
            exported = self._export_csv(content, **kwargs)
        elif format == ExportFormat.EXCEL:
            exported = self._export_excel(content, **kwargs)
        else:
            raise ValueError(f"Unsupported export format: {format}")
        
        # Save to file if filename provided
        if filename:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(exported)
        
        return exported
    
    def _export_html(self, content: Any, **kwargs) -> str:
        """Export content to HTML."""
        title = kwargs.get("title", "MicroAgents Export")
        styles = kwargs.get("styles", "")
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>
        body {{
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            background: {self.theme.background};
            color: {self.theme.foreground};
            margin: 20px;
            line-height: 1.6;
        }}
        pre {{
            background: #2c3e50;
            padding: 15px;
            border-radius: 5px;
            overflow-x: auto;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 20px 0;
        }}
        th, td {{
            border: 1px solid {self.theme.border};
            padding: 8px;
            text-align: left;
        }}
        th {{
            background: {self.theme.primary};
            color: white;
        }}
        tr:nth-child(even) {{
            background: rgba(255, 255, 255, 0.05);
        }}
        {styles}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <div id="content">
        {self._content_to_html(content)}
    </div>
</body>
</html>"""
        
        return html
    
    def _content_to_html(self, content: Any) -> str:
        """Convert content to HTML string."""
        if isinstance(content, str):
            return f"<pre>{content}</pre>"
        elif isinstance(content, (list, dict)):
            return f"<pre>{json.dumps(content, indent=2)}</pre>"
        elif hasattr(content, "__html__"):
            return content.__html__()
        else:
            return str(content)
    
    def _export_pdf(self, content: Any, **kwargs) -> str:
        """Export content to PDF."""
        # Note: This is a placeholder. In production, use a PDF library like ReportLab or WeasyPrint
        return f"PDF export would be generated for: {type(content).__name__}"
    
    def _export_markdown(self, content: Any, **kwargs) -> str:
        """Export content to Markdown."""
        if isinstance(content, str):
            return content
        elif isinstance(content, (list, dict)):
            return f"```json\n{json.dumps(content, indent=2)}\n```"
        else:
            return str(content)
    
    def _export_json(self, content: Any, **kwargs) -> str:
        """Export content to JSON."""
        indent = kwargs.get("indent", 2)
        return json.dumps(content, indent=indent, ensure_ascii=False)
    
    def _export_csv(self, content: Any, **kwargs) -> str:
        """Export content to CSV."""
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        if isinstance(content, list):
            if content and isinstance(content[0], dict):
                # List of dicts
                headers = list(content[0].keys())
                writer.writerow(headers)
                for row in content:
                    writer.writerow([row.get(h, "") for h in headers])
            else:
                # List of lists
                for row in content:
                    writer.writerow(row)
        elif isinstance(content, dict):
            # Dict to CSV (key-value pairs)
            writer.writerow(["Key", "Value"])
            for key, value in content.items():
                writer.writerow([key, value])
        else:
            writer.writerow([str(content)])
        
        return output.getvalue()
    
    def _export_excel(self, content: Any, **kwargs) -> str:
        """Export content to Excel."""
        # Note: This would use pandas to create Excel file
        # Returning placeholder
        return f"Excel export would be generated for: {type(content).__name__}"
    
    @contextmanager
    def spinner(
        self,
        message: str = "Loading...",
        spinner_type: str = "dots",
        speed: float = 1.0,
    ) -> Generator[None, None, None]:
        """Context manager for showing a spinner."""
        with Status(message, spinner=spinner_type, speed=speed, console=self.console):
            yield
    
    def print(self, *args, **kwargs) -> None:
        """Print with theme-aware styling."""
        self.console.print(*args, **kwargs)
    
    def clear(self) -> None:
        """Clear the console."""
        self.console.clear()


# Global formatter instance
_formatter: Optional[Formatter] = None


def get_formatter(theme: Optional[ThemeConfig] = None) -> Formatter:
    """Get or create the global formatter."""
    global _formatter
    
    if _formatter is None:
        _formatter = Formatter(theme)
    
    return _formatter


# Convenience functions
def print_table(
    data: List[Any],
    columns: Optional[List[str]] = None,
    title: Optional[str] = None,
    **kwargs,
) -> None:
    """Convenience function to print a table."""
    formatter = get_formatter()
    table = formatter.create_table(data=data, columns=columns, title=title, **kwargs)
    formatter.print(table)


def print_tree(
    data: Dict[str, Any],
    root_label: str = "Root",
    **kwargs,
) -> None:
    """Convenience function to print a tree."""
    formatter = get_formatter()
    tree = formatter.create_tree(root_label, data, **kwargs)
    formatter.print(tree)


def print_json(
    data: Any,
    **kwargs,
) -> None:
    """Convenience function to print JSON."""
    formatter = get_formatter()
    json_renderable = formatter.render_json(data, **kwargs)
    formatter.print(json_renderable)


def print_markdown(
    text: str,
    **kwargs,
) -> None:
    """Convenience function to print markdown."""
    formatter = get_formatter()
    markdown = formatter.render_markdown(text, **kwargs)
    formatter.print(markdown)


def print_chart(
    data: Dict[str, Union[int, float]],
    **kwargs,
) -> None:
    """Convenience function to print a chart."""
    formatter = get_formatter()
    chart = formatter.create_chart(data, **kwargs)
    formatter.print(chart)


# Initialize formatter on module import
get_formatter()

# Export public API
__all__ = [
    "get_formatter",
    "print_table",
    "print_tree",
    "print_json",
    "print_markdown",
    "print_chart",
    "ColorTheme",
    "ExportFormat",
    "SortOrder",
    "TableConfig",
    "TreeConfig",
    "ProgressConfig",
    "ThemeConfig",
    "Formatter",
    "SearchHighlighter",
    "AccessibilityHighlighter",
    "ResponsiveLayout",
    "Paginator",
]