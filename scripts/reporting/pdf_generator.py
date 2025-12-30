#!/usr/bin/env python3
"""
PDF Generator for MicroAgents Platform
Professional reporting with advanced features for DevOps intelligence.
"""

import os
import io
import tempfile
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Union, BinaryIO
from pathlib import Path
from enum import Enum
from decimal import Decimal
import json

# PDF Generation Libraries
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, KeepTogether, ListFlowable, ListItem,
    PageTemplate, Frame, NextPageTemplate, FrameBreak
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics import renderPDF
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib.utils import ImageReader

# Security & Compliance
import cryptography
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key, load_pem_public_key,
    Encoding, PrivateFormat, NoEncryption, PublicFormat
)
from cryptography import x509
from cryptography.x509.oid import NameOID
import PyPDF2
from PyPDF2 import PdfReader, PdfWriter

# Performance & Compression
import zlib
import base64
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# Configuration
from ..config.settings import PDF_CONFIG

logger = logging.getLogger(__name__)

# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class ReportType(Enum):
    """Supported report types."""
    EXECUTIVE_SUMMARY = "executive_summary"
    TECHNICAL_DETAIL = "technical_detail"
    COMPLIANCE_AUDIT = "compliance_audit"
    COST_ANALYSIS = "cost_analysis"
    SECURITY_REVIEW = "security_review"
    PERFORMANCE_REPORT = "performance_report"
    ROI_ANALYSIS = "roi_analysis"
    MONTHLY_REVIEW = "monthly_review"
    QUARTERLY_REVIEW = "quarterly_review"
    ANNUAL_REVIEW = "annual_review"

class Language(Enum):
    """Supported languages."""
    ENGLISH = "en"
    FRENCH = "fr"
    SPANISH = "es"
    GERMAN = "de"
    JAPANESE = "ja"
    CHINESE = "zh"
    ARABIC = "ar"  # RTL support

class AccessibilityLevel(Enum):
    """PDF accessibility levels."""
    LEVEL_A = "A"  # Basic accessibility
    LEVEL_AA = "AA"  # Enhanced accessibility
    LEVEL_AAA = "AAA"  # Full accessibility

class ExportFormat(Enum):
    """Export formats."""
    PDF = "pdf"
    PDF_A = "pdf_a"  # Archival PDF
    PDF_UA = "pdf_ua"  # Universal accessibility
    PDF_X = "pdf_x"  | Print-ready

# ============================================================================
# BRANDING MANAGER
# ============================================================================

class BrandingManager:
    """Manages branding and styling compliance."""
    
    def __init__(self, brand_config: Dict[str, Any]):
        self.config = brand_config
        self._load_fonts()
        self._setup_colors()
        
    def _load_fonts(self):
        """Load and register brand fonts."""
        font_dir = Path(self.config.get('font_dir', '/usr/share/fonts'))
        
        # Register primary font
        primary_font = self.config.get('primary_font', 'Helvetica')
        primary_font_path = font_dir / f"{primary_font}.ttf"
        
        if primary_font_path.exists():
            try:
                pdfmetrics.registerFont(TTFont('PrimaryFont', str(primary_font_path)))
                self.primary_font = 'PrimaryFont'
            except Exception as e:
                logger.warning(f"Failed to load primary font: {e}")
                self.primary_font = 'Helvetica'
        else:
            self.primary_font = 'Helvetica'
        
        # Register secondary font
        secondary_font = self.config.get('secondary_font', 'Helvetica')
        secondary_font_path = font_dir / f"{secondary_font}.ttf"
        
        if secondary_font_path.exists():
            try:
                pdfmetrics.registerFont(TTFont('SecondaryFont', str(secondary_font_path)))
                self.secondary_font = 'SecondaryFont'
            except Exception as e:
                logger.warning(f"Failed to load secondary font: {e}")
                self.secondary_font = 'Helvetica'
        else:
            self.secondary_font = 'Helvetica'
        
        # Register symbol font for special characters
        pdfmetrics.registerFont(TTFont('SymbolFont', 'ZapfDingbats'))
        
    def _setup_colors(self):
        """Setup brand color palette."""
        colors_config = self.config.get('colors', {})
        
        self.colors = {
            'primary': self._parse_color(colors_config.get('primary', '#1a237e')),
            'secondary': self._parse_color(colors_config.get('secondary', '#283593')),
            'accent': self._parse_color(colors_config.get('accent', '#3949ab')),
            'success': self._parse_color(colors_config.get('success', '#43a047')),
            'warning': self._parse_color(colors_config.get('warning', '#ff9800')),
            'error': self._parse_color(colors_config.get('error', '#e53935')),
            'info': self._parse_color(colors_config.get('info', '#039be5')),
            'background': self._parse_color(colors_config.get('background', '#f5f5f5')),
            'text_primary': self._parse_color(colors_config.get('text_primary', '#212121')),
            'text_secondary': self._parse_color(colors_config.get('text_secondary', '#757575')),
            'border': self._parse_color(colors_config.get('border', '#e0e0e0')),
        }
        
    def _parse_color(self, color_str: str):
        """Parse hex color string to ReportLab color."""
        if color_str.startswith('#'):
            color_str = color_str[1:]
        
        if len(color_str) == 6:
            r = int(color_str[0:2], 16) / 255.0
            g = int(color_str[2:4], 16) / 255.0
            b = int(color_str[4:6], 16) / 255.0
            return colors.Color(r, g, b)
        elif len(color_str) == 8:
            r = int(color_str[0:2], 16) / 255.0
            g = int(color_str[2:4], 16) / 255.0
            b = int(color_str[4:6], 16) / 255.0
            a = int(color_str[6:8], 16) / 255.0
            return colors.Color(r, g, b, alpha=a)
        else:
            return colors.black
    
    def get_logo(self) -> Optional[Image]:
        """Get brand logo image."""
        logo_path = self.config.get('logo_path')
        if logo_path and Path(logo_path).exists():
            try:
                return Image(logo_path, width=2*inch, height=0.5*inch)
            except Exception as e:
                logger.warning(f"Failed to load logo: {e}")
        return None
    
    def get_watermark(self) -> Optional[str]:
        """Get watermark text."""
        return self.config.get('watermark', 'CONFIDENTIAL')
    
    def get_footer_text(self) -> str:
        """Get footer text."""
        company = self.config.get('company_name', 'MicroAgents Platform')
        website = self.config.get('website', 'https://microagents.io')
        return f"{company} | {website} | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"

# ============================================================================
# TEMPLATE MANAGER
# ============================================================================

class TemplateManager:
    """Manages professional report templates."""
    
    def __init__(self, branding: BrandingManager, language: Language = Language.ENGLISH):
        self.branding = branding
        self.language = language
        self.styles = self._create_styles()
        self.translations = self._load_translations()
        
    def _load_translations(self) -> Dict[str, str]:
        """Load language translations."""
        # In production, load from external files
        translations = {
            'en': {
                'table_of_contents': 'Table of Contents',
                'page': 'Page',
                'confidential': 'CONFIDENTIAL',
                'executive_summary': 'Executive Summary',
                'introduction': 'Introduction',
                'methodology': 'Methodology',
                'findings': 'Findings',
                'recommendations': 'Recommendations',
                'conclusion': 'Conclusion',
                'appendix': 'Appendix',
                'generated_on': 'Generated on',
                'by': 'by',
                'microagents_platform': 'MicroAgents Platform',
            },
            'fr': {
                'table_of_contents': 'Table des Matières',
                'page': 'Page',
                'confidential': 'CONFIDENTIEL',
                'executive_summary': 'Résumé Exécutif',
                'introduction': 'Introduction',
                'methodology': 'Méthodologie',
                'findings': 'Constats',
                'recommendations': 'Recommandations',
                'conclusion': 'Conclusion',
                'appendix': 'Annexe',
                'generated_on': 'Généré le',
                'by': 'par',
                'microagents_platform': 'Plateforme MicroAgents',
            },
            # Add other languages...
        }
        return translations.get(self.language.value, translations['en'])
    
    def _create_styles(self):
        """Create professional paragraph styles."""
        styles = getSampleStyleSheet()
        
        # Title Style
        styles.add(ParagraphStyle(
            name='Title',
            parent=styles['Title'],
            fontName=self.branding.primary_font,
            fontSize=24,
            textColor=self.branding.colors['primary'],
            spaceAfter=30,
            alignment=TA_CENTER
        ))
        
        # Heading 1
        styles.add(ParagraphStyle(
            name='Heading1',
            parent=styles['Heading1'],
            fontName=self.branding.primary_font,
            fontSize=18,
            textColor=self.branding.colors['primary'],
            spaceBefore=20,
            spaceAfter=12,
            borderPadding=5,
            borderColor=self.branding.colors['border'],
            borderWidth=1,
            borderRadius=3,
            backColor=self.branding.colors['background']
        ))
        
        # Heading 2
        styles.add(ParagraphStyle(
            name='Heading2',
            parent=styles['Heading2'],
            fontName=self.branding.primary_font,
            fontSize=16,
            textColor=self.branding.colors['secondary'],
            spaceBefore=15,
            spaceAfter=10
        ))
        
        # Heading 3
        styles.add(ParagraphStyle(
            name='Heading3',
            parent=styles['Heading3'],
            fontName=self.branding.primary_font,
            fontSize=14,
            textColor=self.branding.colors['accent'],
            spaceBefore=10,
            spaceAfter=8
        ))
        
        # Normal Text
        styles.add(ParagraphStyle(
            name='NormalText',
            parent=styles['Normal'],
            fontName=self.branding.secondary_font,
            fontSize=10,
            textColor=self.branding.colors['text_primary'],
            spaceAfter=8,
            alignment=TA_JUSTIFY,
            wordWrap='CJK'
        ))
        
        # Bullet Text
        styles.add(ParagraphStyle(
            name='BulletText',
            parent=styles['Normal'],
            fontName=self.branding.secondary_font,
            fontSize=10,
            textColor=self.branding.colors['text_primary'],
            leftIndent=20,
            spaceAfter=5
        ))
        
        # Quote Style
        styles.add(ParagraphStyle(
            name='Quote',
            parent=styles['Normal'],
            fontName=self.branding.secondary_font,
            fontSize=10,
            textColor=self.branding.colors['text_secondary'],
            leftIndent=40,
            rightIndent=40,
            spaceBefore=10,
            spaceAfter=10,
            borderLeftColor=self.branding.colors['border'],
            borderLeftWidth=3,
            borderLeftPadding=10,
            backColor=colors.Color(0.95, 0.95, 0.95)
        ))
        
        # Table Header
        styles.add(ParagraphStyle(
            name='TableHeader',
            parent=styles['Normal'],
            fontName=self.branding.primary_font,
            fontSize=10,
            textColor=colors.white,
            alignment=TA_CENTER,
            backColor=self.branding.colors['primary']
        ))
        
        # Table Cell
        styles.add(ParagraphStyle(
            name='TableCell',
            parent=styles['Normal'],
            fontName=self.branding.secondary_font,
            fontSize=9,
            textColor=self.branding.colors['text_primary'],
            alignment=TA_LEFT
        ))
        
        # Footer Style
        styles.add(ParagraphStyle(
            name='Footer',
            parent=styles['Normal'],
            fontName=self.branding.secondary_font,
            fontSize=8,
            textColor=self.branding.colors['text_secondary'],
            alignment=TA_CENTER
        ))
        
        # Code Style
        styles.add(ParagraphStyle(
            name='Code',
            parent=styles['Normal'],
            fontName='Courier',
            fontSize=9,
            textColor=self.branding.colors['text_primary'],
            backColor=colors.Color(0.97, 0.97, 0.97),
            borderColor=self.branding.colors['border'],
            borderWidth=1,
            borderPadding=5,
            leftIndent=20
        ))
        
        return styles
    
    def get_template(self, report_type: ReportType) -> Dict[str, Any]:
        """Get template configuration for report type."""
        templates = {
            ReportType.EXECUTIVE_SUMMARY: {
                'page_size': letter,
                'orientation': 'portrait',
                'margins': (1*inch, 1*inch, 1*inch, 1*inch),
                'sections': [
                    'title_page',
                    'table_of_contents',
                    'executive_summary',
                    'key_metrics',
                    'recommendations',
                    'appendix'
                ]
            },
            ReportType.TECHNICAL_DETAIL: {
                'page_size': letter,
                'orientation': 'portrait',
                'margins': (0.75*inch, 0.75*inch, 0.75*inch, 0.75*inch),
                'sections': [
                    'title_page',
                    'table_of_contents',
                    'introduction',
                    'methodology',
                    'detailed_findings',
                    'technical_analysis',
                    'conclusion',
                    'appendix'
                ]
            },
            ReportType.COMPLIANCE_AUDIT: {
                'page_size': A4,
                'orientation': 'portrait',
                'margins': (1*inch, 1*inch, 1*inch, 1*inch),
                'sections': [
                    'title_page',
                    'compliance_statement',
                    'audit_scope',
                    'control_assessment',
                    'findings',
                    'remediation_plan',
                    'evidence_summary',
                    'signatures'
                ]
            },
            ReportType.COST_ANALYSIS: {
                'page_size': landscape(A4),
                'orientation': 'landscape',
                'margins': (0.5*inch, 0.5*inch, 0.5*inch, 0.5*inch),
                'sections': [
                    'title_page',
                    'executive_summary',
                    'cost_breakdown',
                    'savings_opportunities',
                    'recommendations',
                    'detailed_analysis',
                    'appendix'
                ]
            }
        }
        
        return templates.get(report_type, templates[ReportType.EXECUTIVE_SUMMARY])

# ============================================================================
# DATA VISUALIZATION
# ============================================================================

class DataVisualizer:
    """Creates charts and visualizations for reports."""
    
    def __init__(self, branding: BrandingManager):
        self.branding = branding
        self.chart_width = 400
        self.chart_height = 200
        
    def create_bar_chart(self, data: Dict[str, List[float]], 
                        categories: List[str],
                        title: str = "",
                        x_label: str = "",
                        y_label: str = "") -> Drawing:
        """Create a vertical bar chart."""
        drawing = Drawing(self.chart_width, self.chart_height)
        
        # Prepare data
        chart_data = []
        series_names = list(data.keys())
        
        for series in series_names:
            chart_data.append(data[series])
        
        # Create chart
        bc = VerticalBarChart()
        bc.x = 50
        bc.y = 50
        bc.height = self.chart_height - 100
        bc.width = self.chart_width - 100
        bc.data = chart_data
        bc.strokeColor = self.branding.colors['border']
        bc.strokeWidth = 1
        
        # Set category labels
        bc.categoryAxis.categoryNames = categories
        bc.categoryAxis.labels.boxAnchor = 'ne'
        bc.categoryAxis.labels.dx = 8
        bc.categoryAxis.labels.dy = -2
        bc.categoryAxis.labels.angle = 45
        bc.categoryAxis.labels.fontName = self.branding.secondary_font
        bc.categoryAxis.labels.fontSize = 8
        
        # Set value axis
        bc.valueAxis.labelTextFormat = '%d'
        bc.valueAxis.labels.fontName = self.branding.secondary_font
        bc.valueAxis.labels.fontSize = 8
        
        # Set bar colors
        colors = [
            self.branding.colors['primary'],
            self.branding.colors['secondary'],
            self.branding.colors['accent'],
            self.branding.colors['success'],
            self.branding.colors['warning']
        ]
        
        for i, color in enumerate(colors[:len(series_names)]):
            bc.bars[i].fillColor = color
        
        # Add title
        if title:
            title_text = String(self.chart_width/2, self.chart_height - 20, title,
                              textAnchor='middle',
                              fontName=self.branding.primary_font,
                              fontSize=12,
                              fillColor=self.branding.colors['text_primary'])
            drawing.add(title_text)
        
        # Add axis labels
        if x_label:
            x_label_text = String(self.chart_width/2, 30, x_label,
                                textAnchor='middle',
                                fontName=self.branding.secondary_font,
                                fontSize=9,
                                fillColor=self.branding.colors['text_secondary'])
            drawing.add(x_label_text)
        
        if y_label:
            y_label_text = String(20, self.chart_height/2, y_label,
                                textAnchor='middle',
                                fontName=self.branding.secondary_font,
                                fontSize=9,
                                fillColor=self.branding.colors['text_secondary'],
                                angle=90)
            drawing.add(y_label_text)
        
        drawing.add(bc)
        return drawing
    
    def create_line_chart(self, data: Dict[str, List[float]],
                         categories: List[str],
                         title: str = "",
                         x_label: str = "",
                         y_label: str = "") -> Drawing:
        """Create a line chart."""
        drawing = Drawing(self.chart_width, self.chart_height)
        
        # Prepare data
        chart_data = []
        series_names = list(data.keys())
        
        for series in series_names:
            chart_data.append(data[series])
        
        # Create chart
        lc = HorizontalLineChart()
        lc.x = 50
        lc.y = 50
        lc.height = self.chart_height - 100
        lc.width = self.chart_width - 100
        lc.data = chart_data
        lc.strokeColor = self.branding.colors['border']
        lc.strokeWidth = 1
        
        # Set category labels
        lc.categoryAxis.categoryNames = categories
        lc.categoryAxis.labels.fontName = self.branding.secondary_font
        lc.categoryAxis.labels.fontSize = 8
        
        # Set value axis
        lc.valueAxis.labelTextFormat = '%d'
        lc.valueAxis.labels.fontName = self.branding.secondary_font
        lc.valueAxis.labels.fontSize = 8
        
        # Set line colors and markers
        line_colors = [
            self.branding.colors['primary'],
            self.branding.colors['secondary'],
            self.branding.colors['accent'],
            self.branding.colors['success'],
            self.branding.colors['warning']
        ]
        
        for i, color in enumerate(line_colors[:len(series_names)]):
            lc.lines[i].strokeColor = color
            lc.lines[i].strokeWidth = 2
            lc.lines.symbol = makeMarker('FilledCircle')
            lc.lines[i].symbol.strokeColor = color
            lc.lines[i].symbol.fillColor = color
            lc.lines[i].symbol.size = 4
        
        # Add title
        if title:
            title_text = String(self.chart_width/2, self.chart_height - 20, title,
                              textAnchor='middle',
                              fontName=self.branding.primary_font,
                              fontSize=12,
                              fillColor=self.branding.colors['text_primary'])
            drawing.add(title_text)
        
        drawing.add(lc)
        return drawing
    
    def create_pie_chart(self, data: Dict[str, float],
                        title: str = "") -> Drawing:
        """Create a pie chart."""
        drawing = Drawing(self.chart_width, self.chart_height)
        
        pie = Pie()
        pie.x = self.chart_width / 2
        pie.y = self.chart_height / 2 - 20
        pie.width = 200
        pie.height = 200
        
        # Prepare data
        labels = list(data.keys())
        values = list(data.values())
        
        pie.data = values
        pie.labels = labels
        
        # Set colors
        pie_colors = [
            self.branding.colors['primary'],
            self.branding.colors['secondary'],
            self.branding.colors['accent'],
            self.branding.colors['success'],
            self.branding.colors['warning'],
            self.branding.colors['error'],
            self.branding.colors['info']
        ]
        
        for i, color in enumerate(pie_colors[:len(labels)]):
            pie.slices[i].fillColor = color
        
        # Add percentages
        pie.simpleLabels = 0
        
        # Add title
        if title:
            title_text = String(self.chart_width/2, self.chart_height - 30, title,
                              textAnchor='middle',
                              fontName=self.branding.primary_font,
                              fontSize=12,
                              fillColor=self.branding.colors['text_primary'])
            drawing.add(title_text)
        
        drawing.add(pie)
        return drawing
    
    def create_metric_card(self, title: str, value: str, 
                          change: Optional[str] = None,
                          icon: Optional[str] = None) -> Drawing:
        """Create a metric card visualization."""
        drawing = Drawing(200, 100)
        
        # Card background
        from reportlab.graphics.shapes import Rect
        card = Rect(0, 0, 200, 100, rx=5, ry=5)
        card.fillColor = self.branding.colors['background']
        card.strokeColor = self.branding.colors['border']
        card.strokeWidth = 1
        drawing.add(card)
        
        # Title
        title_text = String(10, 75, title,
                          fontName=self.branding.secondary_font,
                          fontSize=10,
                          fillColor=self.branding.colors['text_secondary'])
        drawing.add(title_text)
        
        # Value
        value_text = String(10, 50, value,
                          fontName=self.branding.primary_font,
                          fontSize=24,
                          fillColor=self.branding.colors['text_primary'])
        drawing.add(value_text)
        
        # Change indicator
        if change:
            change_text = String(10, 30, change,
                               fontName=self.branding.secondary_font,
                               fontSize=9,
                               fillColor=self._get_change_color(change))
            drawing.add(change_text)
        
        return drawing
    
    def _get_change_color(self, change: str) -> colors.Color:
        """Get color based on change value."""
        if change.startswith('+'):
            return self.branding.colors['success']
        elif change.startswith('-'):
            return self.branding.colors['error']
        else:
            return self.branding.colors['text_secondary']

# ============================================================================
# ACCESSIBILITY MANAGER
# ============================================================================

class AccessibilityManager:
    """Manages PDF accessibility features."""
    
    def __init__(self, level: AccessibilityLevel = AccessibilityLevel.LEVEL_AA):
        self.level = level
        self.tags = {}
        
    def add_structure(self, element_type: str, content: str, 
                     language: str = "en") -> Dict[str, Any]:
        """Add accessibility structure to content."""
        structure = {
            'type': element_type,
            'content': content,
            'language': language,
            'alt_text': '',
            'reading_order': len(self.tags) + 1
        }
        
        tag_id = f"tag_{len(self.tags)}"
        self.tags[tag_id] = structure
        
        return {'tag_id': tag_id, 'structure': structure}
    
    def add_alt_text(self, tag_id: str, alt_text: str):
        """Add alternative text for images or charts."""
        if tag_id in self.tags:
            self.tags[tag_id]['alt_text'] = alt_text
    
    def generate_accessibility_metadata(self, doc_info: Dict[str, Any]) -> Dict[str, Any]:
        """Generate accessibility metadata for PDF."""
        metadata = {
            'pdf_version': '1.7',
            'language': doc_info.get('language', 'en'),
            'title': doc_info.get('title', ''),
            'author': doc_info.get('author', ''),
            'subject': doc_info.get('subject', ''),
            'keywords': doc_info.get('keywords', []),
            'creation_date': datetime.now().isoformat(),
            'modification_date': datetime.now().isoformat(),
            'accessibility': {
                'level': self.level.value,
                'tagged': True,
                'language_specified': True,
                'title_specified': True,
                'logical_reading_order': True,
                'alternative_texts': any(tag.get('alt_text') for tag in self.tags.values())
            },
            'tags': self.tags
        }
        
        return metadata
    
    def create_accessible_table(self, data: List[List[Any]], 
                               headers: List[str]) -> List[Dict[str, Any]]:
        """Create accessible table structure."""
        table_structure = []
        
        # Add header row
        header_row = {
            'type': 'TR',
            'children': []
        }
        
        for header in headers:
            header_cell = {
                'type': 'TH',
                'content': str(header),
                'scope': 'col'
            }
            header_row['children'].append(header_cell)
        
        table_structure.append(header_row)
        
        # Add data rows
        for row in data:
            data_row = {
                'type': 'TR',
                'children': []
            }
            
            for cell in row:
                data_cell = {
                    'type': 'TD',
                    'content': str(cell)
                }
                data_row['children'].append(data_cell)
            
            table_structure.append(data_row)
        
        return table_structure

# ============================================================================
# SECURITY MANAGER
# ============================================================================

class SecurityManager:
    """Manages PDF security features."""
    
    def __init__(self, certificate_path: Optional[str] = None,
                 private_key_path: Optional[str] = None):
        self.certificate_path = certificate_path
        self.private_key_path = private_key_path
        self.certificate = None
        self.private_key = None
        
        if certificate_path and private_key_path:
            self._load_certificate()
    
    def _load_certificate(self):
        """Load digital certificate and private key."""
        try:
            with open(self.certificate_path, 'rb') as f:
                cert_data = f.read()
                self.certificate = x509.load_pem_x509_certificate(cert_data)
            
            with open(self.private_key_path, 'rb') as f:
                key_data = f.read()
                self.private_key = load_pem_private_key(
                    key_data,
                    password=None
                )
        except Exception as e:
            logger.warning(f"Failed to load certificate: {e}")
    
    def generate_digital_signature(self, pdf_content: bytes) -> Optional[bytes]:
        """Generate digital signature for PDF."""
        if not self.private_key:
            return None
        
        try:
            signature = self.private_key.sign(
                pdf_content,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
            
            return signature
        except Exception as e:
            logger.error(f"Failed to generate signature: {e}")
            return None
    
    def encrypt_pdf(self, pdf_content: bytes, 
                   password: str,
                   permissions: List[str] = None) -> bytes:
        """Encrypt PDF with password protection."""
        try:
            reader = PdfReader(io.BytesIO(pdf_content))
            writer = PdfWriter()
            
            # Copy all pages
            for page in reader.pages:
                writer.add_page(page)
            
            # Set permissions
            if permissions is None:
                permissions = ['print', 'modify', 'copy', 'annot-forms']
            
            # Encrypt
            writer.encrypt(
                user_password=password,
                owner_password=None,
                permissions_flag=self._get_permissions_flag(permissions)
            )
            
            # Write to buffer
            output_buffer = io.BytesIO()
            writer.write(output_buffer)
            
            return output_buffer.getvalue()
        except Exception as e:
            logger.error(f"Failed to encrypt PDF: {e}")
            return pdf_content
    
    def _get_permissions_flag(self, permissions: List[str]) -> int:
        """Convert permissions list to PDF permission flag."""
        permission_map = {
            'print': 4,          # Print the document
            'modify': 8,         # Modify the contents
            'copy': 16,          # Copy text and graphics
            'annot-forms': 32,   # Add annotations and forms
            'fill-forms': 256,   # Fill in existing form fields
            'extract': 512,      # Extract text and graphics
            'assemble': 1024,    # Assemble the document
            'print-high': 2048   # Print in high quality
        }
        
        flag = 0
        for perm in permissions:
            if perm in permission_map:
                flag |= permission_map[perm]
        
        return flag
    
    def add_watermark(self, pdf_content: bytes, 
                     text: str = "CONFIDENTIAL",
                     opacity: float = 0.3) -> bytes:
        """Add watermark to PDF."""
        try:
            reader = PdfReader(io.BytesIO(pdf_content))
            writer = PdfWriter()
            
            for page_num, page in enumerate(reader.pages):
                # Create watermark
                packet = io.BytesIO()
                can = canvas.Canvas(packet, pagesize=page.mediabox[2:])
                
                # Set transparency
                can.setFillAlpha(opacity)
                can.setFont("Helvetica", 60)
                can.setFillColor(colors.grey)
                
                # Rotate and position watermark
                can.saveState()
                can.translate(page.mediabox[2] / 2, page.mediabox[3] / 2)
                can.rotate(45)
                
                # Draw watermark
                can.drawCentredString(0, 0, text)
                can.restoreState()
                
                can.save()
                packet.seek(0)
                watermark = PdfReader(packet)
                
                # Merge watermark with page
                page.merge_page(watermark.pages[0])
                writer.add_page(page)
            
            # Write to buffer
            output_buffer = io.BytesIO()
            writer.write(output_buffer)
            
            return output_buffer.getvalue()
        except Exception as e:
            logger.error(f"Failed to add watermark: {e}")
            return pdf_content

# ============================================================================
# PDF GENERATOR
# ============================================================================

class PDFGenerator:
    """Main PDF generator class."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or PDF_CONFIG
        self.branding = BrandingManager(self.config.get('branding', {}))
        self.accessibility = AccessibilityManager()
        self.security = SecurityManager(
            self.config.get('certificate_path'),
            self.config.get('private_key_path')
        )
        self.visualizer = DataVisualizer(self.branding)
        
        # Performance optimization
        self.executor = ThreadPoolExecutor(max_workers=4)
        self._template_cache = {}
    
    def generate_report(self, report_type: ReportType,
                       data: Dict[str, Any],
                       language: Language = Language.ENGLISH,
                       export_format: ExportFormat = ExportFormat.PDF,
                       output_path: Optional[str] = None) -> Optional[bytes]:
        """Generate a complete report."""
        try:
            logger.info(f"Generating {report_type.value} report in {language.value}")
            
            # Setup template
            template_mgr = TemplateManager(self.branding, language)
            template = template_mgr.get_template(report_type)
            styles = template_mgr.styles
            
            # Create output buffer
            buffer = io.BytesIO()
            
            # Create document with metadata
            doc = SimpleDocTemplate(
                buffer,
                pagesize=template['page_size'],
                rightMargin=template['margins'][0],
                leftMargin=template['margins'][1],
                topMargin=template['margins'][2],
                bottomMargin=template['margins'][3],
                title=data.get('title', 'MicroAgents Report'),
                author=data.get('author', 'MicroAgents Platform'),
                subject=data.get('subject', 'DevOps Intelligence Report'),
                creator='MicroAgents PDF Generator v1.0',
                keywords=data.get('keywords', ['DevOps', 'Monitoring', 'Analytics']),
                lang=language.value
            )
            
            # Build story (document content)
            story = self._build_story(report_type, data, template_mgr, template)
            
            # Build document with accessibility
            doc.build(
                story,
                onFirstPage=self._add_header_footer(template_mgr),
                onLaterPages=self._add_header_footer(template_mgr)
            )
            
            # Get PDF content
            pdf_content = buffer.getvalue()
            
            # Apply security features
            if self.config.get('encrypt', False):
                password = self.config.get('encryption_password', 'microagents')
                pdf_content = self.security.encrypt_pdf(
                    pdf_content,
                    password,
                    permissions=['print', 'copy']
                )
            
            if self.config.get('add_watermark', False):
                watermark_text = self.branding.get_watermark()
                pdf_content = self.security.add_watermark(
                    pdf_content,
                    watermark_text,
                    opacity=0.2
                )
            
            # Add digital signature if certificate available
            if self.config.get('digital_signature', False):
                signature = self.security.generate_digital_signature(pdf_content)
                if signature:
                    # In production, embed signature in PDF
                    pass
            
            # Apply compression if enabled
            if self.config.get('compress', True):
                pdf_content = self._compress_pdf(pdf_content)
            
            # Save to file or return bytes
            if output_path:
                with open(output_path, 'wb') as f:
                    f.write(pdf_content)
                logger.info(f"Report saved to {output_path}")
                return None
            else:
                return pdf_content
            
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            raise
    
    def _build_story(self, report_type: ReportType,
                    data: Dict[str, Any],
                    template_mgr: TemplateManager,
                    template: Dict[str, Any]) -> List[Any]:
        """Build the document story (content)."""
        story = []
        styles = template_mgr.styles
        
        # Title Page
        story.extend(self._create_title_page(data, template_mgr))
        story.append(PageBreak())
        
        # Table of Contents
        story.extend(self._create_table_of_contents(data, template_mgr))
        story.append(PageBreak())
        
        # Report Sections based on template
        for section in template.get('sections', []):
            if section == 'executive_summary':
                story.extend(self._create_executive_summary(data, template_mgr))
            elif section == 'key_metrics':
                story.extend(self._create_key_metrics(data, template_mgr))
            elif section == 'technical_analysis':
                story.extend(self._create_technical_analysis(data, template_mgr))
            elif section == 'compliance_assessment':
                story.extend(self._create_compliance_assessment(data, template_mgr))
            elif section == 'cost_analysis':
                story.extend(self._create_cost_analysis(data, template_mgr))
            elif section == 'recommendations':
                story.extend(self._create_recommendations(data, template_mgr))
            elif section == 'appendix':
                story.extend(self._create_appendix(data, template_mgr))
            
            if section != template['sections'][-1]:  # Not the last section
                story.append(PageBreak())
        
        return story
    
    def _create_title_page(self, data: Dict[str, Any], 
                          template_mgr: TemplateManager) -> List[Any]:
        """Create title page."""
        story = []
        styles = template_mgr.styles
        
        # Logo
        logo = self.branding.get_logo()
        if logo:
            story.append(logo)
            story.append(Spacer(1, 40))
        
        # Title
        title = data.get('title', 'MicroAgents Platform Report')
        story.append(Paragraph(title, styles['Title']))
        story.append(Spacer(1, 20))
        
        # Subtitle
        subtitle = data.get('subtitle', 'DevOps Intelligence & Analytics')
        story.append(Paragraph(subtitle, styles['Heading2']))
        story.append(Spacer(1, 40))
        
        # Report Metadata Table
        metadata = [
            ['Report ID:', data.get('report_id', 'N/A')],
            ['Period:', data.get('period', 'N/A')],
            ['Generated:', datetime.now().strftime('%Y-%m-%d %H:%M UTC')],
            ['Timezone:', data.get('timezone', 'UTC')],
            ['Language:', template_mgr.language.value.upper()],
            ['Version:', data.get('version', '1.0')]
        ]
        
        metadata_table = Table(metadata, colWidths=[100, 300])
        metadata_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), template_mgr.branding.secondary_font),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (0, 0), (0, -1), template_mgr.branding.colors['text_secondary']),
            ('TEXTCOLOR', (1, 0), (1, -1), template_mgr.branding.colors['text_primary']),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BOX', (0, 0), (-1, -1), 1, template_mgr.branding.colors['border']),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, template_mgr.branding.colors['border']),
        ]))
        
        story.append(metadata_table)
        story.append(Spacer(1, 60))
        
        # Confidential Notice
        if data.get('confidential', True):
            notice = template_mgr.translations['confidential']
            story.append(Paragraph(notice, styles['Heading3']))
        
        return story
    
    def _create_table_of_contents(self, data: Dict[str, Any],
                                 template_mgr: TemplateManager) -> List[Any]:
        """Create table of contents."""
        story = []
        styles = template_mgr.styles
        
        # TOC Title
        toc_title = template_mgr.translations['table_of_contents']
        story.append(Paragraph(toc_title, styles['Heading1']))
        story.append(Spacer(1, 20))
        
        # TOC Entries (simplified - in production use proper TOC generation)
        toc_entries = [
            ('Executive Summary', 1),
            ('Key Metrics & Findings', 3),
            ('Technical Analysis', 5),
            ('Recommendations', 8),
            ('Compliance Assessment', 10),
            ('Cost Analysis', 12),
            ('Appendix', 15)
        ]
        
        for entry, page in toc_entries:
            row = [
                Paragraph(entry, styles['NormalText']),
                Paragraph(f'Page {page}', styles['NormalText'])
            ]
            
            toc_table = Table([row], colWidths=[400, 100])
            toc_table.setStyle(TableStyle([
                ('LEFTPADDING', (0, 0), (0, 0), 20),
                ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            
            story.append(toc_table)
            story.append(Spacer(1, 5))
        
        return story
    
    def _create_executive_summary(self, data: Dict[str, Any],
                                 template_mgr: TemplateManager) -> List[Any]:
        """Create executive summary section."""
        story = []
        styles = template_mgr.styles
        
        story.append(Paragraph('Executive Summary', styles['Heading1']))
        story.append(Spacer(1, 10))
        
        # Summary text
        summary = data.get('executive_summary', 'No summary provided.')
        story.append(Paragraph(summary, styles['NormalText']))
        story.append(Spacer(1, 20))
        
        # Key Findings
        story.append(Paragraph('Key Findings', styles['Heading2']))
        
        findings = data.get('key_findings', [])
        if findings:
            findings_list = []
            for finding in findings:
                findings_list.append(ListItem(
                    Paragraph(finding, styles['BulletText']),
                    bulletColor=template_mgr.branding.colors['primary']
                ))
            
            story.append(ListFlowable(findings_list, bulletType='bullet'))
        
        story.append(Spacer(1, 20))
        
        # ROI Summary
        roi_data = data.get('roi_summary', {})
        if roi_data:
            story.append(Paragraph('ROI Summary', styles['Heading2']))
            
            roi_table_data = [
                ['Metric', 'Value', 'Impact']
            ]
            
            for metric, value in roi_data.items():
                roi_table_data.append([metric, str(value), 'Positive'])
            
            roi_table = Table(roi_table_data, colWidths=[150, 100, 100])
            roi_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), template_mgr.branding.colors['primary']),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), template_mgr.branding.primary_font),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), template_mgr.branding.colors['background']),
                ('TEXTCOLOR', (0, 1), (-1, -1), template_mgr.branding.colors['text_primary']),
                ('ALIGN', (1, 1), (1, -1), 'CENTER'),
                ('FONTNAME', (0, 1), (-1, -1), template_mgr.branding.secondary_font),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, template_mgr.branding.colors['border']),
            ]))
            
            story.append(roi_table)
        
        return story
    
    def _create_key_metrics(self, data: Dict[str, Any],
                           template_mgr: TemplateManager) -> List[Any]:
        """Create key metrics section with visualizations."""
        story = []
        styles = template_mgr.styles
        
        story.append(Paragraph('Key Metrics', styles['Heading1']))
        story.append(Spacer(1, 10))
        
        # Metric Cards
        metrics = data.get('metrics', {})
        if metrics:
            # Create a table of metric cards
            metric_cards = []
            row = []
            
            for i, (metric_name, metric_data) in enumerate(metrics.items()):
                card = self.visualizer.create_metric_card(
                    metric_data.get('title', metric_name),
                    metric_data.get('value', 'N/A'),
                    metric_data.get('change'),
                    metric_data.get('icon')
                )
                
                row.append(card)
                
                # Two cards per row
                if len(row) == 2 or i == len(metrics) - 1:
                    metric_cards.append(row.copy())
                    row = []
            
            for card_row in metric_cards:
                # Convert drawings to ReportLab elements
                # Note: In production, would need proper rendering
                pass
        
        # Performance Chart
        perf_data = data.get('performance_data', {})
        if perf_data:
            story.append(Paragraph('Performance Trends', styles['Heading2']))
            
            chart = self.visualizer.create_line_chart(
                perf_data.get('series', {}),
                perf_data.get('categories', []),
                perf_data.get('title', 'Performance Over Time'),
                perf_data.get('x_label', 'Time'),
                perf_data.get('y_label', 'Value')
            )
            
            story.append(chart)
            story.append(Spacer(1, 20))
        
        # Cost Distribution Chart
        cost_data = data.get('cost_distribution', {})
        if cost_data:
            story.append(Paragraph('Cost Distribution', styles['Heading2']))
            
            chart = self.visualizer.create_pie_chart(
                cost_data,
                'Cost Breakdown by Category'
            )
            
            story.append(chart)
        
        return story
    
    def _create_technical_analysis(self, data: Dict[str, Any],
                                  template_mgr: TemplateManager) -> List[Any]:
        """Create technical analysis section."""
        story = []
        styles = template_mgr.styles
        
        story.append(Paragraph('Technical Analysis', styles['Heading1']))
        story.append(Spacer(1, 10))
        
        # Detailed findings
        findings = data.get('technical_findings', [])
        for finding in findings:
            story.append(Paragraph(finding.get('title', ''), styles['Heading2']))
            story.append(Paragraph(finding.get('description', ''), styles['NormalText']))
            
            # Add code example if present
            if finding.get('code_example'):
                story.append(Paragraph('Example:', styles['Heading3']))
                story.append(Paragraph(
                    finding['code_example'],
                    styles['Code']
                ))
            
            story.append(Spacer(1, 10))
        
        return story
    
    def _create_compliance_assessment(self, data: Dict[str, Any],
                                     template_mgr: TemplateManager) -> List[Any]:
        """Create compliance assessment section."""
        story = []
        styles = template_mgr.styles
        
        story.append(Paragraph('Compliance Assessment', styles['Heading1']))
        story.append(Spacer(1, 10))
        
        compliance_data = data.get('compliance', {})
        
        for standard, controls in compliance_data.items():
            story.append(Paragraph(standard, styles['Heading2']))
            
            # Compliance controls table
            table_data = [['Control ID', 'Requirement', 'Status', 'Evidence']]
            
            for control in controls:
                table_data.append([
                    control.get('id', ''),
                    control.get('requirement', ''),
                    control.get('status', ''),
                    control.get('evidence', '')
                ])
            
            table = Table(table_data, colWidths=[80, 200, 80, 80])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), template_mgr.branding.colors['primary']),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), template_mgr.branding.primary_font),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), template_mgr.branding.colors['background']),
                ('TEXTCOLOR', (0, 1), (-1, -1), template_mgr.branding.colors['text_primary']),
                ('FONTNAME', (0, 1), (-1, -1), template_mgr.branding.secondary_font),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, template_mgr.branding.colors['border']),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            
            story.append(table)
            story.append(Spacer(1, 20))
        
        return story
    
    def _create_cost_analysis(self, data: Dict[str, Any],
                             template_mgr: TemplateManager) -> List[Any]:
        """Create cost analysis section."""
        story = []
        styles = template_mgr.styles
        
        story.append(Paragraph('Cost Analysis', styles['Heading1']))
        story.append(Spacer(1, 10))
        
        cost_data = data.get('cost_analysis', {})
        
        # Cost breakdown chart
        if cost_data.get('breakdown'):
            chart = self.visualizer.create_bar_chart(
                cost_data['breakdown'].get('series', {}),
                cost_data['breakdown'].get('categories', []),
                'Cost Breakdown by Service',
                'Service',
                'Cost (USD)'
            )
            
            story.append(chart)
            story.append(Spacer(1, 20))
        
        # Savings opportunities
        savings = cost_data.get('savings_opportunities', [])
        if savings:
            story.append(Paragraph('Savings Opportunities', styles['Heading2']))
            
            for opportunity in savings:
                story.append(Paragraph(
                    f"• {opportunity.get('description', '')}: "
                    f"${opportunity.get('potential_savings', 0):,.2f}",
                    styles['BulletText']
                ))
        
        return story
    
    def _create_recommendations(self, data: Dict[str, Any],
                               template_mgr: TemplateManager) -> List[Any]:
        """Create recommendations section."""
        story = []
        styles = template_mgr.styles
        
        story.append(Paragraph('Recommendations', styles['Heading1']))
        story.append(Spacer(1, 10))
        
        recommendations = data.get('recommendations', [])
        
        for i, rec in enumerate(recommendations, 1):
            story.append(Paragraph(f'{i}. {rec.get("title", "")}', styles['Heading2']))
            
            # Priority badge
            priority = rec.get('priority', 'medium').upper()
            priority_color = {
                'HIGH': template_mgr.branding.colors['error'],
                'MEDIUM': template_mgr.branding.colors['warning'],
                'LOW': template_mgr.branding.colors['success']
            }.get(priority, template_mgr.branding.colors['text_secondary'])
            
            # In production, create a proper badge element
            story.append(Paragraph(
                f'Priority: <font color="{priority_color.hexval()}">{priority}</font>',
                styles['NormalText']
            ))
            
            story.append(Paragraph(rec.get('description', ''), styles['NormalText']))
            
            # Implementation details
            if rec.get('implementation'):
                story.append(Paragraph('Implementation:', styles['Heading3']))
                impl_list = []
                for step in rec['implementation']:
                    impl_list.append(ListItem(
                        Paragraph(step, styles['BulletText']),
                        bulletColor=template_mgr.branding.colors['accent']
                    ))
                
                story.append(ListFlowable(impl_list, bulletType='bullet'))
            
            # Expected impact
            if rec.get('expected_impact'):
                story.append(Paragraph('Expected Impact:', styles['Heading3']))
                story.append(Paragraph(rec['expected_impact'], styles['NormalText']))
            
            story.append(Spacer(1, 15))
        
        return story
    
    def _create_appendix(self, data: Dict[str, Any],
                        template_mgr: TemplateManager) -> List[Any]:
        """Create appendix section."""
        story = []
        styles = template_mgr.styles
        
        story.append(Paragraph('Appendix', styles['Heading1']))
        story.append(Spacer(1, 10))
        
        # Glossary
        glossary = data.get('glossary', {})
        if glossary:
            story.append(Paragraph('Glossary', styles['Heading2']))
            
            glossary_data = [['Term', 'Definition']]
            for term, definition in glossary.items():
                glossary_data.append([term, definition])
            
            glossary_table = Table(glossary_data, colWidths=[150, 350])
            glossary_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), template_mgr.branding.colors['primary']),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), template_mgr.branding.primary_font),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                ('BACKGROUND', (0, 1), (-1, -1), template_mgr.branding.colors['background']),
                ('FONTNAME', (0, 1), (-1, -1), template_mgr.branding.secondary_font),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 1, template_mgr.branding.colors['border']),
            ]))
            
            story.append(glossary_table)
            story.append(Spacer(1, 20))
        
        # References
        references = data.get('references', [])
        if references:
            story.append(Paragraph('References', styles['Heading2']))
            
            ref_list = []
            for ref in references:
                ref_list.append(ListItem(
                    Paragraph(ref, styles['BulletText']),
                    bulletColor=template_mgr.branding.colors['text_secondary']
                ))
            
            story.append(ListFlowable(ref_list, bulletType='bullet'))
        
        return story
    
    def _add_header_footer(self, template_mgr: TemplateManager):
        """Create header/footer function."""
        def header_footer(canvas, doc):
            # Save canvas state
            canvas.saveState()
            
            # Header
            header_y = doc.pagesize[1] - 0.5*inch
            
            # Logo in header
            logo = template_mgr.branding.get_logo()
            if logo:
                try:
                    canvas.drawImage(
                        logo.filename,
                        0.5*inch,
                        header_y - 0.25*inch,
                        width=1.5*inch,
                        height=0.375*inch,
                        preserveAspectRatio=True
                    )
                except:
                    pass
            
            # Report title in header
            canvas.setFont(template_mgr.branding.secondary_font, 10)
            canvas.setFillColor(template_mgr.branding.colors['text_secondary'])
            canvas.drawRightString(
                doc.pagesize[0] - 0.5*inch,
                header_y,
                doc.title
            )
            
            # Header line
            canvas.setStrokeColor(template_mgr.branding.colors['border'])
            canvas.setLineWidth(0.5)
            canvas.line(0.5*inch, header_y - 0.25*inch,
                       doc.pagesize[0] - 0.5*inch, header_y - 0.25*inch)
            
            # Footer
            footer_y = 0.5*inch
            
            # Page number
            page_num = canvas.getPageNumber()
            canvas.setFont(template_mgr.branding.secondary_font, 8)
            canvas.setFillColor(template_mgr.branding.colors['text_secondary'])
            canvas.drawCentredString(
                doc.pagesize[0] / 2,
                footer_y,
                f"{template_mgr.translations['page']} {page_num}"
            )
            
            # Footer text
            footer_text = template_mgr.branding.get_footer_text()
            canvas.setFont(template_mgr.branding.secondary_font, 7)
            canvas.drawString(0.5*inch, footer_y - 0.5*inch, footer_text)
            
            # Footer line
            canvas.setStrokeColor(template_mgr.branding.colors['border'])
            canvas.setLineWidth(0.5)
            canvas.line(0.5*inch, footer_y + 0.25*inch,
                       doc.pagesize[0] - 0.5*inch, footer_y + 0.25*inch)
            
            # Restore canvas state
            canvas.restoreState()
        
        return header_footer
    
    def _compress_pdf(self, pdf_content: bytes) -> bytes:
        """Compress PDF content."""
        try:
            # Simple compression - in production use proper PDF optimization
            compressed = zlib.compress(pdf_content, level=9)
            
            # Only return compressed if it's actually smaller
            if len(compressed) < len(pdf_content):
                return compressed
            else:
                return pdf_content
        except Exception as e:
            logger.warning(f"Failed to compress PDF: {e}")
            return pdf_content
    
    @lru_cache(maxsize=100)
    def _get_cached_template(self, report_type: ReportType, language: Language):
        """Get cached template for performance."""
        cache_key = f"{report_type.value}_{language.value}"
        if cache_key not in self._template_cache:
            template_mgr = TemplateManager(self.branding, language)
            self._template_cache[cache_key] = template_mgr
        return self._template_cache[cache_key]
    
    def generate_batch_reports(self, reports: List[Dict[str, Any]]) -> Dict[str, bytes]:
        """Generate multiple reports in batch."""
        results = {}
        
        def process_report(report_config):
            try:
                report_type = ReportType(report_config['type'])
                data = report_config['data']
                language = Language(report_config.get('language', 'en'))
                output = self.generate_report(report_type, data, language)
                return report_config.get('id', str(hash(str(report_config)))), output
            except Exception as e:
                logger.error(f"Failed to generate batch report: {e}")
                return None, None
        
        # Process reports in parallel
        futures = []
        for report_config in reports:
            future = self.executor.submit(process_report, report_config)
            futures.append(future)
        
        # Collect results
        for future in futures:
            report_id, content = future.result()
            if report_id and content:
                results[report_id] = content
        
        return results

# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """Command line interface for PDF generation."""
    import argparse
    import sys
    import json
    
    parser = argparse.ArgumentParser(description='Generate professional PDF reports')
    parser.add_argument('--type', type=str, required=True,
                       choices=[rt.value for rt in ReportType],
                       help='Type of report to generate')
    parser.add_argument('--data', type=str, required=True,
                       help='JSON file containing report data')
    parser.add_argument('--output', type=str, required=True,
                       help='Output PDF file path')
    parser.add_argument('--language', type=str, default='en',
                       choices=[lang.value for lang in Language],
                       help='Report language')
    parser.add_argument('--format', type=str, default='pdf',
                       choices=[fmt.value for fmt in ExportFormat],
                       help='Export format')
    parser.add_argument('--config', type=str,
                       help='Configuration file path')
    parser.add_argument('--encrypt', action='store_true',
                       help='Encrypt PDF with password')
    parser.add_argument('--watermark', action='store_true',
                       help='Add confidential watermark')
    
    args = parser.parse_args()
    
    try:
        # Load data
        with open(args.data, 'r') as f:
            report_data = json.load(f)
        
        # Load config
        config = {}
        if args.config:
            with open(args.config, 'r') as f:
                config = json.load(f)
        
        # Update config with CLI args
        if args.encrypt:
            config['encrypt'] = True
        if args.watermark:
            config['add_watermark'] = True
        
        # Generate report
        generator = PDFGenerator(config)
        
        pdf_bytes = generator.generate_report(
            report_type=ReportType(args.type),
            data=report_data,
            language=Language(args.language),
            export_format=ExportFormat(args.format),
            output_path=args.output
        )
        
        if pdf_bytes:
            print(f"Report generated successfully: {args.output}")
            sys.exit(0)
        else:
            print(f"Failed to generate report")
            sys.exit(1)
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()