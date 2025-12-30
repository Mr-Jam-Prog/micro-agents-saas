"""
Versioning Manager for MicroAgents Registry

Gestion avancée du versioning sémantique avec :
- Détection automatique des changements cassants
- Génération de changelogs
- Gestion des dépendances
- Tests de compatibilité
- Capacités de rollback
- Versions pinning
- Releases canary
- Gestion A/B testing
"""

import re
import json
import logging
import tempfile
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from uuid import UUID, uuid4

import semver
from git import Repo, Actor
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from src.registry.storage.models import (
    Agent, AgentVersion, Dependency, Execution, 
    PerformanceMetric, CompatibilityRule
)
from src.utils.serialization.serializers import json_serializer

logger = logging.getLogger(__name__)


# ============================================================================
# ENUMS ET MODÈLES
# ============================================================================

class VersionIncrement(str, Enum):
    """Type d'incrémentation de version."""
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    PRERELEASE = "prerelease"
    BUILD = "build"


class ChangeType(str, Enum):
    """Type de changement détecté."""
    BREAKING = "breaking"
    FEATURE = "feature"
    FIX = "fix"
    DOCS = "docs"
    STYLE = "style"
    REFACTOR = "refactor"
    PERFORMANCE = "performance"
    TEST = "test"
    BUILD = "build"
    CI = "ci"
    CHORE = "chore"


class ReleaseStrategy(str, Enum):
    """Stratégie de release."""
    IMMEDIATE = "immediate"
    CANARY = "canary"
    BLUE_GREEN = "blue_green"
    A_B_TESTING = "a_b_testing"
    ROLLING = "rolling"
    PHASED = "phased"


class VersionStatus(str, Enum):
    """Statut de version."""
    DRAFT = "draft"
    PRE_RELEASE = "pre_release"
    RELEASE_CANDIDATE = "release_candidate"
    STABLE = "stable"
    DEPRECATED = "deprecated"
    END_OF_LIFE = "end_of_life"


@dataclass
class VersionChange:
    """Détection d'un changement."""
    type: ChangeType
    description: str
    impact: str  # high, medium, low
    breaking: bool = False
    files_changed: List[str] = field(default_factory=list)
    dependencies_affected: List[str] = field(default_factory=list)


@dataclass
class CompatibilityResult:
    """Résultat de test de compatibilité."""
    compatible: bool
    score: float  # 0.0 - 1.0
    issues: List[str]
    warnings: List[str]
    breaking_changes: List[str]
    migration_required: bool
    migration_guide: Optional[str] = None


class VersionBumpRule(BaseModel):
    """Règle d'incrémentation automatique."""
    change_type: ChangeType
    increment: VersionIncrement
    conditions: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    
    class Config:
        extra = "forbid"


class VersioningConfig(BaseModel):
    """Configuration du versioning."""
    # Versioning strategy
    strategy: str = "semantic"
    require_changelog: bool = True
    enforce_semver: bool = True
    
    # Automatic versioning
    auto_detect_changes: bool = True
    auto_bump_version: bool = False
    bump_rules: List[VersionBumpRule] = Field(default_factory=list)
    
    # Pre-release management
    prerelease_prefix: str = "alpha"
    rc_prefix: str = "rc"
    build_metadata_prefix: str = "build"
    
    # Compatibility
    require_compatibility_tests: bool = True
    min_compatibility_score: float = 0.8
    
    # Release strategies
    default_release_strategy: ReleaseStrategy = ReleaseStrategy.IMMEDIATE
    canary_percentage: int = Field(default=10, ge=1, le=100)
    a_b_test_duration_days: int = 7
    
    # Rollback
    auto_rollback_on_failure: bool = True
    max_rollback_depth: int = 5
    rollback_timeout_hours: int = 24
    
    # Pinning
    allow_version_pinning: bool = True
    pin_expiry_days: int = 30
    
    # Changelog
    changelog_categories: List[str] = Field(default_factory=lambda: [
        "Breaking Changes",
        "New Features",
        "Bug Fixes",
        "Performance Improvements",
        "Documentation",
        "Other Changes"
    ])
    
    @validator('min_compatibility_score')
    def validate_compatibility_score(cls, v):
        if not 0 <= v <= 1:
            raise ValueError("Le score de compatibilité doit être entre 0 et 1")
        return v


# ============================================================================
# GESTIONNAIRE DE VERSIONING PRINCIPAL
# ============================================================================

class VersioningManager:
    """Gestionnaire principal de versioning."""
    
    def __init__(self, db_session: Session, config: Optional[VersioningConfig] = None):
        """
        Initialise le gestionnaire de versioning.
        
        Args:
            db_session: Session de base de données
            config: Configuration du versioning
        """
        self.db = db_session
        self.config = config or VersioningConfig()
        
        # Initialisation des composants
        self.detector = ChangeDetector(self.config)
        self.generator = VersionGenerator(self.config)
        self.compatibility = CompatibilityTester(self.db, self.config)
        self.releaser = ReleaseManager(self.db, self.config)
        
        # Cache
        self._cache: Dict[str, Any] = {}
        
        logger.info("VersioningManager initialisé")
    
    # ==================== GESTION DES VERSIONS ====================
    
    def create_new_version(
        self,
        agent_id: UUID,
        source_code_path: str,
        author_id: UUID,
        release_notes: Optional[str] = None,
        force_version: Optional[str] = None
    ) -> Tuple[AgentVersion, List[VersionChange]]:
        """
        Crée une nouvelle version d'un agent.
        
        Args:
            agent_id: ID de l'agent
            source_code_path: Chemin vers le code source
            author_id: ID de l'auteur
            release_notes: Notes de release optionnelles
            force_version: Forcer une version spécifique
            
        Returns:
            Tuple (nouvelle_version, changements_détectés)
        """
        try:
            # Récupération de l'agent
            agent = self.db.query(Agent).filter_by(id=agent_id).first()
            if not agent:
                raise ValueError(f"Agent non trouvé: {agent_id}")
            
            # Détection des changements
            changes = self.detect_changes(agent_id, source_code_path)
            
            # Détermination de la nouvelle version
            if force_version:
                new_version = self._validate_version(force_version)
            else:
                latest_version = self.get_latest_version(agent_id)
                bump_type = self._determine_bump_type(changes)
                new_version = self.generate_next_version(
                    latest_version.version if latest_version else "0.0.0",
                    bump_type
                )
            
            # Validation de la compatibilité
            if self.config.require_compatibility_tests and latest_version:
                compatibility = self.test_compatibility(
                    agent_id,
                    latest_version.version,
                    new_version
                )
                
                if not compatibility.compatible:
                    raise ValueError(
                        f"Changements incompatibles détectés: {compatibility.issues}"
                    )
            
            # Génération du changelog
            changelog = self.generate_changelog(changes, release_notes)
            
            # Création de la version
            version = AgentVersion(
                id=uuid4(),
                agent_id=agent_id,
                version=new_version,
                changelog=changelog,
                release_notes=release_notes or "",
                source_hash=self._calculate_source_hash(source_code_path),
                status=VersionStatus.DRAFT.value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            # Extraction des dépendances
            dependencies = self._extract_dependencies(source_code_path)
            for dep in dependencies:
                version.dependencies.append(dep)
            
            self.db.add(version)
            self.db.commit()
            
            # Audit
            self._log_version_creation(agent_id, new_version, author_id, changes)
            
            logger.info(f"Nouvelle version créée: {agent.name} v{new_version}")
            return version, changes
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Erreur création version: {str(e)}")
            raise
    
    def publish_version(
        self,
        version_id: UUID,
        release_strategy: Optional[ReleaseStrategy] = None,
        target_percentage: Optional[int] = None
    ) -> bool:
        """
        Publie une version.
        
        Args:
            version_id: ID de la version
            release_strategy: Stratégie de release
            target_percentage: Pourcentage cible pour canary
            
        Returns:
            True si publié avec succès
        """
        version = self.db.query(AgentVersion).filter_by(id=version_id).first()
        if not version:
            raise ValueError(f"Version non trouvée: {version_id}")
        
        try:
            strategy = release_strategy or self.config.default_release_strategy
            
            if strategy == ReleaseStrategy.CANARY:
                return self.releaser.deploy_canary(version, target_percentage)
            elif strategy == ReleaseStrategy.A_B_TESTING:
                return self.releaser.deploy_ab_test(version)
            elif strategy == ReleaseStrategy.BLUE_GREEN:
                return self.releaser.deploy_blue_green(version)
            else:
                return self.releaser.deploy_immediate(version)
                
        except Exception as e:
            logger.error(f"Erreur publication version: {str(e)}")
            return False
    
    # ==================== DÉTECTION DE CHANGEMENTS ====================
    
    def detect_changes(
        self,
        agent_id: UUID,
        source_code_path: str
    ) -> List[VersionChange]:
        """
        Détecte les changements depuis la dernière version.
        
        Args:
            agent_id: ID de l'agent
            source_code_path: Chemin vers le nouveau code
            
        Returns:
            Liste des changements détectés
        """
        latest_version = self.get_latest_version(agent_id)
        
        if not latest_version:
            # Première version
            return [VersionChange(
                type=ChangeType.FEATURE,
                description="Initial release",
                impact="high",
                breaking=False
            )]
        
        # Comparaison avec la version précédente
        old_source = self._get_version_source(latest_version.id)
        changes = self.detector.detect_changes(old_source, source_code_path)
        
        return changes
    
    def _determine_bump_type(self, changes: List[VersionChange]) -> VersionIncrement:
        """
        Détermine le type d'incrémentation basé sur les changements.
        
        Args:
            changes: Liste des changements détectés
            
        Returns:
            Type d'incrémentation
        """
        # Application des règles de bump
        for rule in self.config.bump_rules:
            for change in changes:
                if change.type == rule.change_type:
                    if self._check_rule_conditions(rule, change):
                        return rule.increment
        
        # Règles par défaut
        if any(c.breaking for c in changes):
            return VersionIncrement.MAJOR
        elif any(c.type == ChangeType.FEATURE for c in changes):
            return VersionIncrement.MINOR
        elif any(c.type == ChangeType.FIX for c in changes):
            return VersionIncrement.PATCH
        else:
            return VersionIncrement.PATCH
    
    # ==================== GÉNÉRATION DE VERSIONS ====================
    
    def generate_next_version(
        self,
        current_version: str,
        bump_type: VersionIncrement,
        prerelease_tag: Optional[str] = None,
        build_metadata: Optional[str] = None
    ) -> str:
        """
        Génère la prochaine version.
        
        Args:
            current_version: Version actuelle
            bump_type: Type d'incrémentation
            prerelease_tag: Tag pour pre-release
            build_metadata: Métadonnées de build
            
        Returns:
            Nouvelle version
        """
        return self.generator.generate_next(
            current_version,
            bump_type,
            prerelease_tag,
            build_metadata
        )
    
    def generate_changelog(
        self,
        changes: List[VersionChange],
        release_notes: Optional[str] = None
    ) -> str:
        """
        Génère un changelog à partir des changements.
        
        Args:
            changes: Liste des changements
            release_notes: Notes de release optionnelles
            
        Returns:
            Changelog formaté
        """
        return self.generator.generate_changelog(changes, release_notes)
    
    # ==================== TESTS DE COMPATIBILITÉ ====================
    
    def test_compatibility(
        self,
        agent_id: UUID,
        from_version: str,
        to_version: str
    ) -> CompatibilityResult:
        """
        Teste la compatibilité entre deux versions.
        
        Args:
            agent_id: ID de l'agent
            from_version: Version source
            to_version: Version cible
            
        Returns:
            Résultat de compatibilité
        """
        return self.compatibility.test_compatibility(
            agent_id,
            from_version,
            to_version
        )
    
    def check_dependency_compatibility(
        self,
        agent_id: UUID,
        dependencies: List[Dict[str, Any]]
    ) -> List[CompatibilityResult]:
        """
        Vérifie la compatibilité des dépendances.
        
        Args:
            agent_id: ID de l'agent
            dependencies: Liste des dépendances
            
        Returns:
            Résultats de compatibilité par dépendance
        """
        return self.compatibility.check_dependencies_compatibility(
            agent_id,
            dependencies
        )
    
    # ==================== GESTION DES RELEASES ====================
    
    def deploy_canary(
        self,
        version_id: UUID,
        percentage: Optional[int] = None
    ) -> bool:
        """
        Déploie une version en canary.
        
        Args:
            version_id: ID de la version
            percentage: Pourcentage du trafic
            
        Returns:
            True si déployé avec succès
        """
        version = self.db.query(AgentVersion).filter_by(id=version_id).first()
        if not version:
            return False
        
        percentage = percentage or self.config.canary_percentage
        return self.releaser.deploy_canary(version, percentage)
    
    def deploy_ab_test(
        self,
        version_a_id: UUID,
        version_b_id: UUID,
        split_percentage: int = 50,
        duration_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Déploie un test A/B entre deux versions.
        
        Args:
            version_a_id: ID version A
            version_b_id: ID version B
            split_percentage: Pourcentage pour version A
            duration_days: Durée du test
            
        Returns:
            Résultats du déploiement
        """
        version_a = self.db.query(AgentVersion).filter_by(id=version_a_id).first()
        version_b = self.db.query(AgentVersion).filter_by(id=version_b_id).first()
        
        if not version_a or not version_b:
            raise ValueError("Versions non trouvées")
        
        duration = duration_days or self.config.a_b_test_duration_days
        return self.releaser.deploy_ab_test(
            version_a,
            version_b,
            split_percentage,
            duration
        )
    
    # ==================== ROLLBACK ====================
    
    def rollback_version(
        self,
        version_id: UUID,
        reason: str,
        author_id: UUID
    ) -> bool:
        """
        Effectue un rollback à une version précédente.
        
        Args:
            version_id: ID de la version à rollback
            reason: Raison du rollback
            author_id: ID de l'auteur
            
        Returns:
            True si rollback réussi
        """
        try:
            version = self.db.query(AgentVersion).filter_by(id=version_id).first()
            if not version:
                raise ValueError(f"Version non trouvée: {version_id}")
            
            # Trouver la version précédente stable
            previous_version = self.get_previous_stable_version(version.agent_id)
            if not previous_version:
                raise ValueError("Aucune version stable précédente trouvée")
            
            # Marquer la version comme rollback
            version.status = VersionStatus.DEPRECATED.value
            version.updated_at = datetime.utcnow()
            
            # Promouvoir la version précédente
            previous_version.is_latest = True
            previous_version.updated_at = datetime.utcnow()
            
            # Log du rollback
            self._log_rollback(
                version_id,
                previous_version.id,
                reason,
                author_id
            )
            
            self.db.commit()
            
            logger.info(f"Rollback effectué: v{version.version} -> v{previous_version.version}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Erreur rollback: {str(e)}")
            return False
    
    def auto_rollback_on_failure(
        self,
        version_id: UUID,
        failure_metrics: Dict[str, Any]
    ) -> bool:
        """
        Rollback automatique en cas d'échec.
        
        Args:
            version_id: ID de la version défaillante
            failure_metrics: Métriques d'échec
            
        Returns:
            True si rollback déclenché
        """
        if not self.config.auto_rollback_on_failure:
            return False
        
        # Vérifier les conditions de rollback automatique
        should_rollback = self._should_auto_rollback(failure_metrics)
        
        if should_rollback:
            logger.warning(f"Rollback automatique déclenché pour version {version_id}")
            return self.rollback_version(
                version_id,
                "Auto-rollback due to failure metrics",
                UUID('00000000-0000-0000-0000-000000000000')  # System user
            )
        
        return False
    
    # ==================== VERSION PINNING ====================
    
    def pin_version(
        self,
        version_id: UUID,
        user_id: UUID,
        expiry_days: Optional[int] = None
    ) -> bool:
        """
        Épingle une version spécifique.
        
        Args:
            version_id: ID de la version
            user_id: ID de l'utilisateur
            expiry_days: Jours avant expiration
            
        Returns:
            True si épingle avec succès
        """
        if not self.config.allow_version_pinning:
            raise ValueError("Version pinning non autorisé")
        
        try:
            version = self.db.query(AgentVersion).filter_by(id=version_id).first()
            if not version:
                return False
            
            expiry_days = expiry_days or self.config.pin_expiry_days
            expiry_date = datetime.utcnow() + timedelta(days=expiry_days)
            
            # Créer l'enregistrement de pin (à implémenter dans le modèle)
            # pinned_version = PinnedVersion(
            #     version_id=version_id,
            #     user_id=user_id,
            #     expires_at=expiry_date
            # )
            # self.db.add(pinned_version)
            
            self.db.commit()
            
            logger.info(f"Version épinglée: v{version.version} par utilisateur {user_id}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Erreur pinning version: {str(e)}")
            return False
    
    def get_pinned_versions(self, agent_id: UUID) -> List[AgentVersion]:
        """
        Récupère les versions épinglées pour un agent.
        
        Args:
            agent_id: ID de l'agent
            
        Returns:
            Liste des versions épinglées
        """
        # Implémentation dépendante du modèle PinnedVersion
        return []
    
    # ==================== UTILITAIRES ====================
    
    def get_latest_version(self, agent_id: UUID) -> Optional[AgentVersion]:
        """
        Récupère la dernière version d'un agent.
        
        Args:
            agent_id: ID de l'agent
            
        Returns:
            Dernière version ou None
        """
        return self.db.query(AgentVersion).filter_by(
            agent_id=agent_id,
            is_latest=True
        ).first()
    
    def get_previous_stable_version(self, agent_id: UUID) -> Optional[AgentVersion]:
        """
        Récupère la version stable précédente.
        
        Args:
            agent_id: ID de l'agent
            
        Returns:
            Version stable précédente ou None
        """
        latest = self.get_latest_version(agent_id)
        if not latest:
            return None
        
        return self.db.query(AgentVersion).filter(
            AgentVersion.agent_id == agent_id,
            AgentVersion.status == VersionStatus.STABLE.value,
            AgentVersion.id != latest.id
        ).order_by(AgentVersion.created_at.desc()).first()
    
    def get_version_history(
        self,
        agent_id: UUID,
        limit: int = 50,
        include_prereleases: bool = False
    ) -> List[AgentVersion]:
        """
        Récupère l'historique des versions.
        
        Args:
            agent_id: ID de l'agent
            limit: Nombre maximum de versions
            include_prereleases: Inclure les pre-releases
            
        Returns:
            Historique des versions
        """
        query = self.db.query(AgentVersion).filter_by(agent_id=agent_id)
        
        if not include_prereleases:
            query = query.filter(
                AgentVersion.status != VersionStatus.PRE_RELEASE.value
            )
        
        return query.order_by(AgentVersion.created_at.desc()).limit(limit).all()
    
    def validate_version_string(self, version: str) -> bool:
        """
        Valide une chaîne de version.
        
        Args:
            version: Chaîne de version
            
        Returns:
            True si valide
        """
        try:
            semver.VersionInfo.parse(version)
            return True
        except ValueError:
            return False
    
    # ==================== MÉTHODES PRIVÉES ====================
    
    def _validate_version(self, version: str) -> str:
        """Valide et nettoie une version."""
        if not self.validate_version_string(version):
            raise ValueError(f"Version invalide: {version}")
        
        # Normalisation
        v = semver.VersionInfo.parse(version)
        return str(v)
    
    def _calculate_source_hash(self, source_path: str) -> str:
        """Calcule le hash du code source."""
        # Implémentation simplifiée
        with open(source_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    
    def _get_version_source(self, version_id: UUID) -> str:
        """Récupère le code source d'une version."""
        # À implémenter selon le stockage
        return ""
    
    def _extract_dependencies(self, source_path: str) -> List[Dependency]:
        """Extrait les dépendances du code source."""
        # Implémentation dépendante du langage
        return []
    
    def _check_rule_conditions(self, rule: VersionBumpRule, change: VersionChange) -> bool:
        """Vérifie les conditions d'une règle."""
        # Implémentation basique
        return True
    
    def _should_auto_rollback(self, failure_metrics: Dict[str, Any]) -> bool:
        """Détermine si un rollback automatique est nécessaire."""
        error_rate = failure_metrics.get('error_rate', 0)
        latency_increase = failure_metrics.get('latency_increase', 0)
        
        return error_rate > 0.1 or latency_increase > 2.0
    
    def _log_version_creation(
        self,
        agent_id: UUID,
        version: str,
        author_id: UUID,
        changes: List[VersionChange]
    ):
        """Log la création d'une version."""
        # À implémenter avec le système d'audit
        pass
    
    def _log_rollback(
        self,
        from_version_id: UUID,
        to_version_id: UUID,
        reason: str,
        author_id: UUID
    ):
        """Log un rollback."""
        # À implémenter avec le système d'audit
        pass


# ============================================================================
# DÉTECTEUR DE CHANGEMENTS
# ============================================================================

class ChangeDetector:
    """Détecteur de changements dans le code source."""
    
    def __init__(self, config: VersioningConfig):
        self.config = config
        
    def detect_changes(
        self,
        old_source: str,
        new_source: str
    ) -> List[VersionChange]:
        """
        Détecte les changements entre deux versions.
        
        Args:
            old_source: Ancien code source
            new_source: Nouveau code source
            
        Returns:
            Liste des changements détectés
        """
        changes = []
        
        # Analyse syntaxique (simplifiée)
        # En réalité, utiliserait un AST parser
        
        # Détection de changements cassants
        breaking_changes = self._detect_breaking_changes(old_source, new_source)
        changes.extend(breaking_changes)
        
        # Détection de nouvelles fonctionnalités
        features = self._detect_features(old_source, new_source)
        changes.extend(features)
        
        # Détection de corrections de bugs
        fixes = self._detect_fixes(old_source, new_source)
        changes.extend(fixes)
        
        return changes
    
    def _detect_breaking_changes(
        self,
        old_source: str,
        new_source: str
    ) -> List[VersionChange]:
        """Détecte les changements cassants."""
        changes = []
        
        # Règles de détection
        rules = [
            self._check_api_changes,
            self._check_schema_changes,
            self._check_dependency_changes,
        ]
        
        for rule in rules:
            result = rule(old_source, new_source)
            if result:
                changes.append(VersionChange(
                    type=ChangeType.BREAKING,
                    description=result,
                    impact="high",
                    breaking=True
                ))
        
        return changes
    
    def _detect_features(
        self,
        old_source: str,
        new_source: str
    ) -> List[VersionChange]:
        """Détecte les nouvelles fonctionnalités."""
        # Implémentation simplifiée
        return []
    
    def _detect_fixes(
        self,
        old_source: str,
        new_source: str
    ) -> List[VersionChange]:
        """Détecte les corrections de bugs."""
        # Implémentation simplifiée
        return []
    
    def _check_api_changes(self, old_source: str, new_source: str) -> Optional[str]:
        """Vérifie les changements d'API."""
        # Analyse des signatures de fonctions/méthodes
        # En réalité, utiliserait un parser spécifique au langage
        return None
    
    def _check_schema_changes(self, old_source: str, new_source: str) -> Optional[str]:
        """Vérifie les changements de schéma."""
        # Analyse des schémas de données
        return None
    
    def _check_dependency_changes(self, old_source: str, new_source: str) -> Optional[str]:
        """Vérifie les changements de dépendances."""
        # Analyse des fichiers de dépendances
        return None


# ============================================================================
# GÉNÉRATEUR DE VERSIONS
# ============================================================================

class VersionGenerator:
    """Générateur de versions et changelogs."""
    
    def __init__(self, config: VersioningConfig):
        self.config = config
        
    def generate_next(
        self,
        current_version: str,
        bump_type: VersionIncrement,
        prerelease_tag: Optional[str] = None,
        build_metadata: Optional[str] = None
    ) -> str:
        """
        Génère la version suivante.
        
        Args:
            current_version: Version actuelle
            bump_type: Type d'incrémentation
            prerelease_tag: Tag pour pre-release
            build_metadata: Métadonnées de build
            
        Returns:
            Nouvelle version
        """
        try:
            v = semver.VersionInfo.parse(current_version)
            
            if bump_type == VersionIncrement.MAJOR:
                v = v.bump_major()
            elif bump_type == VersionIncrement.MINOR:
                v = v.bump_minor()
            elif bump_type == VersionIncrement.PATCH:
                v = v.bump_patch()
            elif bump_type == VersionIncrement.PRERELEASE:
                tag = prerelease_tag or self.config.prerelease_prefix
                v = v.bump_prerelease(tag=tag)
            elif bump_type == VersionIncrement.BUILD:
                metadata = build_metadata or self.config.build_metadata_prefix
                v = v.bump_build(metadata)
            
            return str(v)
            
        except ValueError as e:
            raise ValueError(f"Erreur génération version: {str(e)}")
    
    def generate_changelog(
        self,
        changes: List[VersionChange],
        release_notes: Optional[str] = None
    ) -> str:
        """
        Génère un changelog.
        
        Args:
            changes: Liste des changements
            release_notes: Notes de release optionnelles
            
        Returns:
            Changelog formaté
        """
        lines = []
        
        # Header
        lines.append("# Changelog")
        lines.append("")
        
        if release_notes:
            lines.append(release_notes)
            lines.append("")
        
        # Group changes by type
        changes_by_type: Dict[ChangeType, List[VersionChange]] = {}
        for change in changes:
            changes_by_type.setdefault(change.type, []).append(change)
        
        # Breaking changes first
        breaking = [c for c in changes if c.breaking]
        if breaking:
            lines.append("## ⚠️ Breaking Changes")
            lines.append("")
            for change in breaking:
                lines.append(f"- **{change.description}**")
                if change.impact:
                    lines.append(f"  - Impact: {change.impact}")
            lines.append("")
        
        # Other changes by category
        for category in self.config.changelog_categories:
            relevant_changes = []
            
            if category == "New Features":
                relevant_changes = changes_by_type.get(ChangeType.FEATURE, [])
            elif category == "Bug Fixes":
                relevant_changes = changes_by_type.get(ChangeType.FIX, [])
            elif category == "Performance Improvements":
                relevant_changes = changes_by_type.get(ChangeType.PERFORMANCE, [])
            elif category == "Documentation":
                relevant_changes = changes_by_type.get(ChangeType.DOCS, [])
            
            if relevant_changes:
                lines.append(f"## {category}")
                lines.append("")
                for change in relevant_changes:
                    lines.append(f"- {change.description}")
                lines.append("")
        
        return "\n".join(lines)
    
    def generate_migration_guide(
        self,
        from_version: str,
        to_version: str,
        breaking_changes: List[str]
    ) -> str:
        """
        Génère un guide de migration.
        
        Args:
            from_version: Version source
            to_version: Version cible
            breaking_changes: Changements cassants
            
        Returns:
            Guide de migration
        """
        lines = []
        
        lines.append(f"# Migration Guide: {from_version} → {to_version}")
        lines.append("")
        lines.append("## Overview")
        lines.append("")
        lines.append(f"This guide helps you migrate from version `{from_version}` to `{to_version}`.")
        lines.append("")
        
        if breaking_changes:
            lines.append("## Breaking Changes")
            lines.append("")
            for i, change in enumerate(breaking_changes, 1):
                lines.append(f"{i}. {change}")
            lines.append("")
        
        lines.append("## Step-by-Step Migration")
        lines.append("")
        lines.append("1. **Backup your configuration**")
        lines.append("2. **Update dependencies**")
        lines.append("3. **Test in staging environment**")
        lines.append("4. **Deploy to production**")
        lines.append("")
        
        return "\n".join(lines)


# ============================================================================
# TESTEUR DE COMPATIBILITÉ
# ============================================================================

class CompatibilityTester:
    """Testeur de compatibilité entre versions."""
    
    def __init__(self, db_session: Session, config: VersioningConfig):
        self.db = db_session
        self.config = config
        
    def test_compatibility(
        self,
        agent_id: UUID,
        from_version: str,
        to_version: str
    ) -> CompatibilityResult:
        """
        Teste la compatibilité entre deux versions.
        
        Args:
            agent_id: ID de l'agent
            from_version: Version source
            to_version: Version cible
            
        Returns:
            Résultat de compatibilité
        """
        issues = []
        warnings = []
        breaking_changes = []
        
        # Récupérer les versions
        from_ver = self._get_version(agent_id, from_version)
        to_ver = self._get_version(agent_id, to_version)
        
        if not from_ver or not to_ver:
            return CompatibilityResult(
                compatible=False,
                score=0.0,
                issues=["Versions non trouvées"],
                warnings=[],
                breaking_changes=[],
                migration_required=True
            )
        
        # Tests de compatibilité
        api_compat = self._test_api_compatibility(from_ver, to_ver)
        if not api_compat["compatible"]:
            issues.extend(api_compat["issues"])
            breaking_changes.extend(api_compat["breaking_changes"])
        
        schema_compat = self._test_schema_compatibility(from_ver, to_ver)
        if not schema_compat["compatible"]:
            issues.extend(schema_compat["issues"])
            breaking_changes.extend(schema_compat["breaking_changes"])
        
        dependency_compat = self._test_dependency_compatibility(from_ver, to_ver)
        if not dependency_compat["compatible"]:
            warnings.extend(dependency_compat["warnings"])
        
        # Calcul du score
        score = self._calculate_compatibility_score(
            api_compat, schema_compat, dependency_compat
        )
        
        # Déterminer si migration requise
        migration_required = len(breaking_changes) > 0
        
        # Générer le guide de migration si nécessaire
        migration_guide = None
        if migration_required:
            generator = VersionGenerator(self.config)
            migration_guide = generator.generate_migration_guide(
                from_version,
                to_version,
                breaking_changes
            )
        
        return CompatibilityResult(
            compatible=score >= self.config.min_compatibility_score,
            score=score,
            issues=issues,
            warnings=warnings,
            breaking_changes=breaking_changes,
            migration_required=migration_required,
            migration_guide=migration_guide
        )
    
    def check_dependencies_compatibility(
        self,
        agent_id: UUID,
        dependencies: List[Dict[str, Any]]
    ) -> List[CompatibilityResult]:
        """
        Vérifie la compatibilité des dépendances.
        
        Args:
            agent_id: ID de l'agent
            dependencies: Liste des dépendances
            
        Returns:
            Résultats de compatibilité par dépendance
        """
        results = []
        
        for dep in dependencies:
            dep_id = dep.get("agent_id")
            version_constraint = dep.get("version_constraint")
            
            if not dep_id or not version_constraint:
                continue
            
            # Vérifier les versions disponibles
            compatible_versions = self._find_compatible_versions(
                UUID(dep_id),
                version_constraint
            )
            
            if not compatible_versions:
                results.append(CompatibilityResult(
                    compatible=False,
                    score=0.0,
                    issues=[f"Aucune version compatible trouvée pour la dépendance {dep_id}"],
                    warnings=[],
                    breaking_changes=[],
                    migration_required=True
                ))
            else:
                results.append(CompatibilityResult(
                    compatible=True,
                    score=1.0,
                    issues=[],
                    warnings=[],
                    breaking_changes=[],
                    migration_required=False
                ))
        
        return results
    
    def _get_version(self, agent_id: UUID, version: str) -> Optional[AgentVersion]:
        """Récupère une version spécifique."""
        return self.db.query(AgentVersion).filter_by(
            agent_id=agent_id,
            version=version
        ).first()
    
    def _test_api_compatibility(
        self,
        from_version: AgentVersion,
        to_version: AgentVersion
    ) -> Dict[str, Any]:
        """Teste la compatibilité d'API."""
        # Implémentation simplifiée
        # En réalité, analyserait les signatures d'API
        return {
            "compatible": True,
            "issues": [],
            "breaking_changes": [],
            "score": 1.0
        }
    
    def _test_schema_compatibility(
        self,
        from_version: AgentVersion,
        to_version: AgentVersion
    ) -> Dict[str, Any]:
        """Teste la compatibilité de schéma."""
        # Implémentation simplifiée
        return {
            "compatible": True,
            "issues": [],
            "breaking_changes": [],
            "score": 1.0
        }
    
    def _test_dependency_compatibility(
        self,
        from_version: AgentVersion,
        to_version: AgentVersion
    ) -> Dict[str, Any]:
        """Teste la compatibilité des dépendances."""
        # Comparer les dépendances
        from_deps = {(d.depends_on_agent_id, d.version_constraint) 
                    for d in from_version.dependencies}
        to_deps = {(d.depends_on_agent_id, d.version_constraint) 
                  for d in to_version.dependencies}
        
        added = to_deps - from_deps
        removed = from_deps - to_deps
        
        warnings = []
        if added:
            warnings.append(f"Dépendances ajoutées: {len(added)}")
        if removed:
            warnings.append(f"Dépendances supprimées: {len(removed)}")
        
        return {
            "compatible": True,
            "warnings": warnings,
            "score": 1.0 if not (added or removed) else 0.8
        }
    
    def _calculate_compatibility_score(
        self,
        api_compat: Dict[str, Any],
        schema_compat: Dict[str, Any],
        dependency_compat: Dict[str, Any]
    ) -> float:
        """Calcule le score de compatibilité."""
        weights = {
            "api": 0.5,
            "schema": 0.3,
            "dependency": 0.2
        }
        
        score = (
            api_compat["score"] * weights["api"] +
            schema_compat["score"] * weights["schema"] +
            dependency_compat["score"] * weights["dependency"]
        )
        
        return round(score, 2)
    
    def _find_compatible_versions(
        self,
        agent_id: UUID,
        version_constraint: str
    ) -> List[AgentVersion]:
        """Trouve les versions compatibles avec une contrainte."""
        # Récupérer toutes les versions
        versions = self.db.query(AgentVersion).filter_by(
            agent_id=agent_id,
            status=VersionStatus.STABLE.value
        ).all()
        
        # Filtrer par contrainte
        compatible = []
        for version in versions:
            try:
                if semver.match(version.version, version_constraint):
                    compatible.append(version)
            except ValueError:
                continue
        
        return compatible


# ============================================================================
# GESTIONNAIRE DE RELEASES
# ============================================================================

class ReleaseManager:
    """Gestionnaire de déploiements et releases."""
    
    def __init__(self, db_session: Session, config: VersioningConfig):
        self.db = db_session
        self.config = config
        self.active_deployments: Dict[UUID, Dict[str, Any]] = {}
        
    def deploy_immediate(self, version: AgentVersion) -> bool:
        """
        Déploie immédiatement une version.
        
        Args:
            version: Version à déployer
            
        Returns:
            True si déployé avec succès
        """
        try:
            # Mettre à jour le statut
            version.status = VersionStatus.STABLE.value
            version.published_at = datetime.utcnow()
            version.is_latest = True
            
            # Désactiver l'ancienne version latest
            self.db.query(AgentVersion).filter(
                AgentVersion.agent_id == version.agent_id,
                AgentVersion.id != version.id,
                AgentVersion.is_latest == True
            ).update({"is_latest": False})
            
            self.db.commit()
            
            logger.info(f"Version déployée immédiatement: {version.version}")
            return True
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Erreur déploiement immédiat: {str(e)}")
            return False
    
    def deploy_canary(self, version: AgentVersion, percentage: int = 10) -> bool:
        """
        Déploie une version en canary.
        
        Args:
            version: Version à déployer
            percentage: Pourcentage du trafic
            
        Returns:
            True si déploiement canary démarré
        """
        try:
            # Créer la release canary
            canary_id = uuid4()
            
            self.active_deployments[canary_id] = {
                "type": "canary",
                "version_id": version.id,
                "percentage": percentage,
                "started_at": datetime.utcnow(),
                "status": "active",
                "metrics": {
                    "success_count": 0,
                    "error_count": 0,
                    "total_executions": 0
                }
            }
            
            # Marquer comme pre-release
            version.status = VersionStatus.PRE_RELEASE.value
            version.published_at = datetime.utcnow()
            
            self.db.commit()
            
            logger.info(f"Déploiement canary démarré: v{version.version} ({percentage}%)")
            return True
            
        except Exception as e:
            logger.error(f"Erreur déploiement canary: {str(e)}")
            return False
    
    def deploy_ab_test(
        self,
        version_a: AgentVersion,
        version_b: AgentVersion,
        split_percentage: int = 50,
        duration_days: int = 7
    ) -> Dict[str, Any]:
        """
        Déploie un test A/B.
        
        Args:
            version_a: Version A
            version_b: Version B
            split_percentage: Pourcentage pour version A
            duration_days: Durée du test
            
        Returns:
            Résultats du déploiement
        """
        try:
            test_id = uuid4()
            ends_at = datetime.utcnow() + timedelta(days=duration_days)
            
            self.active_deployments[test_id] = {
                "type": "a_b_test",
                "version_a_id": version_a.id,
                "version_b_id": version_b.id,
                "split_percentage": split_percentage,
                "started_at": datetime.utcnow(),
                "ends_at": ends_at,
                "status": "active",
                "metrics": {
                    "a": {"success": 0, "errors": 0, "latency": []},
                    "b": {"success": 0, "errors": 0, "latency": []}
                }
            }
            
            # Marquer les versions comme test
            version_a.status = VersionStatus.PRE_RELEASE.value
            version_b.status = VersionStatus.PRE_RELEASE.value
            
            self.db.commit()
            
            logger.info(
                f"Test A/B démarré: v{version_a.version} ({split_percentage}%) vs "
                f"v{version_b.version} ({100-split_percentage}%)"
            )
            
            return {
                "test_id": test_id,
                "status": "started",
                "ends_at": ends_at
            }
            
        except Exception as e:
            logger.error(f"Erreur démarrage test A/B: {str(e)}")
            return {"status": "error", "error": str(e)}
    
    def deploy_blue_green(self, version: AgentVersion) -> bool:
        """
        Déploie en blue-green.
        
        Args:
            version: Version à déployer
            
        Returns:
            True si déploiement démarré
        """
        try:
            deployment_id = uuid4()
            
            self.active_deployments[deployment_id] = {
                "type": "blue_green",
                "version_id": version.id,
                "phase": "blue",  # blue = nouvelle version, green = ancienne
                "started_at": datetime.utcnow(),
                "status": "active",
                "traffic_switch_at": None
            }
            
            version.status = VersionStatus.PRE_RELEASE.value
            
            self.db.commit()
            
            logger.info(f"Déploiement blue-green démarré: v{version.version}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur déploiement blue-green: {str(e)}")
            return False
    
    def switch_traffic(self, deployment_id: UUID) -> bool:
        """
        Bascule le trafic dans un déploiement blue-green.
        
        Args:
            deployment_id: ID du déploiement
            
        Returns:
            True si basculement réussi
        """
        deployment = self.active_deployments.get(deployment_id)
        if not deployment:
            return False
        
        try:
            deployment["phase"] = "green"
            deployment["traffic_switch_at"] = datetime.utcnow()
            
            # Promouvoir la version
            version = self.db.query(AgentVersion).get(deployment["version_id"])
            if version:
                version.status = VersionStatus.STABLE.value
                version.is_latest = True
                self.db.commit()
            
            logger.info(f"Trafic basculé pour déploiement {deployment_id}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur basculement trafic: {str(e)}")
            return False
    
    def promote_canary(self, deployment_id: UUID) -> bool:
        """
        Promote une version canary.
        
        Args:
            deployment_id: ID du déploiement
            
        Returns:
            True si promotion réussie
        """
        deployment = self.active_deployments.get(deployment_id)
        if not deployment or deployment["type"] != "canary":
            return False
        
        try:
            metrics = deployment["metrics"]
            success_rate = metrics["success_count"] / max(metrics["total_executions"], 1)
            
            if success_rate >= 0.95:  # Seuil de promotion
                version = self.db.query(AgentVersion).get(deployment["version_id"])
                if version:
                    return self.deploy_immediate(version)
            
            return False
            
        except Exception as e:
            logger.error(f"Erreur promotion canary: {str(e)}")
            return False
    
    def get_active_deployments(self) -> List[Dict[str, Any]]:
        """Récupère les déploiements actifs."""
        return list(self.active_deployments.values())


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def parse_semver(version: str) -> semver.VersionInfo:
    """Parse une version semver."""
    try:
        return semver.VersionInfo.parse(version)
    except ValueError as e:
        raise ValueError(f"Version semver invalide: {version}") from e


def compare_versions(v1: str, v2: str) -> int:
    """Compare deux versions semver."""
    ver1 = parse_semver(v1)
    ver2 = parse_semver(v2)
    
    if ver1 > ver2:
        return 1
    elif ver1 < ver2:
        return -1
    else:
        return 0


def is_prerelease(version: str) -> bool:
    """Vérifie si une version est une pre-release."""
    ver = parse_semver(version)
    return ver.prerelease is not None


def get_version_parts(version: str) -> Tuple[int, int, int, Optional[str], Optional[str]]:
    """Extrait les parties d'une version."""
    ver = parse_semver(version)
    return (ver.major, ver.minor, ver.patch, ver.prerelease, ver.build)


# Exemple d'utilisation
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # Configuration
    config = VersioningConfig(
        auto_detect_changes=True,
        require_compatibility_tests=True,
        min_compatibility_score=0.8,
        default_release_strategy=ReleaseStrategy.CANARY,
        canary_percentage=10
    )
    
    # Initialisation
    engine = create_engine('sqlite:///:memory:')
    Session = sessionmaker(bind=engine)
    db = Session()
    
    # Création du manager
    manager = VersioningManager(db, config)
    
    print("Versioning Manager initialisé avec succès!")
    print(f"Stratégie par défaut: {config.default_release_strategy}")
    print(f"Score compatibilité minimum: {config.min_compatibility_score}")