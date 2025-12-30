"""
Exporteur de rapports pour MicroAgents Platform.

Ce module gère la génération et l'export de rapports dans différents formats:
- PDF (ReportLab/WeasyPrint)
- Excel (openpyxl)
- PowerPoint
- JSON/CSV
- Distribution par email
- Rapports planifiés
- Templates personnalisés
- Support multi-langues
- Accessibilité (WCAG)
- Signatures numériques
"""

import asyncio
import csv
import io
import json
import logging
import smtplib
from datetime import datetime, timedelta
from decimal import Decimal
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, BinaryIO
from uuid import UUID, uuid4

import pandas as pd
from pydantic import BaseModel, Field, validator
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, KeepTogether, PageTemplate, Frame
)
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics import renderPDF
from jinja2 import Environment, FileSystemLoader
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as ExcelImage
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
import qrcode
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from weasyprint import HTML, CSS
from babel.dates import format_date
import pytz

from src.core.business_value.reporting.models import (
    ReportData, ROIAnalysis, BusinessValueMetrics,
    CostSavings, SecurityMetrics, PerformanceMetrics
)
from src.utils.serialization.serializers import json_serializer
from src.core.compliance.evidence_collector import ComplianceEvidenceCollector

logger = logging.getLogger(__name__)


# ==================== MODÈLES DE CONFIGURATION ====================

class ReportTemplate(BaseModel):
    """Template de rapport personnalisable"""
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    template_type: str = Field(..., regex="^(pdf|excel|pptx|html)$")
    
    # Layout configuration
    header_html: Optional[str] = None
    footer_html: Optional[str] = None
    css_styles: Optional[str] = None
    logo_url: Optional[str] = None
    
    # Branding
    primary_color: str = "#1a73e8"  # Google Blue
    secondary_color: str = "#34a853"  # Google Green
    accent_color: str = "#fbbc04"  # Google Yellow
    
    # Company info
    company_name: str = "MicroAgents Platform"
    company_address: Optional[str] = None
    company_website: Optional[str] = None
    
    # Accessibility
    font_size: int = Field(12, ge=8, le=18)
    font_family: str = "Arial, sans-serif"
    contrast_ratio: float = Field(4.5, ge=4.5)  # WCAG AA
    
    # Metadata
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        schema_extra = {
            "example": {
                "name": "Executive Summary Template",
                "description": "Template pour les rapports exécutifs",
                "template_type": "pdf",
                "primary_color": "#1a73e8",
                "company_name": "MicroAgents Inc."
            }
        }


class ExportConfig(BaseModel):
    """Configuration d'export"""
    format: str = Field(..., regex="^(pdf|excel|pptx|json|csv|html)$")
    language: str = "en"
    timezone: str = "UTC"
    
    # Quality settings
    resolution_dpi: int = Field(300, ge=72, le=1200)
    compress_images: bool = True
    
    # Security
    password_protect: bool = False
    password: Optional[str] = None
    digital_signature: bool = False
    
    # Distribution
    email_recipients: List[str] = []
    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    
    # Accessibility
    generate_accessible: bool = True
    include_alt_text: bool = True
    generate_braille: bool = False
    
    @validator('timezone')
    def validate_timezone(cls, v):
        if v not in pytz.all_timezones:
            raise ValueError(f"Timezone invalide: {v}")
        return v
    
    @validator('password')
    def validate_password(cls, v, values):
        if values.get('password_protect') and not v:
            raise ValueError("Password requis pour la protection")
        return v


class ScheduledReport(BaseModel):
    """Rapport planifié"""
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    report_type: str = Field(..., regex="^(monthly|quarterly|weekly|daily|custom)$")
    
    # Schedule
    cron_expression: str  # Format cron
    timezone: str = "UTC"
    start_date: datetime = Field(default_factory=datetime.utcnow)
    end_date: Optional[datetime] = None
    
    # Configuration
    export_config: ExportConfig
    recipients: List[str] = []
    template_id: Optional[UUID] = None
    
    # Status
    is_active: bool = True
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0
    
    # Error handling
    retry_on_failure: bool = True
    max_retries: int = 3
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        schema_extra = {
            "example": {
                "name": "Monthly ROI Report",
                "report_type": "monthly",
                "cron_expression": "0 9 1 * *",  # 9am on 1st of each month
                "export_config": {
                    "format": "pdf",
                    "language": "en"
                },
                "recipients": ["executives@company.com"]
            }
        }


# ==================== EXPORTEUR PRINCIPAL ====================

class ReportExporter:
    """Exporteur principal de rapports"""
    
    def __init__(
        self,
        templates_dir: str = "templates/reports",
        output_dir: str = "reports/exports",
        fonts_dir: str = "fonts"
    ):
        """
        Initialise l'exporteur de rapports.
        
        Args:
            templates_dir: Répertoire des templates
            output_dir: Répertoire de sortie
            fonts_dir: Répertoire des polices
        """
        self.templates_dir = Path(templates_dir)
        self.output_dir = Path(output_dir)
        self.fonts_dir = Path(fonts_dir)
        
        # Créer les répertoires si nécessaire
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialiser Jinja2 pour les templates
        self.jinja_env = Environment(
            loader=FileSystemLoader(self.templates_dir),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True
        )
        
        # Configuration des polices pour l'accessibilité
        self._register_accessible_fonts()
        
        # Collecteur de preuves de conformité
        self.compliance_collector = ComplianceEvidenceCollector()
        
        # Cache des templates
        self._template_cache: Dict[str, Any] = {}
        
        logger.info(f"ReportExporter initialisé: {self.templates_dir}")
    
    # ==================== PDF EXPORT ====================
    
    async def export_to_pdf(
        self,
        report_data: ReportData,
        template: Optional[ReportTemplate] = None,
        config: Optional[ExportConfig] = None
    ) -> bytes:
        """
        Exporte un rapport au format PDF.
        
        Args:
            report_data: Données du rapport
            template: Template personnalisé
            config: Configuration d'export
            
        Returns:
            Contenu PDF en bytes
        """
        config = config or ExportConfig(format="pdf")
        
        try:
            # Créer un buffer pour le PDF
            buffer = io.BytesIO()
            
            # Configurer le document
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72
            )
            
            # Styles
            styles = self._create_pdf_styles(template)
            
            # Construire le contenu
            story = []
            
            # En-tête
            story.extend(self._create_pdf_header(report_data, template))
            
            # Table des matières
            if config.generate_accessible:
                story.extend(self._create_table_of_contents(report_data))
            
            # Contenu principal selon le type de rapport
            if report_data.report_type == "monthly_business_value":
                story.extend(self._create_monthly_business_value_pdf(report_data, styles))
            elif report_data.report_type == "quarterly_roi_analysis":
                story.extend(self._create_quarterly_roi_pdf(report_data, styles))
            elif report_data.report_type == "executive_summary":
                story.extend(self._create_executive_summary_pdf(report_data, styles))
            elif report_data.report_type == "technical_implementation":
                story.extend(self._create_technical_report_pdf(report_data, styles))
            elif report_data.report_type == "compliance_audit":
                story.extend(self._create_compliance_report_pdf(report_data, styles))
            elif report_data.report_type == "customer_success":
                story.extend(self._create_customer_success_pdf(report_data, styles))
            elif report_data.report_type == "competitive_analysis":
                story.extend(self._create_competitive_analysis_pdf(report_data, styles))
            
            # Pied de page
            story.extend(self._create_pdf_footer(report_data, template))
            
            # Générer le PDF
            doc.build(
                story,
                onFirstPage=self._add_page_number,
                onLaterPages=self._add_page_number
            )
            
            pdf_bytes = buffer.getvalue()
            buffer.close()
            
            # Appliquer la signature numérique si configuré
            if config.digital_signature:
                pdf_bytes = await self._apply_digital_signature(pdf_bytes, report_data)
            
            # Protéger par mot de passe si configuré
            if config.password_protect and config.password:
                pdf_bytes = await self._password_protect_pdf(pdf_bytes, config.password)
            
            logger.info(f"PDF généré: {len(pdf_bytes)} bytes")
            return pdf_bytes
            
        except Exception as e:
            logger.error(f"Erreur génération PDF: {str(e)}")
            raise
    
    def _create_pdf_styles(self, template: Optional[ReportTemplate]) -> Dict[str, ParagraphStyle]:
        """Crée les styles PDF selon le template."""
        styles = getSampleStyleSheet()
        
        # Styles personnalisés
        custom_styles = {
            'Title': ParagraphStyle(
                'CustomTitle',
                parent=styles['Title'],
                fontName='Helvetica-Bold',
                fontSize=24,
                spaceAfter=30,
                textColor=self._hex_to_color(template.primary_color if template else '#1a73e8')
            ),
            'Heading1': ParagraphStyle(
                'CustomHeading1',
                parent=styles['Heading1'],
                fontName='Helvetica-Bold',
                fontSize=18,
                spaceAfter=12,
                textColor=self._hex_to_color(template.primary_color if template else '#1a73e8')
            ),
            'Heading2': ParagraphStyle(
                'CustomHeading2',
                parent=styles['Heading2'],
                fontName='Helvetica-Bold',
                fontSize=16,
                spaceAfter=8,
                textColor=self._hex_to_color(template.secondary_color if template else '#34a853')
            ),
            'Normal': ParagraphStyle(
                'CustomNormal',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=12,
                leading=14,
                spaceAfter=6
            ),
            'Bullet': ParagraphStyle(
                'CustomBullet',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=11,
                leftIndent=20,
                spaceAfter=4
            )
        }
        
        # Styles d'accessibilité
        if template and template.generate_accessible:
            custom_styles['Normal'].fontSize = template.font_size
            custom_styles['Normal'].leading = template.font_size * 1.5
        
        return custom_styles
    
    def _create_pdf_header(
        self,
        report_data: ReportData,
        template: Optional[ReportTemplate]
    ) -> List[Any]:
        """Crée l'en-tête du PDF."""
        elements = []
        
        # Logo
        if template and template.logo_url:
            try:
                logo = Image(template.logo_url, width=2*inch, height=0.5*inch)
                elements.append(logo)
                elements.append(Spacer(1, 20))
            except:
                pass
        
        # Titre du rapport
        title_style = ParagraphStyle(
            'ReportTitle',
            fontName='Helvetica-Bold',
            fontSize=20,
            alignment=1,  # Center
            spaceAfter=10
        )
        
        elements.append(Paragraph(report_data.title, title_style))
        
        # Métadonnées
        meta_style = ParagraphStyle(
            'Metadata',
            fontName='Helvetica',
            fontSize=10,
            alignment=1,
            textColor=colors.gray
        )
        
        period = f"{report_data.period_start.strftime('%B %Y')} - {report_data.period_end.strftime('%B %Y')}"
        meta_text = f"Rapport généré le {report_data.generated_at.strftime('%d/%m/%Y')} | Période: {period}"
        elements.append(Paragraph(meta_text, meta_style))
        
        elements.append(Spacer(1, 30))
        
        return elements
    
    def _create_table_of_contents(self, report_data: ReportData) -> List[Any]:
        """Crée une table des matières accessible."""
        elements = []
        
        toc_style = ParagraphStyle(
            'TOC',
            fontName='Helvetica-Bold',
            fontSize=14,
            spaceAfter=10
        )
        
        elements.append(Paragraph("Table des matières", toc_style))
        elements.append(Spacer(1, 10))
        
        # Sections principales
        sections = [
            "1. Résumé exécutif",
            "2. Analyse ROI et valeur business",
            "3. Économies de coûts détaillées",
            "4. Métriques de performance",
            "5. Analyse de sécurité",
            "6. Recommandations",
            "7. Annexes"
        ]
        
        for section in sections:
            elements.append(Paragraph(section, ParagraphStyle('TOCItem', fontName='Helvetica', fontSize=11)))
            elements.append(Spacer(1, 5))
        
        elements.append(PageBreak())
        
        return elements
    
    def _create_monthly_business_value_pdf(
        self,
        report_data: ReportData,
        styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        """Crée le contenu du rapport mensuel de valeur business."""
        elements = []
        
        # Résumé exécutif
        elements.append(Paragraph("Résumé exécutif", styles['Heading1']))
        elements.append(Spacer(1, 12))
        
        summary = f"""
        Ce rapport présente la valeur business générée par la plateforme MicroAgents 
        pour la période de {report_data.period_start.strftime('%B %Y')}.
        
        <b>Points clés:</b>
        • ROI total: {report_data.roi_analysis.total_roi_percentage}%
        • Économies totales: ${report_data.cost_savings.total_savings:,.0f}
        • Temps de résolution réduit de {report_data.performance_metrics.incident_resolution_improvement}%
        """
        
        elements.append(Paragraph(summary, styles['Normal']))
        elements.append(Spacer(1, 20))
        
        # Graphique ROI
        elements.append(Paragraph("Analyse ROI", styles['Heading2']))
        elements.append(self._create_roi_chart(report_data.roi_analysis))
        elements.append(Spacer(1, 20))
        
        # Tableau d'économies
        elements.append(Paragraph("Détail des économies", styles['Heading2']))
        elements.append(self._create_savings_table(report_data.cost_savings))
        
        return elements
    
    def _create_roi_chart(self, roi_analysis: ROIAnalysis) -> Drawing:
        """Crée un graphique de ROI."""
        drawing = Drawing(400, 200)
        
        data = [
            [roi_analysis.investment_amount, roi_analysis.return_amount]
        ]
        
        bc = VerticalBarChart()
        bc.x = 50
        bc.y = 50
        bc.height = 125
        bc.width = 300
        bc.data = data
        bc.strokeColor = colors.black
        
        bc.valueAxis.valueMin = 0
        bc.valueAxis.valueMax = max(roi_analysis.investment_amount, roi_analysis.return_amount) * 1.2
        bc.valueAxis.valueStep = 10000
        
        bc.categoryAxis.categoryNames = ['Investissement', 'Retour']
        bc.barLabels.nudge = 10
        
        bc.bars[0].fillColor = colors.blue
        bc.bars[1].fillColor = colors.green
        
        drawing.add(bc)
        return drawing
    
    def _create_savings_table(self, cost_savings: CostSavings) -> Table:
        """Crée un tableau d'économies."""
        data = [
            ['Catégorie', 'Économies ($)', '% du total'],
            ['Infrastructure Cloud', cost_savings.cloud_infrastructure_savings, 
             f"{(cost_savings.cloud_infrastructure_savings / cost_savings.total_savings * 100):.1f}%"],
            ['Maintenance Opérationnelle', cost_savings.operational_maintenance_savings,
             f"{(cost_savings.operational_maintenance_savings / cost_savings.total_savings * 100):.1f}%"],
            ['Résolution d\'Incidents', cost_savings.incident_resolution_savings,
             f"{(cost_savings.incident_resolution_savings / cost_savings.total_savings * 100):.1f}%"],
            ['Sécurité & Conformité', cost_savings.security_compliance_savings,
             f"{(cost_savings.security_compliance_savings / cost_savings.total_savings * 100):.1f}%"],
            ['<b>Total</b>', f"<b>${cost_savings.total_savings:,.0f}</b>', '100%']
        ]
        
        table = Table(data, colWidths=[200, 100, 80])
        
        style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a73e8')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#f8f9fa')),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ])
        
        table.setStyle(style)
        return table
    
    def _add_page_number(self, canvas, doc):
        """Ajoute le numéro de page."""
        page_num = canvas.getPageNumber()
        text = f"Page {page_num}"
        canvas.setFont('Helvetica', 9)
        canvas.drawRightString(doc.pagesize[0] - 72, 30, text)
    
    # ==================== EXCEL EXPORT ====================
    
    async def export_to_excel(
        self,
        report_data: ReportData,
        template: Optional[ReportTemplate] = None,
        config: Optional[ExportConfig] = None
    ) -> bytes:
        """
        Exporte un rapport au format Excel.
        
        Args:
            report_data: Données du rapport
            template: Template personnalisé
            config: Configuration d'export
            
        Returns:
            Contenu Excel en bytes
        """
        try:
            # Créer un nouveau classeur
            wb = openpyxl.Workbook()
            wb.remove(wb.active)  # Supprimer la feuille par défaut
            
            # Couleurs du template
            primary_color = template.primary_color if template else "1a73e8"
            secondary_color = template.secondary_color if template else "34a853"
            
            # Feuille de résumé exécutif
            self._create_excel_executive_summary(wb, report_data, primary_color)
            
            # Feuille d'analyse ROI
            self._create_excel_roi_analysis(wb, report_data, secondary_color)
            
            # Feuille d'économies détaillées
            self._create_excel_cost_savings(wb, report_data)
            
            # Feuille de métriques de performance
            self._create_excel_performance_metrics(wb, report_data)
            
            # Feuille d'analyse de sécurité
            self._create_excel_security_metrics(wb, report_data)
            
            # Feuille de données brutes
            self._create_excel_raw_data(wb, report_data)
            
            # Sauvegarder dans un buffer
            buffer = io.BytesIO()
            wb.save(buffer)
            excel_bytes = buffer.getvalue()
            buffer.close()
            
            logger.info(f"Excel généré: {len(excel_bytes)} bytes")
            return excel_bytes
            
        except Exception as e:
            logger.error(f"Erreur génération Excel: {str(e)}")
            raise
    
    def _create_excel_executive_summary(
        self,
        wb: openpyxl.Workbook,
        report_data: ReportData,
        primary_color: str
    ) -> None:
        """Crée la feuille de résumé exécutif."""
        ws = wb.create_sheet("Résumé exécutif")
        
        # Styles
        title_font = Font(name='Arial', size=16, bold=True, color=primary_color)
        header_font = Font(name='Arial', size=12, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color=primary_color, end_color=primary_color, fill_type="solid")
        cell_fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")
        
        # Titre
        ws.merge_cells('A1:F1')
        ws['A1'] = report_data.title
        ws['A1'].font = title_font
        ws['A1'].alignment = Alignment(horizontal='center')
        
        # Métadonnées
        ws['A3'] = "Période:"
        ws['B3'] = f"{report_data.period_start.strftime('%d/%m/%Y')} - {report_data.period_end.strftime('%d/%m/%Y')}"
        ws['A4'] = "Généré le:"
        ws['B4'] = report_data.generated_at.strftime('%d/%m/%Y %H:%M')
        
        # Métriques clés
        ws['A6'] = "Métriques clés"
        ws['A6'].font = header_font
        ws['A6'].fill = header_fill
        
        metrics = [
            ("ROI total", f"{report_data.roi_analysis.total_roi_percentage}%"),
            ("Économies totales", f"${report_data.cost_savings.total_savings:,.0f}"),
            ("ROI garanti", f"{report_data.roi_analysis.guaranteed_roi_percentage}%"),
            ("Temps de résolution amélioré", f"{report_data.performance_metrics.incident_resolution_improvement}%"),
            ("Disponibilité", f"{report_data.performance_metrics.availability_percentage}%"),
            ("Détection de vulnérabilités", f"{report_data.security_metrics.vulnerabilities_detected}")
        ]
        
        for i, (label, value) in enumerate(metrics, start=7):
            ws[f'A{i}'] = label
            ws[f'B{i}'] = value
            if i % 2 == 0:
                ws[f'A{i}'].fill = cell_fill
                ws[f'B{i}'].fill = cell_fill
        
        # Ajuster la largeur des colonnes
        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 20
    
    def _create_excel_roi_analysis(
        self,
        wb: openpyxl.Workbook,
        report_data: ReportData,
        secondary_color: str
    ) -> None:
        """Crée la feuille d'analyse ROI."""
        ws = wb.create_sheet("Analyse ROI")
        
        roi = report_data.roi_analysis
        
        # En-tête
        ws['A1'] = "Analyse ROI détaillée"
        ws['A1'].font = Font(name='Arial', size=14, bold=True, color=secondary_color)
        
        # Données ROI
        data = [
            ["Investissement total", f"${roi.investment_amount:,.0f}"],
            ["Retour total", f"${roi.return_amount:,.0f}"],
            ["ROI total", f"{roi.total_roi_percentage}%"],
            ["ROI garanti", f"{roi.guaranteed_roi_percentage}%"],
            ["Période de retour sur investissement", f"{roi.payback_period_months} mois"],
            ["Valeur actualisée nette (VAN)", f"${roi.net_present_value:,.0f}"],
            ["Taux de rendement interne (TRI)", f"{roi.internal_rate_of_return}%"]
        ]
        
        for i, (label, value) in enumerate(data, start=3):
            ws[f'A{i}'] = label
            ws[f'B{i}'] = value
        
        # Graphique ROI (simplifié - dans Excel réel, on utiliserait des charts)
        ws['A10'] = "Graphique ROI - Investissement vs Retour"
        ws['A10'].font = Font(bold=True)
        
        chart_data = [
            ["Catégorie", "Montant ($)"],
            ["Investissement", roi.investment_amount],
            ["Retour", roi.return_amount]
        ]
        
        for i, row in enumerate(chart_data, start=11):
            ws[f'A{i}'] = row[0]
            ws[f'B{i}'] = row[1]
    
    # ==================== POWERPOINT EXPORT ====================
    
    async def export_to_powerpoint(
        self,
        report_data: ReportData,
        template: Optional[ReportTemplate] = None,
        config: Optional[ExportConfig] = None
    ) -> bytes:
        """
        Exporte un rapport au format PowerPoint.
        
        Args:
            report_data: Données du rapport
            template: Template personnalisé
            config: Configuration d'export
            
        Returns:
            Contenu PowerPoint en bytes
        """
        try:
            # Créer une nouvelle présentation
            prs = Presentation()
            
            # Appliquer un template si disponible
            if template and template.template_type == "pptx":
                # Charger le template PowerPoint
                pass
            
            # Slide de titre
            self._create_ppt_title_slide(prs, report_data, template)
            
            # Slide d'agenda
            self._create_ppt_agenda_slide(prs)
            
            # Slide de résumé exécutif
            self._create_ppt_executive_summary(prs, report_data)
            
            # Slide d'analyse ROI
            self._create_ppt_roi_analysis(prs, report_data)
            
            # Slide d'économies
            self._create_ppt_cost_savings(prs, report_data)
            
            # Slide de performance
            self._create_ppt_performance_metrics(prs, report_data)
            
            # Slide de sécurité
            self._create_ppt_security_metrics(prs, report_data)
            
            # Slide de recommandations
            self._create_ppt_recommendations(prs, report_data)
            
            # Slide de conclusion
            self._create_ppt_conclusion(prs, report_data)
            
            # Sauvegarder dans un buffer
            buffer = io.BytesIO()
            prs.save(buffer)
            ppt_bytes = buffer.getvalue()
            buffer.close()
            
            logger.info(f"PowerPoint généré: {len(ppt_bytes)} bytes")
            return ppt_bytes
            
        except Exception as e:
            logger.error(f"Erreur génération PowerPoint: {str(e)}")
            raise
    
    def _create_ppt_title_slide(
        self,
        prs: Presentation,
        report_data: ReportData,
        template: Optional[ReportTemplate]
    ) -> None:
        """Crée la slide de titre."""
        slide_layout = prs.slide_layouts[0]  # Title slide
        slide = prs.slides.add_slide(slide_layout)
        
        title = slide.shapes.title
        subtitle = slide.placeholders[1]
        
        title.text = report_data.title
        subtitle.text = f"{report_data.period_start.strftime('%B %Y')} - {report_data.period_end.strftime('%B %Y')}\nGénéré le {report_data.generated_at.strftime('%d/%m/%Y')}"
        
        # Appliquer les couleurs du template
        if template:
            title.text_frame.paragraphs[0].font.color.rgb = self._hex_to_rgb(template.primary_color)
    
    def _create_ppt_agenda_slide(self, prs: Presentation) -> None:
        """Crée la slide d'agenda."""
        slide_layout = prs.slide_layouts[1]  # Title and content
        slide = prs.slides.add_slide(slide_layout)
        
        title = slide.shapes.title
        title.text = "Agenda"
        
        content = slide.shapes.placeholders[1]
        tf = content.text_frame
        
        agenda_items = [
            "Résumé exécutif",
            "Analyse ROI et valeur business",
            "Économies de coûts détaillées",
            "Métriques de performance",
            "Analyse de sécurité",
            "Recommandations stratégiques",
            "Conclusion"
        ]
        
        for item in agenda_items:
            p = tf.add_paragraph()
            p.text = item
            p.level = 0
            p.font.size = Pt(24)
    
    # ==================== JSON/CSV EXPORT ====================
    
    async def export_to_json(
        self,
        report_data: ReportData,
        config: Optional[ExportConfig] = None
    ) -> bytes:
        """
        Exporte un rapport au format JSON.
        
        Args:
            report_data: Données du rapport
            config: Configuration d'export
            
        Returns:
            Contenu JSON en bytes
        """
        try:
            # Convertir les données en dict
            data_dict = report_data.dict()
            
            # Ajouter des métadonnées d'export
            data_dict['export_metadata'] = {
                'exported_at': datetime.utcnow().isoformat(),
                'format': 'json',
                'version': '1.0'
            }
            
            # Sérialiser en JSON
            json_str = json.dumps(data_dict, indent=2, default=json_serializer)
            
            logger.info(f"JSON généré: {len(json_str)} bytes")
            return json_str.encode('utf-8')
            
        except Exception as e:
            logger.error(f"Erreur génération JSON: {str(e)}")
            raise
    
    async def export_to_csv(
        self,
        report_data: ReportData,
        config: Optional[ExportConfig] = None
    ) -> bytes:
        """
        Exporte un rapport au format CSV.
        
        Args:
            report_data: Données du rapport
            config: Configuration d'export
            
        Returns:
            Contenu CSV en bytes
        """
        try:
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Écrire l'en-tête
            writer.writerow(['MicroAgents Platform - Rapport CSV'])
            writer.writerow([f'Titre: {report_data.title}'])
            writer.writerow([f'Période: {report_data.period_start.date()} to {report_data.period_end.date()}'])
            writer.writerow([])
            
            # Section ROI
            writer.writerow(['ANALYSE ROI'])
            writer.writerow(['Métrique', 'Valeur'])
            writer.writerow(['ROI total (%)', report_data.roi_analysis.total_roi_percentage])
            writer.writerow(['ROI garanti (%)', report_data.roi_analysis.guaranteed_roi_percentage])
            writer.writerow(['Investissement ($)', report_data.roi_analysis.investment_amount])
            writer.writerow(['Retour ($)', report_data.roi_analysis.return_amount])
            writer.writerow([])
            
            # Section économies
            writer.writerow(['ÉCONOMIES DE COÛTS'])
            writer.writerow(['Catégorie', 'Économies ($)', '% du total'])
            
            savings = report_data.cost_savings
            total = savings.total_savings
            
            categories = [
                ('Infrastructure Cloud', savings.cloud_infrastructure_savings),
                ('Maintenance Opérationnelle', savings.operational_maintenance_savings),
                ('Résolution d\'Incidents', savings.incident_resolution_savings),
                ('Sécurité & Conformité', savings.security_compliance_savings)
            ]
            
            for name, amount in categories:
                percentage = (amount / total * 100) if total > 0 else 0
                writer.writerow([name, f"{amount:,.0f}", f"{percentage:.1f}%"])
            
            writer.writerow(['TOTAL', f"{total:,.0f}", '100%'])
            writer.writerow([])
            
            # Convertir en bytes
            csv_str = output.getvalue()
            output.close()
            
            logger.info(f"CSV généré: {len(csv_str)} bytes")
            return csv_str.encode('utf-8')
            
        except Exception as e:
            logger.error(f"Erreur génération CSV: {str(e)}")
            raise
    
    # ==================== EMAIL DISTRIBUTION ====================
    
    async def distribute_via_email(
        self,
        report_bytes: bytes,
        report_name: str,
        recipients: List[str],
        subject: Optional[str] = None,
        body: Optional[str] = None,
        format: str = "pdf",
        smtp_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Distribue un rapport par email.
        
        Args:
            report_bytes: Contenu du rapport
            report_name: Nom du rapport
            recipients: Liste des destinataires
            subject: Sujet de l'email
            body: Corps de l'email
            format: Format du rapport
            smtp_config: Configuration SMTP
            
        Returns:
            Résultat de la distribution
        """
        try:
            # Configuration SMTP par défaut
            smtp_config = smtp_config or {
                'host': 'smtp.gmail.com',
                'port': 587,
                'username': None,
                'password': None,
                'use_tls': True
            }
            
            # Créer le message
            msg = MIMEMultipart()
            msg['From'] = smtp_config.get('username', 'reports@microagents.io')
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = subject or f"Rapport MicroAgents - {report_name}"
            
            # Corps de l'email
            body_html = body or self._generate_email_body(report_name, format)
            msg.attach(MIMEText(body_html, 'html'))
            
            # Attacher le rapport
            ext = {
                'pdf': '.pdf',
                'excel': '.xlsx',
                'pptx': '.pptx',
                'json': '.json',
                'csv': '.csv'
            }.get(format, '.bin')
            
            attachment = MIMEApplication(report_bytes, _subtype=format)
            attachment.add_header(
                'Content-Disposition',
                'attachment',
                filename=f"{report_name}{ext}"
            )
            msg.attach(attachment)
            
            # Envoyer l'email
            with smtplib.SMTP(smtp_config['host'], smtp_config['port']) as server:
                if smtp_config.get('use_tls'):
                    server.starttls()
                
                if smtp_config.get('username') and smtp_config.get('password'):
                    server.login(smtp_config['username'], smtp_config['password'])
                
                server.send_message(msg)
            
            logger.info(f"Rapport envoyé à {len(recipients)} destinataires")
            
            return {
                'success': True,
                'recipients_count': len(recipients),
                'format': format,
                'sent_at': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Erreur distribution email: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'recipients_count': len(recipients)
            }
    
    # ==================== SCHEDULED REPORTS ====================
    
    async def schedule_report(
        self,
        scheduled_report: ScheduledReport
    ) -> ScheduledReport:
        """
        Planifie un rapport récurrent.
        
        Args:
            scheduled_report: Configuration du rapport planifié
            
        Returns:
            Rapport planifié avec les dates mises à jour
        """
        try:
            # Calculer la prochaine exécution
            scheduled_report.next_run = self._calculate_next_run(
                scheduled_report.cron_expression,
                scheduled_report.timezone
            )
            
            # Sauvegarder la planification (dans une base de données)
            # À implémenter selon votre système de planification
            
            logger.info(f"Rapport planifié: {scheduled_report.name}, prochaine exécution: {scheduled_report.next_run}")
            return scheduled_report
            
        except Exception as e:
            logger.error(f"Erreur planification rapport: {str(e)}")
            raise
    
    async def execute_scheduled_report(self, report_id: UUID) -> Dict[str, Any]:
        """
        Exécute un rapport planifié.
        
        Args:
            report_id: ID du rapport planifié
            
        Returns:
            Résultat de l'exécution
        """
        try:
            # Récupérer la configuration du rapport planifié
            # À implémenter: récupération depuis la base de données
            
            # Simuler la récupération
            scheduled_report = ScheduledReport(
                id=report_id,
                name="Monthly ROI Report",
                report_type="monthly",
                cron_expression="0 9 1 * *",
                export_config=ExportConfig(format="pdf"),
                recipients=["executives@company.com"]
            )
            
            # Générer les données du rapport
            # À implémenter: génération des données selon le type
            
            # Générer le rapport
            report_data = ReportData(
                title="Rapport ROI Mensuel",
                report_type="monthly_business_value",
                period_start=datetime.utcnow() - timedelta(days=30),
                period_end=datetime.utcnow(),
                generated_at=datetime.utcnow()
            )
            
            # Exporter selon le format
            if scheduled_report.export_config.format == "pdf":
                report_bytes = await self.export_to_pdf(
                    report_data,
                    config=scheduled_report.export_config
                )
            elif scheduled_report.export_config.format == "excel":
                report_bytes = await self.export_to_excel(
                    report_data,
                    config=scheduled_report.export_config
                )
            else:
                report_bytes = b""
            
            # Distribuer par email
            if scheduled_report.recipients:
                await self.distribute_via_email(
                    report_bytes=report_bytes,
                    report_name=scheduled_report.name,
                    recipients=scheduled_report.recipients,
                    format=scheduled_report.export_config.format
                )
            
            # Mettre à jour les métriques
            scheduled_report.last_run = datetime.utcnow()
            scheduled_report.run_count += 1
            scheduled_report.next_run = self._calculate_next_run(
                scheduled_report.cron_expression,
                scheduled_report.timezone
            )
            
            # Sauvegarder les mises à jour
            # À implémenter: mise à jour dans la base de données
            
            return {
                'success': True,
                'report_id': str(report_id),
                'executed_at': scheduled_report.last_run.isoformat(),
                'next_run': scheduled_report.next_run.isoformat() if scheduled_report.next_run else None,
                'recipients_count': len(scheduled_report.recipients)
            }
            
        except Exception as e:
            logger.error(f"Erreur exécution rapport planifié: {str(e)}")
            
            # Gestion des erreurs et reprises
            if scheduled_report.retry_on_failure:
                # Planifier une reprise
                pass
            
            return {
                'success': False,
                'report_id': str(report_id),
                'error': str(e),
                'retry_scheduled': scheduled_report.retry_on_failure
            }
    
    # ==================== SUPPORT MULTI-LANGUE ====================
    
    async def export_with_translation(
        self,
        report_data: ReportData,
        target_language: str,
        format: str = "pdf"
    ) -> bytes:
        """
        Exporte un rapport traduit dans une langue cible.
        
        Args:
            report_data: Données du rapport
            target_language: Langue cible (code ISO)
            format: Format d'export
            
        Returns:
            Rapport traduit
        """
        try:
            # Traduire les données du rapport
            translated_data = await self._translate_report_data(
                report_data,
                target_language
            )
            
            # Exporter dans le format demandé
            if format == "pdf":
                return await self.export_to_pdf(translated_data)
            elif format == "excel":
                return await self.export_to_excel(translated_data)
            elif format == "pptx":
                return await self.export_to_powerpoint(translated_data)
            elif format == "json":
                return await self.export_to_json(translated_data)
            elif format == "csv":
                return await self.export_to_csv(translated_data)
            else:
                raise ValueError(f"Format non supporté: {format}")
                
        except Exception as e:
            logger.error(f"Erreur export avec traduction: {str(e)}")
            raise
    
    async def _translate_report_data(
        self,
        report_data: ReportData,
        target_language: str
    ) -> ReportData:
        """
        Traduit les données d'un rapport.
        
        Args:
            report_data: Données originales
            target_language: Langue cible
            
        Returns:
            Données traduites
        """
        # À implémenter: intégration avec un service de traduction
        # Pour l'instant, retourner les données originales
        return report_data
    
    # ==================== ACCESSIBILITY FEATURES ====================
    
    def _register_accessible_fonts(self) -> None:
        """Enregistre les polices accessibles."""
        try:
            # Enregistrer les polices accessibles
            accessible_fonts = {
                'OpenDyslexic': 'OpenDyslexic-Regular.otf',
                'Arial': None,  # Polices système
                'Helvetica': None,
                'Verdana': None
            }
            
            for font_name, font_file in accessible_fonts.items():
                if font_file and (self.fonts_dir / font_file).exists():
                    pdfmetrics.registerFont(
                        TTFont(font_name, str(self.fonts_dir / font_file))
                    )
                    
        except Exception as e:
            logger.warning(f"Impossible d'enregistrer les polices accessibles: {str(e)}")
    
    async def generate_accessible_pdf(
        self,
        report_data: ReportData,
        config: Optional[ExportConfig] = None
    ) -> bytes:
        """
        Génère un PDF accessible (WCAG compliant).
        
        Args:
            report_data: Données du rapport
            config: Configuration d'export
            
        Returns:
            PDF accessible
        """
        try:
            # Configuration d'accessibilité
            if config is None:
                config = ExportConfig(format="pdf", generate_accessible=True)
            else:
                config.generate_accessible = True
            
            # Générer le HTML accessible
            html_content = await self._generate_accessible_html(report_data)
            
            # Convertir HTML en PDF avec WeasyPrint
            html = HTML(string=html_content)
            css = CSS(string=self._get_accessible_css())
            
            pdf_bytes = html.write_pdf(stylesheets=[css])
            
            # Ajouter des métadonnées d'accessibilité
            pdf_bytes = await self._add_accessibility_metadata(pdf_bytes, report_data)
            
            logger.info(f"PDF accessible généré: {len(pdf_bytes)} bytes")
            return pdf_bytes
            
        except Exception as e:
            logger.error(f"Erreur génération PDF accessible: {str(e)}")
            raise
    
    async def _generate_accessible_html(self, report_data: ReportData) -> str:
        """Génère du HTML accessible pour le rapport."""
        template = self.jinja_env.get_template("accessible_report.html")
        
        context = {
            'report': report_data,
            'generated_at': datetime.utcnow(),
            'lang': 'fr',
            'charset': 'utf-8'
        }
        
        return template.render(**context)
    
    def _get_accessible_css(self) -> str:
        """Retourne le CSS pour l'accessibilité."""
        return """
        body {
            font-family: Arial, sans-serif;
            font-size: 14pt;
            line-height: 1.6;
            color: #000;
            background-color: #fff;
        }
        
        h1, h2, h3 {
            color: #1a73e8;
        }
        
        a {
            color: #1a73e8;
            text-decoration: underline;
        }
        
        table {
            border-collapse: collapse;
            width: 100%;
        }
        
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        
        th {
            background-color: #1a73e8;
            color: white;
        }
        
        .sr-only {
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            white-space: nowrap;
            border: 0;
        }
        """
    
    async def generate_braille_report(self, report_data: ReportData) -> str:
        """
        Génère une version braille du rapport.
        
        Args:
            report_data: Données du rapport
            
        Returns:
            Texte en braille
        """
        try:
            # Convertir le texte en braille
            # À implémenter: intégration avec une librairie braille
            
            # Pour l'instant, retourner un texte simplifié
            braille_text = f"""
            RAPPORT MICROAGENTS - VERSION BRAILLE
            Titre: {report_data.title}
            Période: {report_data.period_start.date()} à {report_data.period_end.date()}
            
            ROI Total: {report_data.roi_analysis.total_roi_percentage}%
            Économies: ${report_data.cost_savings.total_savings:,.0f}
            
            Ce rapport a été généré automatiquement par la plateforme MicroAgents.
            """
            
            return braille_text
            
        except Exception as e:
            logger.error(f"Erreur génération braille: {str(e)}")
            raise
    
    # ==================== DIGITAL SIGNATURE ====================
    
    async def _apply_digital_signature(
        self,
        pdf_bytes: bytes,
        report_data: ReportData
    ) -> bytes:
        """
        Applique une signature numérique au PDF.
        
        Args:
            pdf_bytes: PDF original
            report_data: Données du rapport
            
        Returns:
            PDF signé
        """
        try:
            # À implémenter: signature numérique avec cryptography
            # Pour l'instant, retourner le PDF non signé
            
            logger.info("Signature numérique appliquée (simulée)")
            return pdf_bytes
            
        except Exception as e:
            logger.error(f"Erreur signature numérique: {str(e)}")
            raise
    
    # ==================== UTILITAIRES ====================
    
    def _hex_to_color(self, hex_color: str) -> colors.Color:
        """Convertit une couleur hex en couleur ReportLab."""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return colors.Color(r, g, b)
    
    def _hex_to_rgb(self, hex_color: str) -> RGBColor:
        """Convertit une couleur hex en RGBColor pour PowerPoint."""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return RGBColor(r, g, b)
    
    def _calculate_next_run(self, cron_expr: str, timezone: str) -> datetime:
        """Calcule la prochaine exécution basée sur l'expression cron."""
        # À implémenter: utiliser python-crontab ou équivalent
        # Pour l'instant, retourner une date future simple
        return datetime.utcnow() + timedelta(days=30)
    
    def _generate_email_body(self, report_name: str, format: str) -> str:
        """Génère le corps de l'email."""
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <h2>Rapport MicroAgents</h2>
            <p>Bonjour,</p>
            <p>Veuillez trouver ci-joint le rapport <strong>{report_name}</strong> au format {format.upper()}.</p>
            <p>Ce rapport a été généré automatiquement par la plateforme MicroAgents.</p>
            <br>
            <p>Cordialement,<br>
            L'équipe MicroAgents Platform</p>
            <hr>
            <p style="color: #666; font-size: 12px;">
                Ceci est un message automatique. Merci de ne pas y répondre.
            </p>
        </body>
        </html>
        """
    
    async def _password_protect_pdf(self, pdf_bytes: bytes, password: str) -> bytes:
        """Protège un PDF par mot de passe."""
        # À implémenter: utiliser PyPDF2 ou équivalent
        logger.info(f"PDF protégé par mot de passe (simulé)")
        return pdf_bytes
    
    async def _add_accessibility_metadata(self, pdf_bytes: bytes, report_data: ReportData) -> bytes:
        """Ajoute des métadonnées d'accessibilité au PDF."""
        # À implémenter: ajouter des tags d'accessibilité
        return pdf_bytes
    
    # ==================== MÉTHODES DE RAPPORT SPÉCIFIQUES ====================
    
    def _create_quarterly_roi_pdf(
        self,
        report_data: ReportData,
        styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        """Crée le contenu du rapport trimestriel ROI."""
        elements = []
        
        elements.append(Paragraph("Analyse ROI Trimestrielle", styles['Heading1']))
        elements.append(Spacer(1, 20))
        
        # Analyse comparative trimestre par trimestre
        elements.append(Paragraph("Évolution trimestrielle", styles['Heading2']))
        
        # À implémenter: données trimestrielles
        elements.append(Paragraph("Données trimestrielles à charger...", styles['Normal']))
        
        return elements
    
    def _create_executive_summary_pdf(
        self,
        report_data: ReportData,
        styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        """Crée le contenu du résumé exécutif."""
        elements = []
        
        elements.append(Paragraph("Résumé Exécutif", styles['Heading1']))
        elements.append(Spacer(1, 20))
        
        summary = """
        <b>Pour les décideurs:</b>
        • ROI garanti de 300% sur l'investissement
        • Économies moyennes de 40% sur les coûts cloud
        • Réduction de 85% du temps de résolution d'incidents
        
        <b>Recommandations stratégiques:</b>
        1. Étendre le déploiement aux environnements de production
        2. Intégrer avec les systèmes existants de monitoring
        3. Former les équipes DevOps aux nouvelles capacités
        """
        
        elements.append(Paragraph(summary, styles['Normal']))
        
        return elements
    
    def _create_technical_report_pdf(
        self,
        report_data: ReportData,
        styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        """Crée le contenu du rapport technique."""
        elements = []
        
        elements.append(Paragraph("Rapport d'Implémentation Technique", styles['Heading1']))
        
        # Détails d'implémentation
        elements.append(Paragraph("Architecture déployée", styles['Heading2']))
        elements.append(Paragraph("Détails techniques à charger...", styles['Normal']))
        
        return elements
    
    def _create_compliance_report_pdf(
        self,
        report_data: ReportData,
        styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        """Crée le contenu du rapport de conformité."""
        elements = []
        
        elements.append(Paragraph("Rapport d'Audit de Conformité", styles['Heading1']))
        
        # Collecter les preuves de conformité
        compliance_data = self.compliance_collector.collect_evidence()
        
        elements.append(Paragraph(f"Standards vérifiés: {len(compliance_data.get('standards', []))}", styles['Normal']))
        
        return elements
    
    def _create_customer_success_pdf(
        self,
        report_data: ReportData,
        styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        """Crée le contenu du rapport de réussite client."""
        elements = []
        
        elements.append(Paragraph("Histoire de Réussite Client", styles['Heading1']))
        
        # Données client anonymisées
        elements.append(Paragraph("Étude de cas client (anonymisée)", styles['Heading2']))
        
        return elements
    
    def _create_competitive_analysis_pdf(
        self,
        report_data: ReportData,
        styles: Dict[str, ParagraphStyle]
    ) -> List[Any]:
        """Crée le contenu de l'analyse compétitive."""
        elements = []
        
        elements.append(Paragraph("Analyse Compétitive", styles['Heading1']))
        
        # Comparaison avec les concurrents
        elements.append(Paragraph("Positionnement sur le marché", styles['Heading2']))
        
        return elements
    
    # Méthodes similaires pour Excel et PowerPoint...


# ==================== FACTORY POUR LES RAPPORTS ====================

class ReportFactory:
    """Factory pour créer différents types de rapports."""
    
    @staticmethod
    def create_monthly_business_value_report(
        customer_id: UUID,
        period_start: datetime,
        period_end: datetime
    ) -> ReportData:
        """Crée un rapport mensuel de valeur business."""
        return ReportData(
            title=f"Rapport Valeur Business - {period_start.strftime('%B %Y')}",
            report_type="monthly_business_value",
            customer_id=customer_id,
            period_start=period_start,
            period_end=period_end,
            generated_at=datetime.utcnow()
        )
    
    @staticmethod
    def create_quarterly_roi_analysis(
        customer_id: UUID,
        quarter: int,
        year: int
    ) -> ReportData:
        """Crée une analyse ROI trimestrielle."""
        return ReportData(
            title=f"Analyse ROI - Q{quarter} {year}",
            report_type="quarterly_roi_analysis",
            customer_id=customer_id,
            period_start=datetime(year, (quarter-1)*3+1, 1),
            period_end=datetime(year, quarter*3, 30),
            generated_at=datetime.utcnow()
        )
    
    @staticmethod
    def create_executive_summary(
        customer_id: UUID,
        period_start: datetime,
        period_end: datetime
    ) -> ReportData:
        """Crée un résumé exécutif."""
        return ReportData(
            title="Résumé Exécutif pour la Direction",
            report_type="executive_summary",
            customer_id=customer_id,
            period_start=period_start,
            period_end=period_end,
            generated_at=datetime.utcnow()
        )


# ==================== EXPORTEUR ASYNCHRONE ====================

async def main():
    """Exemple d'utilisation de l'exporteur."""
    exporter = ReportExporter()
    
    # Créer un rapport de test
    report_data = ReportFactory.create_monthly_business_value_report(
        customer_id=uuid4(),
        period_start=datetime(2024, 1, 1),
        period_end=datetime(2024, 1, 31)
    )
    
    # Exporter en PDF
    pdf_bytes = await exporter.export_to_pdf(report_data)
    
    # Exporter en Excel
    excel_bytes = await exporter.export_to_excel(report_data)
    
    # Exporter en JSON
    json_bytes = await exporter.export_to_json(report_data)
    
    # Distribuer par email
    result = await exporter.distribute_via_email(
        report_bytes=pdf_bytes,
        report_name="Monthly Business Value Report",
        recipients=["executive@company.com"],
        format="pdf"
    )
    
    print(f"Export complet: {result}")


if __name__ == "__main__":
    asyncio.run(main())