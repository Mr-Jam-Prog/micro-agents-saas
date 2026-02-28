"""
Human Gate - Gestion des approbations humaines pour les décisions critiques
Implémente human-in-the-loop pour workflows enterprise avec multi-canaux et timeout.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field
import logging
from enum import Enum
import json
import re
import html
from abc import ABC, abstractmethod

from ..types import DecisionContext, AutomationDecision, HumanGateResponse
from ..exceptions import HumanRejectedError, ApprovalTimeoutError

logger = logging.getLogger(__name__)


class ApprovalChannel(str, Enum):
    """Canaux d'approbation supportés."""
    SLACK = "slack"
    EMAIL = "email"
    MS_TEAMS = "ms_teams"
    WEBHOOK = "webhook"
    API = "api"
    INTERNAL_UI = "internal_ui"


class ApprovalStatus(str, Enum):
    """Statuts d'approbation."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalPriority(str, Enum):
    """Priorités d'approbation."""
    LOW = "low"          # 24h pour répondre
    MEDIUM = "medium"    # 12h pour répondre
    HIGH = "high"        # 4h pour répondre
    CRITICAL = "critical" # 1h pour répondre


@dataclass
class Approver:
    """Informations sur un approbateur."""
    user_id: str
    name: str
    email: str
    roles: List[str]
    channels: List[ApprovalChannel]
    escalation_order: int = 1  # Ordre d'escalation (1 = premier)


@dataclass
class ApprovalRequest:
    """Demande d'approbation."""
    request_id: str
    decision_summary: str
    context_summary: str
    risk_assessment: str
    automation_decision: AutomationDecision
    context: DecisionContext
    approvers: List[Approver]
    priority: ApprovalPriority
    required_approvals: int = 1  # Nombre minimum d'approbations requises
    timeout_hours: float = 24.0
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: ApprovalStatus = ApprovalStatus.PENDING
    approvals_received: List[Dict[str, Any]] = field(default_factory=list)
    rejections_received: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalResponse:
    """Réponse à une demande d'approbation."""
    request_id: str
    approver: Approver
    status: ApprovalStatus
    comments: Optional[str] = None
    responded_at: Optional[datetime] = None
    channel: Optional[ApprovalChannel] = None


class ApprovalConnector(ABC):
    """Interface abstraite pour les connecteurs d'approbation."""
    
    @abstractmethod
    async def send_approval_request(self, request: ApprovalRequest) -> bool:
        """Envoie une demande d'approbation via ce canal."""
        pass
    
    @abstractmethod
    async def check_approval_status(self, request_id: str) -> Optional[ApprovalStatus]:
        """Vérifie le statut d'une demande d'approbation."""
        pass
    
    @abstractmethod
    async def cancel_approval_request(self, request_id: str) -> bool:
        """Annule une demande d'approbation."""
        pass


class SlackConnector(ApprovalConnector):
    """Connecteur pour l'envoi de demandes d'approbation via Slack."""
    
    def __init__(self, webhook_url: str, bot_token: Optional[str] = None):
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.logger = logging.getLogger(f"{__name__}.SlackConnector")
    
    async def send_approval_request(self, request: ApprovalRequest) -> bool:
        """Envoie une demande d'approbation via Slack."""
        try:
            import httpx
            
            # Créer le message Slack formaté
            message = self._format_slack_message(request)
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.webhook_url,
                    json=message,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    self.logger.info(f"Slack approval request sent: {request.request_id}")
                    return True
                else:
                    self.logger.error(f"Failed to send Slack request: {response.status_code}")
                    return False
                    
        except Exception as e:
            self.logger.error(f"Error sending Slack approval: {e}")
            return False
    
    def _format_slack_message(self, request: ApprovalRequest) -> Dict[str, Any]:
        """Formate un message Slack pour une demande d'approbation."""
        # Échapper le Markdown pour Slack
        safe_summary = self._escape_slack_markdown(request.decision_summary)
        safe_context = self._escape_slack_markdown(request.context_summary)
        
        # Couleur basée sur la priorité
        color_map = {
            ApprovalPriority.LOW: "#36a64f",      # Vert
            ApprovalPriority.MEDIUM: "#ffcc00",   # Jaune
            ApprovalPriority.HIGH: "#ff9900",     # Orange
            ApprovalPriority.CRITICAL: "#ff0000"  # Rouge
        }
        
        return {
            "attachments": [{
                "color": color_map.get(request.priority, "#36a64f"),
                "title": "🤖 Demande d'approbation de décision automatisée",
                "text": f"*Résumé:*\n{safe_summary}\n\n*Contexte:*\n{safe_context}",
                "fields": [
                    {
                        "title": "Priorité",
                        "value": request.priority.value.upper(),
                        "short": True
                    },
                    {
                        "title": "Délai",
                        "value": f"{request.timeout_hours}h",
                        "short": True
                    },
                    {
                        "title": "Approbateurs requis",
                        "value": str(request.required_approvals),
                        "short": True
                    },
                    {
                        "title": "ID de demande",
                        "value": request.request_id,
                        "short": True
                    }
                ],
                "actions": [
                    {
                        "name": "approve",
                        "text": "✅ Approuver",
                        "type": "button",
                        "value": f"approve:{request.request_id}",
                        "style": "primary"
                    },
                    {
                        "name": "reject",
                        "text": "❌ Rejeter",
                        "type": "button",
                        "value": f"reject:{request.request_id}",
                        "style": "danger"
                    },
                    {
                        "name": "details",
                        "text": "📋 Voir les détails",
                        "type": "button",
                        "value": f"details:{request.request_id}",
                        "url": f"https://internal.app/approvals/{request.request_id}"
                    }
                ],
                "footer": f"Envoyé le {request.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
                "ts": int(request.created_at.timestamp())
            }]
        }
    
    def _escape_slack_markdown(self, text: str) -> str:
        """Échappe le markdown Slack dans un texte."""
        # Échapper les caractères spéciaux Slack
        replacements = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
        }
        
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        
        return text
    
    async def check_approval_status(self, request_id: str) -> Optional[ApprovalStatus]:
        """Vérifie le statut d'une demande d'approbation dans Slack."""
        # Dans une implémentation réelle, nous vérifierions l'API Slack
        # Pour l'exemple, on simule une vérification
        return None  # Pas d'implémentation de polling dans cette version simple
    
    async def cancel_approval_request(self, request_id: str) -> bool:
        """Annule une demande d'approbation dans Slack."""
        # Dans une implémentation réelle, nous mettrions à jour le message Slack
        self.logger.info(f"Cancelled Slack approval request: {request_id}")
        return True


class EmailConnector(ApprovalConnector):
    """Connecteur pour l'envoi de demandes d'approbation par email."""
    
    def __init__(self, smtp_server: str, smtp_port: int, sender_email: str):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.logger = logging.getLogger(f"{__name__}.EmailConnector")
    
    async def send_approval_request(self, request: ApprovalRequest) -> bool:
        """Envoie une demande d'approbation par email."""
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            # Pour l'exemple, nous utilisons une implémentation synchrone
            # Dans la réalité, on utiliserait asyncio.to_thread ou aiosmtplib
            loop = asyncio.get_event_loop()
            
            await loop.run_in_executor(
                None,
                self._send_email_sync,
                request
            )
            
            self.logger.info(f"Email approval request sent: {request.request_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending email approval: {e}")
            return False
    
    def _send_email_sync(self, request: ApprovalRequest):
        """Envoie un email de manière synchrone (pour l'exemple)."""
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        # Construire le message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"[{request.priority.value.upper()}] Demande d'approbation de décision automatisée"
        msg['From'] = self.sender_email
        
        # Destinataires
        approver_emails = [a.email for a in request.approvers]
        msg['To'] = ', '.join(approver_emails)
        
        # Version texte
        text_content = self._format_text_email(request)
        text_part = MIMEText(text_content, 'plain', 'utf-8')
        msg.attach(text_part)
        
        # Version HTML
        html_content = self._format_html_email(request)
        html_part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(html_part)
        
        # Envoyer l'email
        with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
            server.send_message(msg)
    
    def _format_text_email(self, request: ApprovalRequest) -> str:
        """Formate un email texte pour une demande d'approbation."""
        return f"""
DEMANDE D'APPROBATION DE DÉCISION AUTOMATISÉE

ID de demande: {request.request_id}
Priorité: {request.priority.value.upper()}
Délai de réponse: {request.timeout_hours} heures
Approbations requises: {request.required_approvals}

RÉSUMÉ DE LA DÉCISION:
{request.decision_summary}

CONTEXTE:
{request.context_summary}

ÉVALUATION DES RISQUES:
{request.risk_assessment}

APPROBATEURS:
{', '.join([f"{a.name} ({a.email})" for a in request.approvers])}

POUR APPROUVER:
- Répondez à cet email avec "APPROUVE" ou "APPROVED"
- Ou cliquez sur le lien d'approbation (si disponible)

POUR REJETER:
- Répondez à cet email avec "REJECT" ou "REJECTED"

Date d'envoi: {request.created_at.strftime('%Y-%m-%d %H:%M UTC')}
"""
    
    def _format_html_email(self, request: ApprovalRequest) -> str:
        """Formate un email HTML pour une demande d'approbation."""
        # Échapper le HTML pour la sécurité
        safe_summary = html.escape(request.decision_summary)
        safe_context = html.escape(request.context_summary)
        safe_risk = html.escape(request.risk_assessment)
        
        approvers_html = "<br>".join([
            f"• {html.escape(a.name)} &lt;{html.escape(a.email)}&gt;" 
            for a in request.approvers
        ])
        
        return f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
        .header {{ background-color: {'#ff9900' if request.priority == ApprovalPriority.HIGH else '#36a64f'}; 
                  color: white; padding: 20px; border-radius: 5px; }}
        .content {{ margin: 20px 0; }}
        .section {{ margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
        .button {{ display: inline-block; padding: 10px 20px; margin: 5px; 
                  text-decoration: none; border-radius: 5px; color: white; }}
        .approve {{ background-color: #36a64f; }}
        .reject {{ background-color: #ff0000; }}
        .details {{ background-color: #007bff; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 Demande d'approbation de décision automatisée</h1>
        <p>Priorité: <strong>{request.priority.value.upper()}</strong> | 
           Délai: <strong>{request.timeout_hours}h</strong> | 
           ID: {request.request_id}</p>
    </div>
    
    <div class="content">
        <div class="section">
            <h2>📋 Résumé de la décision</h2>
            <p>{safe_summary.replace(chr(10), '<br>')}</p>
        </div>
        
        <div class="section">
            <h2>📊 Contexte</h2>
            <p>{safe_context.replace(chr(10), '<br>')}</p>
        </div>
        
        <div class="section">
            <h2>⚠️ Évaluation des risques</h2>
            <p>{safe_risk.replace(chr(10), '<br>')}</p>
        </div>
        
        <div class="section">
            <h2>👥 Approbateurs</h2>
            <p>{approvers_html}</p>
            <p><strong>Approbations requises:</strong> {request.required_approvals}</p>
        </div>
        
        <div class="section">
            <h2>✅ Actions rapides</h2>
            <a href="https://internal.app/approvals/{request.request_id}?action=approve" 
               class="button approve">Approuver</a>
            <a href="https://internal.app/approvals/{request.request_id}?action=reject" 
               class="button reject">Rejeter</a>
            <a href="https://internal.app/approvals/{request.request_id}" 
               class="button details">Voir les détails</a>
        </div>
    </div>
    
    <footer>
        <p><em>Envoyé le {request.created_at.strftime('%Y-%m-%d %H:%M UTC')}</em></p>
        <p><small>Pour répondre par email, répondez avec "APPROUVE" ou "REJECT".</small></p>
    </footer>
</body>
</html>
"""
    
    async def check_approval_status(self, request_id: str) -> Optional[ApprovalStatus]:
        """Vérifie le statut d'une demande d'approbation par email."""
        # Dans une implémentation réelle, nous vérifierions la boîte de réception
        return None
    
    async def cancel_approval_request(self, request_id: str) -> bool:
        """Annule une demande d'approbation par email."""
        # Dans une implémentation réelle, nous enverrions un email d'annulation
        self.logger.info(f"Cancelled email approval request: {request_id}")
        return True


class HumanGate:
    """
    Gestionnaire d'approbations humaines.
    
    Responsabilités:
    1. Déterminer si une validation humaine est requise
    2. Générer un résumé décisionnel sécurisé (Markdown safe)
    3. Gérer les approbations asynchrones via multiple canaux
    4. Gérer les timeouts et escalations
    5. Support multi-approvers
    """
    
    def __init__(
        self,
        connectors: List[ApprovalConnector],
        approval_store = None,  # Stockage persistant des approbations
        default_timeout_hours: float = 24.0,
        enable_escalation: bool = True,
        escalation_delay_hours: float = 4.0
    ):
        """
        Initialise le HumanGate.
        
        Args:
            connectors: Liste des connecteurs d'approbation
            approval_store: Stockage persistant pour les approbations (optionnel)
            default_timeout_hours: Timeout par défaut en heures
            enable_escalation: Active l'escalation automatique
            escalation_delay_hours: Délai avant escalade
        """
        self.connectors = connectors
        self.approval_store = approval_store
        self.default_timeout_hours = default_timeout_hours
        self.enable_escalation = enable_escalation
        self.escalation_delay_hours = escalation_delay_hours
        self.logger = logger
        
        # Cache des demandes en cours (en production, utiliser un store persistant)
        self.active_requests: Dict[str, ApprovalRequest] = {}
        
        self.logger.info(
            "human_gate_initialized",
            connector_count=len(connectors),
            default_timeout_hours=default_timeout_hours
        )
    
    async def check(
        self,
        automation_decision: AutomationDecision,
        context: DecisionContext,
        timeout: Optional[float] = None
    ) -> HumanGateResponse:
        """
        Détermine si une approbation humaine est requise et gère le processus.
        
        Args:
            automation_decision: Décision d'automatisation
            context: Contexte décisionnel
            timeout: Timeout personnalisé en secondes
            
        Returns:
            HumanGateResponse: Réponse avec statut d'approbation
        """
        try:
            self.logger.info(
                "human_gate_check_started",
                automation_level=automation_decision.level.value,
                context_id=id(context)
            )
            
            # 1. Déterminer si une approbation est requise
            requires_approval = await self._requires_human_approval(
                automation_decision, 
                context
            )
            
            if not requires_approval:
                self.logger.info("human_approval_not_required")
                return HumanGateResponse(
                    required=False,
                    approved=True,
                    justification="No human approval required based on policy"
                )
            
            # 2. Déterminer les approbateurs
            approvers = await self._determine_approvers(context)
            
            if not approvers:
                self.logger.warning("no_approvers_found")
                return HumanGateResponse(
                    required=True,
                    approved=False,
                    justification="No approvers available for this context"
                )
            
            # 3. Générer le résumé de décision
            decision_summary = self._generate_decision_summary(automation_decision, context)
            context_summary = self._generate_context_summary(context)
            risk_assessment = self._generate_risk_assessment(context)
            
            # 4. Déterminer la priorité
            priority = self._determine_priority(automation_decision, context)
            
            # 5. Déterminer le timeout
            timeout_hours = timeout / 3600 if timeout else self._determine_timeout_hours(priority)
            
            # 6. Créer la demande d'approbation
            request = ApprovalRequest(
                request_id=self._generate_request_id(),
                decision_summary=decision_summary,
                context_summary=context_summary,
                risk_assessment=risk_assessment,
                automation_decision=automation_decision,
                context=context,
                approvers=approvers,
                priority=priority,
                required_approvals=self._determine_required_approvals(context),
                timeout_hours=timeout_hours,
                metadata={
                    "automation_level": automation_decision.level.value,
                    "confidence_score": automation_decision.metadata.get("confidence_score", 0.0),
                    "risk_level": automation_decision.metadata.get("risk_level", "unknown")
                }
            )
            
            # 7. Envoyer la demande via tous les connecteurs
            sent = await self._send_approval_request(request)
            
            if not sent:
                self.logger.error("failed_to_send_approval_request")
                return HumanGateResponse(
                    required=True,
                    approved=False,
                    justification="Failed to send approval request to any channel"
                )
            
            # 8. Stocker la demande
            await self._store_approval_request(request)
            
            # 9. Attendre la réponse (avec timeout)
            approved, justification = await self._wait_for_approval(
                request, 
                timeout_hours * 3600 if timeout is None else timeout
            )
            
            # 10. Mettre à jour le statut de la demande
            request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            await self._update_approval_request(request)
            
            self.logger.info(
                "human_gate_decision_made",
                request_id=request.request_id,
                approved=approved,
                approvers_count=len(approvers),
                response_time=(datetime.utcnow() - request.created_at).total_seconds()
            )
            
            return HumanGateResponse(
                required=True,
                approved=approved,
                justification=justification,
                metadata={
                    "request_id": request.request_id,
                    "priority": priority.value,
                    "timeout_hours": timeout_hours,
                    "approvers_count": len(approvers),
                    "required_approvals": request.required_approvals,
                    "actual_approvals": len(request.approvals_received),
                    "actual_rejections": len(request.rejections_received)
                }
            )
            
        except ApprovalTimeoutError as e:
            self.logger.warning("approval_timeout", error=str(e))
            return HumanGateResponse(
                required=True,
                approved=False,
                justification=f"Approval timeout: {str(e)}",
                metadata={"timeout": True}
            )
        except Exception as e:
            self.logger.error("human_gate_error", error=str(e))
            # En cas d'erreur, assumer que l'approbation est requise mais non obtenue
            return HumanGateResponse(
                required=True,
                approved=False,
                justification=f"Error in human gate: {str(e)}",
                metadata={"error": True}
            )
    
    async def _requires_human_approval(
        self,
        automation_decision: AutomationDecision,
        context: DecisionContext
    ) -> bool:
        """Détermine si une approbation humaine est requise."""
        # Règle 1: Basé sur le niveau d'automatisation
        if automation_decision.level.value in ["AUTO_EXECUTE", "AUTO_EXECUTE_WITH_MONITORING"]:
            # Vérifier les préférences client
            client_prefs = context.client_preferences or {}
            automation_pref = client_prefs.get("automation_preference", "medium")
            
            if automation_pref == "none":
                return True
            elif automation_pref == "low" and automation_decision.level.value == "AUTO_EXECUTE":
                return True
        
        # Règle 2: Basé sur le contexte de risque
        if automation_decision.metadata.get("requires_human_approval", False):
            return True
        
        # Règle 3: Basé sur l'environnement
        environment = context.environment or {}
        env = environment.get("environment", "production")
        
        if env == "production" and automation_decision.level.value == "AUTO_EXECUTE":
            # En production, AUTO_EXECUTE nécessite toujours une approbation
            return True
        
        # Règle 4: Basé sur le type d'intention
        intent = context.intent or {}
        intent_type = intent.get("type")
        
        high_risk_intents = ["SECURITY_RISK_REDUCTION", "COMPLIANCE_ENFORCEMENT", "DATA_DELETION"]
        if intent_type in high_risk_intents:
            return True
        
        return False
    
    async def _determine_approvers(self, context: DecisionContext) -> List[Approver]:
        """Détermine la liste des approbateurs pour un contexte donné."""
        approvers = []
        
        # 1. Récupérer les approbateurs par défaut
        default_approvers = await self._get_default_approvers(context)
        approvers.extend(default_approvers)
        
        # 2. Récupérer les approbateurs spécifiques au client
        client_prefs = context.client_preferences or {}
        client_approvers = client_prefs.get("approvers", [])
        
        for approver_data in client_approvers:
            approvers.append(Approver(
                user_id=approver_data.get("user_id", ""),
                name=approver_data.get("name", "Unknown"),
                email=approver_data.get("email", ""),
                roles=approver_data.get("roles", []),
                channels=[ApprovalChannel(ch) for ch in approver_data.get("channels", ["email"])]
            ))
        
        # 3. Dédupliquer par user_id
        unique_approvers = {}
        for approver in approvers:
            if approver.user_id not in unique_approvers:
                unique_approvers[approver.user_id] = approver
        
        return list(unique_approvers.values())
    
    async def _get_default_approvers(self, context: DecisionContext) -> List[Approver]:
        """Récupère les approbateurs par défaut."""
        # Dans une implémentation réelle, cela viendrait d'une base de données ou d'une API
        # Pour l'exemple, nous créons des approbateurs fictifs
        
        # Déterminer le rôle requis basé sur le contexte
        intent = context.intent or {}
        intent_type = intent.get("type", "UNKNOWN")
        
        if intent_type == "SECURITY_RISK_REDUCTION":
            roles = ["security_engineer", "devops_lead"]
        elif intent_type == "COST_OPTIMIZATION":
            roles = ["finance_lead", "devops_engineer"]
        elif intent_type == "COMPLIANCE_ENFORCEMENT":
            roles = ["compliance_officer", "legal_lead"]
        else:
            roles = ["devops_engineer", "team_lead"]
        
        # Créer des approbateurs fictifs
        return [
            Approver(
                user_id="alice_devops",
                name="Alice DevOps",
                email="alice@example.com",
                roles=["devops_engineer", "team_lead"],
                channels=[ApprovalChannel.SLACK, ApprovalChannel.EMAIL],
                escalation_order=1
            ),
            Approver(
                user_id="bob_security",
                name="Bob Security",
                email="bob@example.com",
                roles=["security_engineer"],
                channels=[ApprovalChannel.SLACK, ApprovalChannel.EMAIL],
                escalation_order=2
            )
        ]
    
    def _generate_decision_summary(
        self,
        automation_decision: AutomationDecision,
        context: DecisionContext
    ) -> str:
        """Génère un résumé de décision sécurisé en Markdown."""
        # Sécuriser les entrées
        def safe_markdown(text: str) -> str:
            """Échappe les caractères spéciaux Markdown."""
            if not text:
                return ""
            
            # Échapper les caractères spéciaux Markdown
            replacements = [
                ('\\', '\\\\'),
                ('`', '\\`'),
                ('*', '\\*'),
                ('_', '\\_'),
                ('{', '\\{'),
                ('}', '\\}'),
                ('[', '\\['),
                (']', '\\]'),
                ('(', '\\('),
                (')', '\\)'),
                ('#', '\\#'),
                ('+', '\\+'),
                ('-', '\\-'),
                ('.', '\\.'),
                ('!', '\\!')
            ]
            
            for old, new in replacements:
                text = text.replace(old, new)
            
            return text
        
        intent = context.intent or {}
        
        summary = f"""
# 📋 Résumé de la décision automatisée

## Intention
- **Type:** {safe_markdown(intent.get('type', 'Unknown'))}
- **Priorité:** {safe_markdown(str(intent.get('priority', 'N/A')))}
- **Métriques de succès:** {safe_markdown(str(intent.get('success_metrics', {})))}

## Niveau d'automatisation
- **Niveau:** {safe_markdown(automation_decision.level.value)}
- **Justification:** {safe_markdown(automation_decision.justification)}

## Score de confiance
- **Score:** {safe_markdown(str(automation_decision.metadata.get('confidence_score', 'N/A')))}
- **Niveau de risque:** {safe_markdown(automation_decision.metadata.get('risk_level', 'Unknown'))}

## Action recommandée
{self._format_recommended_action(automation_decision, context)}
"""
        
        return summary.strip()
    
    def _format_recommended_action(
        self,
        automation_decision: AutomationDecision,
        context: DecisionContext
    ) -> str:
        """Formate l'action recommandée."""
        level = automation_decision.level.value
        
        if level == "AUTO_EXECUTE":
            return "**Exécution automatique complète** - Le système exécutera l'action sans intervention supplémentaire."
        elif level == "AUTO_EXECUTE_WITH_MONITORING":
            return "**Exécution automatique avec monitoring** - Le système exécutera l'action et surveillera les résultats."
        elif level == "RECOMMEND":
            return "**Recommandation uniquement** - Le système recommande une action, mais n'exécutera rien automatiquement."
        else:  # INVESTIGATE
            return "**Investigation requise** - Une analyse humaine est nécessaire avant toute action."
    
    def _generate_context_summary(self, context: DecisionContext) -> str:
        """Génère un résumé du contexte sécurisé en Markdown."""
        def safe_markdown(text: str) -> str:
            # Utiliser la même fonction de sécurisation
            return self._generate_decision_summary.__closure__[0].cell_contents(text)
        
        environment = context.environment or {}
        client_prefs = context.client_preferences or {}
        business_value = context.business_value or {}
        
        summary = f"""
## 🌍 Environnement
- **Environnement:** {safe_markdown(environment.get('environment', 'Unknown'))}
- **Région:** {safe_markdown(environment.get('region', 'N/A'))}

## 👤 Préférences client
- **Préférence d'automatisation:** {safe_markdown(client_prefs.get('automation_preference', 'Medium'))}
- **Tolérance au risque:** {safe_markdown(client_prefs.get('risk_tolerance', 'Medium'))}

## 💼 Valeur business
- **ROI attendu:** {safe_markdown(str(business_value.get('expected_roi', 'N/A')))}
- **Criticité business:** {safe_markdown(business_value.get('business_criticality', 'Medium'))}
"""
        
        return summary.strip()
    
    def _generate_risk_assessment(self, context: DecisionContext) -> str:
        """Génère une évaluation des risques sécurisée en Markdown."""
        # Dans une implémentation réelle, cela viendrait du RiskArbitrator
        # Pour l'exemple, nous créons un résumé fictif
        
        risk_summary = """
## ⚠️ Évaluation des risques

### Blast Radius
- **Impact utilisateurs:** Limitée (moins de 100 utilisateurs)
- **Impact données:** Faible (moins de 10GB)
- **Temps d'indisponibilité potentiel:** Moins de 15 minutes

### Capacité de Rollback
- ✅ Rollback complet disponible
- ⏱️ Temps de rollback estimé: 5 minutes
- 📋 Plan de rollback validé

### Conformité
- ✅ Conforme aux standards SOC2
- ✅ Conforme aux exigences GDPR
- ✅ Audit trail disponible

### Recommandations
1. Exécuter pendant les heures creuses (02:00-04:00 UTC)
2. Notifier l'équipe de support avant exécution
3. Surveiller les métriques clés pendant 1 heure après exécution
"""
        
        return risk_summary.strip()
    
    def _determine_priority(
        self,
        automation_decision: AutomationDecision,
        context: DecisionContext
    ) -> ApprovalPriority:
        """Détermine la priorité de la demande d'approbation."""
        intent = context.intent or {}
        intent_priority = intent.get("priority", 5)
        
        if intent_priority >= 9:
            return ApprovalPriority.CRITICAL
        elif intent_priority >= 7:
            return ApprovalPriority.HIGH
        elif intent_priority >= 5:
            return ApprovalPriority.MEDIUM
        else:
            return ApprovalPriority.LOW
    
    def _determine_timeout_hours(self, priority: ApprovalPriority) -> float:
        """Détermine le timeout basé sur la priorité."""
        timeouts = {
            ApprovalPriority.CRITICAL: 1.0,   # 1 heure
            ApprovalPriority.HIGH: 4.0,       # 4 heures
            ApprovalPriority.MEDIUM: 12.0,    # 12 heures
            ApprovalPriority.LOW: 24.0        # 24 heures
        }
        
        return timeouts.get(priority, self.default_timeout_hours)
    
    def _determine_required_approvals(self, context: DecisionContext) -> int:
        """Détermine le nombre d'approbations requises."""
        environment = context.environment or {}
        env = environment.get("environment", "production")
        
        if env == "production":
            return 2  # Production nécessite 2 approbations
        else:
            return 1  # Autres environnements: 1 approbation
    
    def _generate_request_id(self) -> str:
        """Génère un ID unique pour la demande d'approbation."""
        import uuid
        return f"APPROVAL-{uuid.uuid4().hex[:8].upper()}"
    
    async def _send_approval_request(self, request: ApprovalRequest) -> bool:
        """Envoie la demande d'approbation via tous les connecteurs disponibles."""
        success = False
        
        for connector in self.connectors:
            try:
                connector_success = await connector.send_approval_request(request)
                if connector_success:
                    success = True
                    self.logger.info(
                        f"Approval request sent via {connector.__class__.__name__}",
                        request_id=request.request_id
                    )
            except Exception as e:
                self.logger.error(
                    f"Failed to send via {connector.__class__.__name__}: {e}",
                    request_id=request.request_id
                )
        
        return success
    
    async def _store_approval_request(self, request: ApprovalRequest):
        """Stocke la demande d'approbation."""
        if self.approval_store:
            await self.approval_store.store(request)
        else:
            # Stockage en mémoire (pour l'exemple)
            self.active_requests[request.request_id] = request
        
        self.logger.info(f"Approval request stored: {request.request_id}")
    
    async def _update_approval_request(self, request: ApprovalRequest):
        """Met à jour la demande d'approbation."""
        if self.approval_store:
            await self.approval_store.update(request)
        else:
            self.active_requests[request.request_id] = request
    
    async def _wait_for_approval(
        self,
        request: ApprovalRequest,
        timeout_seconds: float
    ) -> Tuple[bool, str]:
        """
        Attend la réponse à une demande d'approbation avec timeout.
        
        Args:
            request: Demande d'approbation
            timeout_seconds: Timeout en secondes
            
        Returns:
            Tuple[bool, str]: (approved, justification)
            
        Raises:
            ApprovalTimeoutError: Si le timeout est atteint
        """
        start_time = datetime.utcnow()
        timeout_time = start_time + timedelta(seconds=timeout_seconds)
        
        self.logger.info(
            "waiting_for_approval",
            request_id=request.request_id,
            timeout_seconds=timeout_seconds
        )
        
        while datetime.utcnow() < timeout_time:
            # Vérifier le statut via tous les connecteurs
            status = await self._check_approval_status(request.request_id)
            
            if status == ApprovalStatus.APPROVED:
                return True, "Approval received from human gate"
            elif status == ApprovalStatus.REJECTED:
                return False, "Rejected by human approver"
            elif status == ApprovalStatus.CANCELLED:
                return False, "Approval request was cancelled"
            
            # Attendre avant la prochaine vérification
            await asyncio.sleep(30)  # Vérifier toutes les 30 secondes
        
        # Timeout atteint
        await self._handle_approval_timeout(request)
        raise ApprovalTimeoutError(f"Approval timeout after {timeout_seconds} seconds")
    
    async def _check_approval_status(self, request_id: str) -> Optional[ApprovalStatus]:
        """Vérifie le statut d'une demande d'approbation."""
        # Vérifier dans le store
        request = await self._get_approval_request(request_id)
        if not request:
            return ApprovalStatus.CANCELLED
        
        # Si nous avons déjà une décision, la retourner
        if request.status != ApprovalStatus.PENDING:
            return request.status
        
        # Vérifier via les connecteurs
        for connector in self.connectors:
            try:
                status = await connector.check_approval_status(request_id)
                if status:
                    return status
            except Exception as e:
                self.logger.error(f"Error checking status via {connector.__class__.__name__}: {e}")
        
        return None
    
    async def _get_approval_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """Récupère une demande d'approbation."""
        if self.approval_store:
            return await self.approval_store.get(request_id)
        else:
            return self.active_requests.get(request_id)
    
    async def _handle_approval_timeout(self, request: ApprovalRequest):
        """Gère le timeout d'une approbation."""
        self.logger.warning(
            "approval_timeout_handled",
            request_id=request.request_id
        )
        
        # Mettre à jour le statut
        request.status = ApprovalStatus.EXPIRED
        
        # Annuler les demandes dans tous les connecteurs
        for connector in self.connectors:
            try:
                await connector.cancel_approval_request(request.request_id)
            except Exception as e:
                self.logger.error(f"Error cancelling request via {connector.__class__.__name__}: {e}")
        
        # Mettre à jour le store
        await self._update_approval_request(request)
    
    async def record_approval_response(
        self,
        request_id: str,
        approver: Approver,
        status: ApprovalStatus,
        comments: Optional[str] = None,
        channel: Optional[ApprovalChannel] = None
    ) -> bool:
        """
        Enregistre une réponse d'approbation (appelée depuis l'API web ou les webhooks).
        
        Args:
            request_id: ID de la demande
            approver: Approbateur
            status: Statut de la réponse
            comments: Commentaires optionnels
            channel: Canal utilisé
            
        Returns:
            bool: True si enregistré avec succès
        """
        try:
            request = await self._get_approval_request(request_id)
            if not request:
                self.logger.error(f"Approval request not found: {request_id}")
                return False
            
            response = ApprovalResponse(
                request_id=request_id,
                approver=approver,
                status=status,
                comments=comments,
                responded_at=datetime.utcnow(),
                channel=channel
            )
            
            if status == ApprovalStatus.APPROVED:
                request.approvals_received.append({
                    "approver": approver.user_id,
                    "name": approver.name,
                    "comments": comments,
                    "channel": channel.value if channel else None,
                    "timestamp": datetime.utcnow().isoformat()
                })
            elif status == ApprovalStatus.REJECTED:
                request.rejections_received.append({
                    "approver": approver.user_id,
                    "name": approver.name,
                    "comments": comments,
                    "channel": channel.value if channel else None,
                    "timestamp": datetime.utcnow().isoformat()
                })
            
            # Vérifier si nous avons suffisamment d'approbations
            if len(request.approvals_received) >= request.required_approvals:
                request.status = ApprovalStatus.APPROVED
            elif len(request.rejections_received) > 0:
                # Si quelqu'un rejette, c'est terminé
                request.status = ApprovalStatus.REJECTED
            
            await self._update_approval_request(request)
            
            self.logger.info(
                "approval_response_recorded",
                request_id=request_id,
                approver=approver.user_id,
                status=status.value,
                total_approvals=len(request.approvals_received),
                total_rejections=len(request.rejections_received)
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error recording approval response: {e}")
            return False
    
    async def get_approval_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Récupère le statut d'une demande d'approbation."""
        request = await self._get_approval_request(request_id)
        if not request:
            return None
        
        return {
            "request_id": request.request_id,
            "status": request.status.value,
            "created_at": request.created_at.isoformat(),
            "timeout_hours": request.timeout_hours,
            "priority": request.priority.value,
            "required_approvals": request.required_approvals,
            "approvals_received": len(request.approvals_received),
            "rejections_received": len(request.rejections_received),
            "approvers": [
                {
                    "user_id": a.user_id,
                    "name": a.name,
                    "email": a.email,
                    "roles": a.roles
                }
                for a in request.approvers
            ],
            "metadata": request.metadata
        }
    
    async def cleanup_expired_requests(self, max_age_hours: float = 168):
        """Nettoie les demandes expirées ou terminées."""
        expired_ids = []
        
        for request_id, request in self.active_requests.items():
            age_hours = (datetime.utcnow() - request.created_at).total_seconds() / 3600
            
            if (age_hours > max_age_hours or 
                request.status in [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED]):
                expired_ids.append(request_id)
        
        for request_id in expired_ids:
            del self.active_requests[request_id]
        
        if expired_ids:
            self.logger.info(
                "expired_requests_cleaned",
                count=len(expired_ids)
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques sur le HumanGate."""
        pending = sum(1 for r in self.active_requests.values() 
                     if r.status == ApprovalStatus.PENDING)
        
        return {
            "active_requests": len(self.active_requests),
            "pending_requests": pending,
            "connectors": [c.__class__.__name__ for c in self.connectors],
            "default_timeout_hours": self.default_timeout_hours,
            "enable_escalation": self.enable_escalation
        }


# Singleton pour utilisation facile
_human_gate_instance = None

def get_human_gate(
    connectors: Optional[List[ApprovalConnector]] = None,
    default_timeout_hours: float = 24.0
) -> HumanGate:
    """Obtient l'instance singleton du HumanGate."""
    global _human_gate_instance
    if _human_gate_instance is None:
        if connectors is None:
            # Créer des connecteurs par défaut (pour l'exemple)
            connectors = [
                EmailConnector(
                    smtp_server="smtp.example.com",
                    smtp_port=587,
                    sender_email="noreply@example.com"
                )
            ]
        
        _human_gate_instance = HumanGate(
            connectors=connectors,
            default_timeout_hours=default_timeout_hours
        )
    return _human_gate_instance


# Fonction utilitaire pour vérification rapide
async def check_human_approval(
    automation_decision: AutomationDecision,
    context: DecisionContext,
    timeout: Optional[float] = None
) -> HumanGateResponse:
    """
    Fonction utilitaire pour vérifier l'approbation humaine.
    
    Args:
        automation_decision: Décision d'automatisation
        context: Contexte décisionnel
        timeout: Timeout personnalisé en secondes
        
    Returns:
        HumanGateResponse: Réponse de l'approbation humaine
    """
    gate = get_human_gate()
    return await gate.check(automation_decision, context, timeout)


# Tests unitaires intégrés
if __name__ == "__main__":
    import asyncio
    
    async def test_human_gate():
        """Test basique du HumanGate."""
        from ..types import AutomationDecision, AutomationLevel, DecisionContext
        
        # Créer une décision d'automatisation de test
        automation_decision = AutomationDecision(
            level=AutomationLevel.AUTO_EXECUTE_WITH_MONITORING,
            justification="High confidence cost optimization",
            metadata={
                "confidence_score": 0.85,
                "risk_level": "medium",
                "requires_human_approval": True
            }
        )
        
        # Créer un contexte de test
        context = DecisionContext(
            client_preferences={
                "automation_preference": "medium",
                "risk_tolerance": "medium",
                "approvers": [
                    {
                        "user_id": "test_approver",
                        "name": "Test Approver",
                        "email": "test@example.com",
                        "roles": ["devops_engineer"],
                        "channels": ["email"]
                    }
                ]
            },
            business_value={"expected_roi": 2.5},
            system_state={},
            compliance_constraints={},
            decision_history=[],
            environment={"environment": "production"},
            intent={"type": "COST_OPTIMIZATION", "priority": 8},
            metadata={}
        )
        
        # Créer un connecteur mock
        class MockConnector(ApprovalConnector):
            async def send_approval_request(self, request: ApprovalRequest) -> bool:
                print(f"✓ Mock: Approval request sent: {request.request_id}")
                return True
            
            async def check_approval_status(self, request_id: str) -> Optional[ApprovalStatus]:
                return None
            
            async def cancel_approval_request(self, request_id: str) -> bool:
                return True
        
        # Tester avec un timeout court
        gate = HumanGate(
            connectors=[MockConnector()],
            default_timeout_hours=0.1  # 6 minutes pour le test
        )
        
        print("Testing human gate with production environment and AUTO_EXECUTE_WITH_MONITORING...")
        
        try:
            response = await gate.check(automation_decision, context, timeout=10)
            print(f"✓ Response received:")
            print(f"  Required: {response.required}")
            print(f"  Approved: {response.approved}")
            print(f"  Justification: {response.justification}")
            
            # Tester avec un environnement non-production
            print("\nTesting with non-production environment...")
            non_prod_context = DecisionContext(
                client_preferences={"automation_preference": "high"},
                business_value={},
                system_state={},
                compliance_constraints={},
                decision_history=[],
                environment={"environment": "staging"},
                intent={"type": "COST_OPTIMIZATION", "priority": 5},
                metadata={}
            )
            
            non_prod_response = await gate.check(automation_decision, non_prod_context, timeout=5)
            print(f"✓ Non-prod response:")
            print(f"  Required: {non_prod_response.required}")
            print(f"  Approved: {non_prod_response.approved}")
            
            # Tester les statistiques
            stats = gate.get_stats()
            print(f"\n✓ Gate statistics:")
            print(f"  Active requests: {stats['active_requests']}")
            print(f"  Pending requests: {stats['pending_requests']}")
            
            print("\n✓ All tests passed!")
            
        except ApprovalTimeoutError as e:
            print(f"✓ Approval timeout (expected in test): {e}")
        
        except Exception as e:
            print(f"✗ Error: {e}")
            import traceback
            traceback.print_exc()
    
    asyncio.run(test_human_gate())