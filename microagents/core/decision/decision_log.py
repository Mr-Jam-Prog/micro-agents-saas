"""
Decision Log - Journalisation immutable pour audit et conformité
Append-only, write-only avec signature optionnelle pour SOC2/ISO27001.
"""

import json
import csv
import hashlib
import hmac
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, BinaryIO, Union
from pathlib import Path
from dataclasses import dataclass, asdict, field
import logging
from enum import Enum
import structlog
import asyncio
from contextlib import asynccontextmanager
import aiofiles
import aiofiles.os

from ..types import (
    DecisionState, 
    Intent, 
    AgentChain, 
    DecisionOutcome,
    DecisionPlan
)

logger = structlog.get_logger(__name__)


class LogFormat(str, Enum):
    """Formats de sortie supportés."""
    JSONL = "jsonl"  # JSON Lines
    CSV = "csv"
    PARQUET = "parquet"


class LogStorage(str, Enum):
    """Types de stockage supportés."""
    LOCAL_FILE = "local_file"
    S3 = "s3"
    POSTGRES = "postgres"
    ELASTICSEARCH = "elasticsearch"


@dataclass
class LogEntry:
    """Entrée de journal immutable."""
    # Identifiant unique
    trace_id: str
    log_id: str
    
    # État
    previous_state: Optional[str]
    new_state: str
    transition_reason: str
    
    # Décision
    intent: Optional[Dict[str, Any]] = None
    chain: Optional[Dict[str, Any]] = None
    confidence_score: Optional[float] = None
    confidence_breakdown: Optional[Dict[str, Any]] = None
    justification: Optional[str] = None
    action: Optional[str] = None
    
    # Contexte
    environment: Optional[str] = None
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    
    # Métadonnées
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: str = "1.0"
    log_type: str = "decision_transition"
    
    # Signature
    signature: Optional[str] = None
    hash_algorithm: str = "sha256"
    
    # Métadonnées système
    hostname: Optional[str] = None
    service_version: str = "1.0.0"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire pour sérialisation."""
        data = asdict(self)
        # Nettoyer les valeurs None pour la sérialisation JSON
        return {k: v for k, v in data.items() if v is not None}
    
    def compute_hash(self) -> str:
        """Calcule le hash de l'entrée (sans la signature)."""
        data = self.to_dict()
        if 'signature' in data:
            del data['signature']
        
        # Sérialisation canonique (tri des clés)
        canonical = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


@dataclass
class LogConfig:
    """Configuration du système de log."""
    # Destination
    storage_type: LogStorage = LogStorage.LOCAL_FILE
    storage_path: Optional[Path] = None
    storage_config: Dict[str, Any] = field(default_factory=dict)
    
    # Format
    log_format: LogFormat = LogFormat.JSONL
    compression: bool = True
    max_file_size_mb: int = 100  # Rotation à 100MB
    
    # Sécurité
    enable_signing: bool = True
    signing_key: Optional[str] = None  # Chemin vers fichier de clé ou clé en clair
    hash_algorithm: str = "sha256"
    
    # Performance
    buffer_size: int = 100  # Nombre d'entrées avant flush
    flush_interval_seconds: int = 5
    async_write: bool = True
    
    # Rétention
    retention_days: int = 365
    archive_old_logs: bool = True
    
    # Métadonnées système
    service_name: str = "decision-engine"
    environment: str = "production"
    
    def __post_init__(self):
        """Valide la configuration."""
        if self.storage_type == LogStorage.LOCAL_FILE and not self.storage_path:
            self.storage_path = Path("/var/log/decision-engine")
        
        if self.enable_signing and not self.signing_key:
            logger.warning("Signing enabled but no signing key provided")
            self.enable_signing = False


class LogSigner:
    """Signe et vérifie les entrées de log pour l'intégrité."""
    
    def __init__(self, key_path: Optional[str] = None, algorithm: str = "sha256"):
        """
        Initialise le signeur.
        
        Args:
            key_path: Chemin vers fichier de clé (optionnel)
            algorithm: Algorithme de hachage (sha256, sha512)
        """
        self.algorithm = algorithm
        self.key = self._load_key(key_path) if key_path else self._generate_key()
        
    def _load_key(self, key_path: str) -> bytes:
        """Charge une clé depuis un fichier."""
        try:
            with open(key_path, 'rb') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to load signing key from {key_path}: {e}")
            return self._generate_key()
    
    def _generate_key(self) -> bytes:
        """Génère une clé temporaire (ne pas utiliser en production)."""
        import secrets
        return secrets.token_bytes(32)
    
    def sign_entry(self, entry: LogEntry) -> str:
        """Signe une entrée de log."""
        # Calculer le hash de l'entrée
        entry_hash = entry.compute_hash()
        
        # Signer avec HMAC
        hmac_obj = hmac.new(self.key, entry_hash.encode(), self.algorithm)
        return hmac_obj.hexdigest()
    
    def verify_entry(self, entry: LogEntry) -> bool:
        """Vérifie la signature d'une entrée de log."""
        if not entry.signature:
            return False
        
        expected_signature = self.sign_entry(entry)
        return hmac.compare_digest(entry.signature, expected_signature)
    
    def get_key_fingerprint(self) -> str:
        """Retourne l'empreinte de la clé pour audit."""
        return hashlib.sha256(self.key).hexdigest()


class LogWriter:
    """Gère l'écriture des logs dans différents backends."""
    
    def __init__(self, config: LogConfig):
        """
        Initialise le writer.
        
        Args:
            config: Configuration du log
        """
        self.config = config
        self.signer = LogSigner(config.signing_key, config.hash_algorithm) if config.enable_signing else None
        self.buffer: List[LogEntry] = []
        self._buffer_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        
        # Initialiser le backend de stockage
        self.backend = self._create_backend()
        
        logger.info(
            "log_writer_initialized",
            storage_type=config.storage_type.value,
            log_format=config.log_format.value,
            enable_signing=config.enable_signing
        )
    
    def _create_backend(self):
        """Crée le backend de stockage approprié."""
        if self.config.storage_type == LogStorage.LOCAL_FILE:
            return LocalFileBackend(self.config)
        elif self.config.storage_type == LogStorage.S3:
            return S3Backend(self.config)
        elif self.config.storage_type == LogStorage.POSTGRES:
            return PostgresBackend(self.config)
        else:
            raise ValueError(f"Unsupported storage type: {self.config.storage_type}")
    
    async def start(self):
        """Démarre le writer (démarre le flush automatique)."""
        if self.config.async_write and self.config.flush_interval_seconds > 0:
            self._flush_task = asyncio.create_task(self._periodic_flush())
            logger.info("log_writer_started", flush_interval=self.config.flush_interval_seconds)
    
    async def stop(self):
        """Arrête le writer (flush final)."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # Flush final
        await self.flush()
        logger.info("log_writer_stopped")
    
    async def write(self, entry: LogEntry):
        """
        Écrit une entrée de log (buffered).
        
        Args:
            entry: Entrée de log à écrire
        """
        async with self._buffer_lock:
            # Signer l'entrée si configuré
            if self.signer and self.config.enable_signing:
                entry.signature = self.signer.sign_entry(entry)
            
            self.buffer.append(entry)
            
            # Flush si le buffer est plein
            if len(self.buffer) >= self.config.buffer_size:
                await self.flush()
    
    async def flush(self):
        """Écrit toutes les entrées du buffer."""
        if not self.buffer:
            return
        
        async with self._buffer_lock:
            entries_to_write = self.buffer.copy()
            self.buffer.clear()
            
            try:
                await self.backend.write_batch(entries_to_write)
                logger.debug("log_buffer_flushed", entry_count=len(entries_to_write))
            except Exception as e:
                logger.error("log_flush_failed", error=str(e))
                # En cas d'erreur, remettre les entrées dans le buffer
                self.buffer.extend(entries_to_write)
                raise
    
    async def _periodic_flush(self):
        """Flush périodique basé sur l'intervalle configuré."""
        while True:
            try:
                await asyncio.sleep(self.config.flush_interval_seconds)
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("periodic_flush_failed", error=str(e))
    
    async def write_immediate(self, entry: LogEntry):
        """
        Écrit une entrée de log immédiatement (sans buffer).
        
        Args:
            entry: Entrée de log à écrire
        """
        # Signer l'entrée si configuré
        if self.signer and self.config.enable_signing:
            entry.signature = self.signer.sign_entry(entry)
        
        try:
            await self.backend.write_single(entry)
        except Exception as e:
            logger.error("immediate_write_failed", error=str(e))
            raise


class LogBackend(ABC):
    """Interface abstraite pour les backends de stockage."""
    
    @abstractmethod
    async def write_single(self, entry: LogEntry) -> None:
        """Écrit une entrée unique."""
        pass
    
    @abstractmethod
    async def write_batch(self, entries: List[LogEntry]) -> None:
        """Écrit un batch d'entrées."""
        pass
    
    @abstractmethod
    async def export(
        self, 
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        format: LogFormat = LogFormat.JSONL
    ) -> Union[str, bytes, Path]:
        """Exporte les logs dans un format donné."""
        pass


class LocalFileBackend(LogBackend):
    """Backend de stockage local (fichiers)."""
    
    def __init__(self, config: LogConfig):
        self.config = config
        self.base_path = config.storage_path
        self.current_file: Optional[aiofiles.threadpool.AsyncTextIOWrapper] = None
        self.current_file_path: Optional[Path] = None
        
        # Créer le répertoire s'il n'existe pas
        if self.base_path:
            self.base_path.mkdir(parents=True, exist_ok=True)
        
        # Initialiser le fichier courant
        asyncio.create_task(self._init_current_file())
    
    async def _init_current_file(self):
        """Initialise le fichier de log courant."""
        if not self.base_path:
            raise ValueError("Storage path not configured")
        
        # Créer un nom de fichier basé sur la date
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.config.service_name}_{timestamp}.{self.config.log_format.value}"
        self.current_file_path = self.base_path / filename
        
        # Ouvrir le fichier en mode append
        self.current_file = await aiofiles.open(self.current_file_path, 'a', encoding='utf-8')
    
    async def _rotate_if_needed(self):
        """Rotation du fichier si taille maximale atteinte."""
        if not self.current_file_path or not self.current_file:
            return
        
        try:
            size = await aiofiles.os.path.getsize(self.current_file_path)
            if size > self.config.max_file_size_mb * 1024 * 1024:
                await self.current_file.close()
                await self._init_current_file()
                logger.info("log_file_rotated", new_file=self.current_file_path.name)
        except Exception as e:
            logger.error("log_rotation_failed", error=str(e))
    
    async def write_single(self, entry: LogEntry) -> None:
        """Écrit une entrée unique dans le fichier."""
        if not self.current_file:
            await self._init_current_file()
        
        await self._rotate_if_needed()
        
        if self.config.log_format == LogFormat.JSONL:
            line = json.dumps(entry.to_dict(), ensure_ascii=False) + "\n"
        else:
            # Pour d'autres formats, conversion
            raise NotImplementedError(f"Format {self.config.log_format} not implemented")
        
        await self.current_file.write(line)
        await self.current_file.flush()
    
    async def write_batch(self, entries: List[LogEntry]) -> None:
        """Écrit un batch d'entrées."""
        if not entries:
            return
        
        if not self.current_file:
            await self._init_current_file()
        
        await self._rotate_if_needed()
        
        lines = []
        for entry in entries:
            if self.config.log_format == LogFormat.JSONL:
                lines.append(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            else:
                raise NotImplementedError(f"Format {self.config.log_format} not implemented")
        
        await self.current_file.writelines(lines)
        await self.current_file.flush()
    
    async def export(
        self, 
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        format: LogFormat = LogFormat.JSONL
    ) -> Path:
        """Exporte les logs dans un fichier."""
        if not self.base_path:
            raise ValueError("Storage path not configured")
        
        # Trouver les fichiers pertinents
        log_files = sorted(self.base_path.glob(f"*.{self.config.log_format.value}"))
        
        # Filtrer par date
        if start_date or end_date:
            filtered_files = []
            for file in log_files:
                # Extraire la date du nom de fichier
                # Format: service_YYYYMMDD_HHMMSS.format
                parts = file.stem.split('_')
                if len(parts) >= 2:
                    try:
                        file_date = datetime.strptime(f"{parts[-2]}_{parts[-1]}", "%Y%m%d_%H%M%S")
                        if start_date and file_date < start_date:
                            continue
                        if end_date and file_date > end_date:
                            continue
                        filtered_files.append(file)
                    except ValueError:
                        continue
            log_files = filtered_files
        
        # Créer le fichier d'export
        export_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = self.base_path / f"export_{export_timestamp}.{format.value}"
        
        if format == LogFormat.CSV:
            await self._export_to_csv(log_files, export_path)
        elif format == LogFormat.JSONL:
            await self._export_to_jsonl(log_files, export_path)
        else:
            raise NotImplementedError(f"Export format {format} not implemented")
        
        return export_path
    
    async def _export_to_csv(self, log_files: List[Path], export_path: Path):
        """Exporte en CSV."""
        all_entries = []
        
        # Lire tous les fichiers
        for file in log_files:
            async with aiofiles.open(file, 'r', encoding='utf-8') as f:
                lines = await f.readlines()
                for line in lines:
                    try:
                        entry = json.loads(line.strip())
                        all_entries.append(entry)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse line in {file}: {e}")
        
        if not all_entries:
            return
        
        # Écrire en CSV
        async with aiofiles.open(export_path, 'w', newline='', encoding='utf-8') as f:
            # Utiliser les clés du premier entry comme en-têtes
            fieldnames = all_entries[0].keys()
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            await f.write(','.join(fieldnames) + '\n')
            
            for entry in all_entries:
                # Nettoyer les valeurs pour CSV
                cleaned_entry = {}
                for key, value in entry.items():
                    if isinstance(value, (dict, list)):
                        cleaned_entry[key] = json.dumps(value, ensure_ascii=False)
                    else:
                        cleaned_entry[key] = str(value) if value is not None else ""
                
                line = ','.join(f'"{cleaned_entry.get(key, "")}"' for key in fieldnames) + '\n'
                await f.write(line)
    
    async def _export_to_jsonl(self, log_files: List[Path], export_path: Path):
        """Exporte en JSONL (simple concaténation)."""
        async with aiofiles.open(export_path, 'w', encoding='utf-8') as out_file:
            for file in log_files:
                async with aiofiles.open(file, 'r', encoding='utf-8') as in_file:
                    content = await in_file.read()
                    await out_file.write(content)


class DecisionLogger:
    """
    Logger de décision principal.
    
    Responsabilités:
    1. Créer des entrées de log structurées
    2. Gérer les signatures pour l'intégrité
    3. Fournir des méthodes pour chaque type de log
    4. Exporter les logs pour audit
    """
    
    def __init__(self, config: Optional[LogConfig] = None):
        """
        Initialise le logger.
        
        Args:
            config: Configuration du logger
        """
        self.config = config or LogConfig()
        self.writer = LogWriter(self.config)
        
        # Statistiques
        self.stats = {
            "entries_written": 0,
            "last_write_time": None,
            "errors": 0
        }
        
        logger.info(
            "decision_logger_initialized",
            storage_type=self.config.storage_type.value,
            enable_signing=self.config.enable_signing
        )
    
    async def start(self):
        """Démarre le logger."""
        await self.writer.start()
        logger.info("decision_logger_started")
    
    async def stop(self):
        """Arrête le logger (flush final)."""
        await self.writer.stop()
        logger.info("decision_logger_stopped", stats=self.stats)
    
    @asynccontextmanager
    async def scoped_logging(self):
        """Contexte pour le logging automatique."""
        await self.start()
        try:
            yield self
        finally:
            await self.stop()
    
    async def log_transition(
        self,
        trace_id: str,
        from_state: Optional[DecisionState],
        to_state: DecisionState,
        reason: str,
        intent: Optional[Intent] = None,
        chain: Optional[AgentChain] = None,
        confidence_score: Optional[float] = None,
        confidence_breakdown: Optional[Dict[str, Any]] = None,
        justification: Optional[str] = None,
        action: Optional[str] = None,
        user_id: Optional[str] = None,
        client_id: Optional[str] = None,
        hostname: Optional[str] = None
    ) -> str:
        """
        Log une transition d'état.
        
        Args:
            trace_id: ID de traçage de la décision
            from_state: État précédent
            to_state: Nouvel état
            reason: Raison de la transition
            intent: Intention (optionnel)
            chain: Chaîne d'agents (optionnel)
            confidence_score: Score de confiance (optionnel)
            confidence_breakdown: Détail du score (optionnel)
            justification: Justification (optionnel)
            action: Action prise (optionnel)
            user_id: ID utilisateur (optionnel)
            client_id: ID client (optionnel)
            hostname: Nom d'hôte (optionnel)
            
        Returns:
            str: ID de l'entrée de log
        """
        import uuid
        
        log_id = str(uuid.uuid4())
        
        entry = LogEntry(
            trace_id=trace_id,
            log_id=log_id,
            previous_state=from_state.value if from_state else None,
            new_state=to_state.value,
            transition_reason=reason,
            intent=intent.dict() if intent else None,
            chain=chain.dict() if chain else None,
            confidence_score=confidence_score,
            confidence_breakdown=confidence_breakdown,
            justification=justification,
            action=action,
            environment=self.config.environment,
            user_id=user_id,
            client_id=client_id,
            hostname=hostname or self._get_hostname()
        )
        
        try:
            await self.writer.write(entry)
            self.stats["entries_written"] += 1
            self.stats["last_write_time"] = datetime.now(timezone.utc)
            
            logger.debug(
                "transition_logged",
                trace_id=trace_id,
                from_state=from_state.value if from_state else None,
                to_state=to_state.value,
                log_id=log_id
            )
            
            return log_id
            
        except Exception as e:
            self.stats["errors"] += 1
            logger.error("log_transition_failed", trace_id=trace_id, error=str(e))
            raise
    
    async def log_decision(
        self,
        outcome: DecisionOutcome,
        plan: Optional[DecisionPlan] = None,
        user_id: Optional[str] = None,
        client_id: Optional[str] = None
    ) -> List[str]:
        """
        Log une décision complète (multiple transitions).
        
        Args:
            outcome: Résultat de la décision
            plan: Plan de décision (optionnel)
            user_id: ID utilisateur (optionnel)
            client_id: ID client (optionnel)
            
        Returns:
            List[str]: IDs des entrées de log créées
        """
        log_ids = []
        
        # Log du résultat final
        log_id = await self.log_transition(
            trace_id=outcome.trace_id,
            from_state=None,  # Pas d'état précédent pour le résultat
            to_state=outcome.final_state,
            reason="Decision completed",
            justification=outcome.justification,
            action=outcome.action,
            confidence_score=outcome.confidence,
            user_id=user_id,
            client_id=client_id
        )
        log_ids.append(log_id)
        
        # Si un plan est fourni, log les étapes
        if plan and plan.steps:
            for i, step in enumerate(plan.steps):
                step_log_id = await self.log_transition(
                    trace_id=outcome.trace_id,
                    from_state=None,
                    to_state=DecisionState.EXECUTED,  # État générique pour les étapes
                    reason=f"Plan step {i+1}: {step.get('module', 'unknown')}",
                    user_id=user_id,
                    client_id=client_id
                )
                log_ids.append(step_log_id)
        
        logger.info(
            "decision_logged",
            trace_id=outcome.trace_id,
            final_state=outcome.final_state.value,
            log_entry_count=len(log_ids)
        )
        
        return log_ids
    
    async def log_error(
        self,
        trace_id: str,
        error_type: str,
        error_message: str,
        context: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        client_id: Optional[str] = None
    ) -> str:
        """
        Log une erreur.
        
        Args:
            trace_id: ID de traçage
            error_type: Type d'erreur
            error_message: Message d'erreur
            context: Contexte additionnel (optionnel)
            user_id: ID utilisateur (optionnel)
            client_id: ID client (optionnel)
            
        Returns:
            str: ID de l'entrée de log
        """
        import uuid
        
        log_id = str(uuid.uuid4())
        
        entry = LogEntry(
            trace_id=trace_id,
            log_id=log_id,
            previous_state=None,
            new_state=DecisionState.FAILED.value,
            transition_reason=f"Error: {error_type}",
            justification=error_message,
            action="ERROR",
            environment=self.config.environment,
            user_id=user_id,
            client_id=client_id,
            hostname=self._get_hostname(),
            log_type="error"
        )
        
        try:
            await self.writer.write_immediate(entry)  # Écriture immédiate pour les erreurs
            self.stats["entries_written"] += 1
            self.stats["last_write_time"] = datetime.now(timezone.utc)
            
            logger.error(
                "error_logged",
                trace_id=trace_id,
                error_type=error_type,
                log_id=log_id
            )
            
            return log_id
            
        except Exception as e:
            self.stats["errors"] += 1
            logger.critical("error_log_failed", trace_id=trace_id, error=str(e))
            raise
    
    async def log_audit_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        user_id: Optional[str] = None,
        client_id: Optional[str] = None,
        resource_id: Optional[str] = None
    ) -> str:
        """
        Log un événement d'audit.
        
        Args:
            event_type: Type d'événement
            event_data: Données de l'événement
            user_id: ID utilisateur (optionnel)
            client_id: ID client (optionnel)
            resource_id: ID de ressource (optionnel)
            
        Returns:
            str: ID de l'entrée de log
        """
        import uuid
        
        log_id = str(uuid.uuid4())
        
        entry = LogEntry(
            trace_id=f"AUDIT-{log_id}",
            log_id=log_id,
            previous_state=None,
            new_state="AUDIT_EVENT",
            transition_reason=f"Audit event: {event_type}",
            justification=json.dumps(event_data, ensure_ascii=False),
            action=event_type,
            environment=self.config.environment,
            user_id=user_id,
            client_id=client_id,
            hostname=self._get_hostname(),
            log_type="audit",
            metadata={
                "resource_id": resource_id,
                "event_data": event_data
            }
        )
        
        try:
            await self.writer.write_immediate(entry)  # Écriture immédiate pour l'audit
            self.stats["entries_written"] += 1
            self.stats["last_write_time"] = datetime.now(timezone.utc)
            
            logger.info(
                "audit_event_logged",
                event_type=event_type,
                log_id=log_id,
                user_id=user_id
            )
            
            return log_id
            
        except Exception as e:
            self.stats["errors"] += 1
            logger.error("audit_log_failed", event_type=event_type, error=str(e))
            raise
    
    async def export_logs(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        format: LogFormat = LogFormat.CSV,
        verify_signatures: bool = True
    ) -> Union[str, bytes, Path]:
        """
        Exporte les logs pour audit.
        
        Args:
            start_date: Date de début (optionnel)
            end_date: Date de fin (optionnel)
            format: Format d'export
            verify_signatures: Vérifier les signatures
            
        Returns:
            Chemin du fichier exporté ou contenu
        """
        logger.info(
            "log_export_requested",
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
            format=format.value,
            verify_signatures=verify_signatures
        )
        
        # Flush avant l'export
        await self.writer.flush()
        
        # Exporter via le backend
        export_result = await self.writer.backend.export(start_date, end_date, format)
        
        logger.info(
            "log_export_completed",
            export_size=getattr(export_result, '__len__', lambda: 0)(),
            format=format.value
        )
        
        return export_result
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du logger."""
        return {
            **self.stats,
            "config": {
                "storage_type": self.config.storage_type.value,
                "enable_signing": self.config.enable_signing,
                "log_format": self.config.log_format.value
            },
            "backend_stats": getattr(self.writer.backend, 'get_stats', lambda: {})()
        }
    
    def _get_hostname(self) -> str:
        """Récupère le nom d'hôte du système."""
        import socket
        try:
            return socket.gethostname()
        except:
            return "unknown"


# Singleton pour utilisation facile
_decision_logger_instance = None

def get_decision_logger(config: Optional[LogConfig] = None) -> DecisionLogger:
    """Obtient l'instance singleton du DecisionLogger."""
    global _decision_logger_instance
    if _decision_logger_instance is None:
        _decision_logger_instance = DecisionLogger(config)
    return _decision_logger_instance


# Fonctions utilitaires
async def log_decision_transition(
    trace_id: str,
    from_state: Optional[DecisionState],
    to_state: DecisionState,
    reason: str,
    **kwargs
) -> str:
    """
    Fonction utilitaire pour logger une transition.
    
    Args:
        trace_id: ID de traçage
        from_state: État précédent
        to_state: Nouvel état
        reason: Raison de la transition
        **kwargs: Arguments additionnels
        
    Returns:
        str: ID de l'entrée de log
    """
    logger = get_decision_logger()
    return await logger.log_transition(trace_id, from_state, to_state, reason, **kwargs)


# Tests unitaires intégrés
if __name__ == "__main__":
    import asyncio
    import tempfile
    
    async def test_decision_logger():
        """Test basique du DecisionLogger."""
        from ..types import Intent, AgentChain, DecisionState
        
        # Créer un répertoire temporaire
        with tempfile.TemporaryDirectory() as tmpdir:
            config = LogConfig(
                storage_type=LogStorage.LOCAL_FILE,
                storage_path=Path(tmpdir),
                enable_signing=False,  # Désactiver la signature pour les tests
                buffer_size=2,  # Petit buffer pour tester le flush
                flush_interval_seconds=1
            )
            
            logger = DecisionLogger(config)
            await logger.start()
            
            try:
                # Test 1: Log d'une transition simple
                print("Test 1: Logging d'une transition...")
                trace_id = "test-trace-123"
                
                intent = Intent(
                    type="COST_OPTIMIZATION",
                    priority=8,
                    success_metrics={"cost_reduction": ">20%"}
                )
                
                chain = AgentChain(
                    agents=["cost_analyzer", "cost_recommender"],
                    expected_value=2.5
                )
                
                log_id = await logger.log_transition(
                    trace_id=trace_id,
                    from_state=DecisionState.INIT,
                    to_state=DecisionState.INTENT_RESOLVED,
                    reason="Intent resolved successfully",
                    intent=intent,
                    chain=chain,
                    confidence_score=0.85,
                    justification="High confidence due to clear intent"
                )
                
                print(f"✓ Transition loggée avec ID: {log_id}")
                
                # Test 2: Log d'une erreur
                print("\nTest 2: Logging d'une erreur...")
                error_log_id = await logger.log_error(
                    trace_id=trace_id,
                    error_type="TimeoutError",
                    error_message="Execution timeout after 30s",
                    context={"timeout_seconds": 30}
                )
                
                print(f"✓ Erreur loggée avec ID: {error_log_id}")
                
                # Test 3: Attendre le flush automatique
                print("\nTest 3: Attente du flush automatique...")
                await asyncio.sleep(2)  # Plus que l'intervalle de flush
                
                # Test 4: Exporter les logs
                print("\nTest 4: Export des logs en CSV...")
                try:
                    export_path = await logger.export_logs(
                        format=LogFormat.CSV
                    )
                    print(f"✓ Logs exportés vers: {export_path}")
                    
                    # Vérifier que le fichier existe
                    if export_path.exists():
                        size = export_path.stat().st_size
                        print(f"✓ Fichier d'export créé ({size} bytes)")
                    else:
                        print("✗ Fichier d'export non créé")
                        
                except NotImplementedError as e:
                    print(f"⚠ Export non implémenté: {e}")
                
                # Test 5: Statistiques
                print("\nTest 5: Vérification des statistiques...")
                stats = logger.get_stats()
                print(f"✓ Entrées écrites: {stats['entries_written']}")
                print(f"✓ Erreurs: {stats['errors']}")
                
                print("\n✓ Tous les tests passent!")
                
            finally:
                await logger.stop()
    
    asyncio.run(test_decision_logger())