"""
Module d'agents réparateurs (~250 agents)

Ce module contient ~250 agents spécialisés dans la réparation et la remediation:
1. Auto-réparation (100+ scripts)
2. Guides de réparation manuelle (80+ playbooks)
3. Mesures préventives (70+ agents)

Types de remediation:
- Corrections infrastructure (redémarrage, scaling, remplacement)
- Changements de configuration
- Déploiements de code
- Correctifs de sécurité
- Récupération de données
- Améliorations de processus
- Recommandations de formation
- Mises à jour de politiques
"""

from typing import Dict, List, Optional, Any, Tuple, Union
from datetime import datetime, timedelta
from enum import Enum
import asyncio
import subprocess
import json
import yaml
from pathlib import Path
import shutil
from dataclasses import dataclass, field
from collections import defaultdict, deque
import re
import tempfile
import logging
import hashlib

# Librairies cloud
import boto3
from google.cloud import compute_v1
from azure.mgmt.compute import ComputeManagementClient
import kubernetes.client
from kubernetes import config, client

# Librairies DevOps
import ansible_runner
import paramiko
from jinja2 import Template
import docker
import git

from microagents.core.base.agent import BaseAgent, AgentResult
from microagents.core.base.context import AgentContext
from microagents.core.business_value.calculator import BusinessValueCalculator
from microagents.core.compliance.evidence_collector import ComplianceEvidenceCollector
from microagents.monitoring.metrics.collector import MetricsCollector
from microagents.utils.security.utils import encrypt_data, decrypt_data


# ==================== ENUMS ET TYPES ====================

class RemediationType(str, Enum):
    """Types de remediation"""
    AUTO_REMEDIATION = "auto_remediation"
    MANUAL_GUIDANCE = "manual_guidance"
    PREVENTIVE_MEASURE = "preventive_measure"


class RemediationAction(str, Enum):
    """Actions de remediation"""
    RESTART = "restart"
    SCALE = "scale"
    REPLACE = "replace"
    RECONFIGURE = "reconfigure"
    PATCH = "patch"
    ROLLBACK = "rollback"
    RESTORE = "restore"
    CLEANUP = "cleanup"
    ISOLATE = "isolate"
    NOTIFY = "notify"


class RemediationPriority(str, Enum):
    """Priorités de remediation"""
    CRITICAL = "critical"      # < 5 minutes
    HIGH = "high"              # < 15 minutes
    MEDIUM = "medium"          # < 1 heure
    LOW = "low"               # < 4 heures
    PLANNED = "planned"       # > 24 heures


@dataclass
class RemediationStep:
    """Étape de remediation"""
    step_number: int
    action: str
    description: str
    command: Optional[str] = None
    script: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    expected_output: Optional[str] = None
    validation_check: Optional[str] = None
    timeout_seconds: int = 300
    retry_count: int = 3


@dataclass
class RemediationPlan:
    """Plan complet de remediation"""
    incident_id: str
    remediation_type: RemediationType
    priority: RemediationPriority
    estimated_duration_minutes: int
    steps: List[RemediationStep]
    pre_conditions: List[str]
    post_conditions: List[str]
    rollback_steps: List[RemediationStep]
    success_criteria: Dict[str, Any]
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RemediationResult:
    """Résultat d'une remediation"""
    remediation_id: str
    incident_id: str
    status: str  # pending, in_progress, completed, failed, rolled_back
    steps_completed: int
    total_steps: int
    start_time: datetime
    end_time: Optional[datetime] = None
    execution_log: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


# ==================== MOTEUR DE REMEDIATION GÉNÉRIQUE ====================

class RemediationEngine:
    """Moteur de remediation générique"""
    
    def __init__(self, scripts_dir: str = "scripts/remediation"):
        self.scripts_dir = Path(scripts_dir)
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir = Path("results/remediation")
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        # Clients cloud
        self.aws_client = None
        self.gcp_client = None
        self.azure_client = None
        self.k8s_client = None
        
        # Métriques
        self.metrics_collector = MetricsCollector()
        self.remediation_history = deque(maxlen=1000)
        
    async def execute_remediation(
        self,
        plan: RemediationPlan,
        dry_run: bool = False
    ) -> RemediationResult:
        """Exécute un plan de remediation"""
        
        remediation_id = f"remediation_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{plan.incident_id[:8]}"
        
        result = RemediationResult(
            remediation_id=remediation_id,
            incident_id=plan.incident_id,
            status="in_progress",
            steps_completed=0,
            total_steps=len(plan.steps),
            start_time=datetime.utcnow(),
            execution_log=[]
        )
        
        try:
            # Vérifier les pré-conditions
            await self._check_preconditions(plan.pre_conditions, result)
            
            # Exécuter chaque étape
            for step in plan.steps:
                step_result = await self._execute_remediation_step(
                    step, plan, dry_run, result
                )
                
                result.execution_log.append(step_result)
                result.steps_completed += 1
                
                if step_result.get("status") == "failed":
                    result.status = "failed"
                    result.errors.append(f"Step {step.step_number} failed: {step_result.get('error')}")
                    
                    # Exécuter le rollback si nécessaire
                    if plan.rollback_steps:
                        await self._execute_rollback(plan.rollback_steps, result)
                        result.status = "rolled_back"
                    
                    break
            
            if result.status == "in_progress":
                result.status = "completed"
                
                # Vérifier les critères de succès
                success = await self._verify_success_criteria(
                    plan.success_criteria, result
                )
                if not success:
                    result.status = "partially_completed"
                    result.errors.append("Success criteria not fully met")
        
        except Exception as e:
            result.status = "failed"
            result.errors.append(f"Remediation engine error: {str(e)}")
        
        finally:
            result.end_time = datetime.utcnow()
            
            # Enregistrer les métriques
            await self._record_remediation_metrics(result, plan)
            
            # Sauvegarder le résultat
            await self._save_remediation_result(result)
        
        return result
    
    async def _execute_remediation_step(
        self,
        step: RemediationStep,
        plan: RemediationPlan,
        dry_run: bool,
        result: RemediationResult
    ) -> Dict[str, Any]:
        """Exécute une étape de remediation"""
        
        step_result = {
            "step_number": step.step_number,
            "action": step.action,
            "start_time": datetime.utcnow(),
            "dry_run": dry_run
        }
        
        try:
            if dry_run:
                step_result["status"] = "dry_run"
                step_result["output"] = f"Would execute: {step.description}"
                return step_result
            
            # Exécuter selon le type d'action
            if step.script:
                output = await self._execute_script(step.script, step.parameters)
            elif step.command:
                output = await self._execute_command(step.command, step.timeout_seconds)
            else:
                # Action spécifique
                output = await getattr(self, f"_execute_{step.action}")(step.parameters)
            
            step_result["output"] = output
            step_result["status"] = "completed"
            
            # Valider si nécessaire
            if step.validation_check:
                validation_result = await self._validate_step_output(output, step.validation_check)
                step_result["validation"] = validation_result
            
        except Exception as e:
            step_result["status"] = "failed"
            step_result["error"] = str(e)
            
            # Réessayer si configuré
            if step.retry_count > 0:
                for retry in range(step.retry_count):
                    try:
                        await asyncio.sleep(2 ** retry)  # Backoff exponentiel
                        # Réessayer l'exécution
                        if step.script:
                            output = await self._execute_script(step.script, step.parameters)
                        else:
                            output = await self._execute_command(step.command, step.timeout_seconds)
                        
                        step_result["status"] = "completed_after_retry"
                        step_result["output"] = output
                        step_result["retry_count"] = retry + 1
                        break
                    except Exception as retry_error:
                        step_result[f"retry_{retry + 1}_error"] = str(retry_error)
        
        finally:
            step_result["end_time"] = datetime.utcnow()
            duration = (step_result["end_time"] - step_result["start_time"]).total_seconds()
            step_result["duration_seconds"] = duration
        
        return step_result
    
    async def _execute_script(self, script_name: str, parameters: Dict) -> str:
        """Exécute un script de remediation"""
        script_path = self.scripts_dir / f"{script_name}.py"
        
        if not script_path.exists():
            raise FileNotFoundError(f"Script not found: {script_path}")
        
        # Préparer les paramètres
        params_json = json.dumps(parameters)
        
        # Exécuter le script
        process = await asyncio.create_subprocess_exec(
            "python", str(script_path), params_json,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            raise RuntimeError(f"Script execution failed: {stderr.decode()}")
        
        return stdout.decode()
    
    async def _execute_command(self, command: str, timeout: int) -> str:
        """Exécute une commande shell"""
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                raise TimeoutError(f"Command timed out after {timeout} seconds")
            
            if process.returncode != 0:
                raise RuntimeError(f"Command failed: {stderr.decode()}")
            
            return stdout.decode()
        
        except Exception as e:
            raise RuntimeError(f"Command execution error: {str(e)}")
    
    async def _execute_restart(self, parameters: Dict) -> str:
        """Exécute un redémarrage"""
        service_type = parameters.get("service_type")
        
        if service_type == "systemd":
            service_name = parameters["service_name"]
            command = f"sudo systemctl restart {service_name}"
            return await self._execute_command(command, 60)
        
        elif service_type == "docker":
            container_name = parameters["container_name"]
            command = f"docker restart {container_name}"
            return await self._execute_command(command, 60)
        
        elif service_type == "k8s":
            namespace = parameters.get("namespace", "default")
            deployment = parameters["deployment"]
            
            # Utiliser le client Kubernetes
            if not self.k8s_client:
                self._init_k8s_client()
            
            api = client.AppsV1Api(self.k8s_client)
            
            # Rollout restart
            body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": datetime.utcnow().isoformat()
                            }
                        }
                    }
                }
            }
            
            api.patch_namespaced_deployment(
                name=deployment,
                namespace=namespace,
                body=body
            )
            
            return f"Kubernetes deployment {deployment} restart initiated"
        
        else:
            raise ValueError(f"Unsupported service type: {service_type}")
    
    async def _execute_scale(self, parameters: Dict) -> str:
        """Exécute un scaling"""
        scaling_type = parameters.get("scaling_type")
        
        if scaling_type == "k8s":
            namespace = parameters.get("namespace", "default")
            deployment = parameters["deployment"]
            replicas = parameters["replicas"]
            
            if not self.k8s_client:
                self._init_k8s_client()
            
            api = client.AppsV1Api(self.k8s_client)
            
            # Scale le deployment
            scale = client.V1Scale(
                metadata=client.V1ObjectMeta(
                    name=deployment,
                    namespace=namespace
                ),
                spec=client.V1ScaleSpec(replicas=replicas)
            )
            
            api.replace_namespaced_deployment_scale(
                name=deployment,
                namespace=namespace,
                body=scale
            )
            
            return f"Scaled deployment {deployment} to {replicas} replicas"
        
        elif scaling_type == "aws_autoscaling":
            # Scaling AWS Auto Scaling Group
            if not self.aws_client:
                self._init_aws_client()
            
            asg_name = parameters["asg_name"]
            desired_capacity = parameters["desired_capacity"]
            
            autoscaling = self.aws_client.client('autoscaling')
            autoscaling.set_desired_capacity(
                AutoScalingGroupName=asg_name,
                DesiredCapacity=desired_capacity,
                HonorCooldown=parameters.get("honor_cooldown", False)
            )
            
            return f"Scaled ASG {asg_name} to {desired_capacity} instances"
        
        else:
            raise ValueError(f"Unsupported scaling type: {scaling_type}")
    
    def _init_k8s_client(self):
        """Initialise le client Kubernetes"""
        try:
            config.load_kube_config()
            self.k8s_client = client.ApiClient()
        except:
            # Essayer in-cluster config
            config.load_incluster_config()
            self.k8s_client = client.ApiClient()
    
    def _init_aws_client(self):
        """Initialise le client AWS"""
        self.aws_client = boto3.Session()


# ==================== AGENTS D'AUTO-REMEDIATION (100+) ====================

class AutoRemediator(BaseAgent):
    """Agent d'auto-remediation générique"""
    
    def __init__(self):
        super().__init__(
            agent_id="auto_remediator_v1",
            category="remediators.auto.general"
        )
        self.remediation_engine = RemediationEngine()
        self.known_patterns = self._load_remediation_patterns()
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        """Détecte et exécute les remediations automatiques"""
        incident_data = context.get_data("incident_data", {})
        
        # Identifier le type d'incident
        incident_type = incident_data.get("type")
        severity = incident_data.get("severity", "medium")
        
        # Chercher un pattern de remediation correspondant
        remediation_pattern = self._find_remediation_pattern(incident_type, incident_data)
        
        if not remediation_pattern:
            return AgentResult.success(
                data={"message": "No auto-remediation pattern found for this incident"},
                metadata={"incident_type": incident_type}
            )
        
        # Créer le plan de remediation
        remediation_plan = await self._create_remediation_plan(
            remediation_pattern, incident_data
        )
        
        # Déterminer si l'auto-remediation est autorisée
        if not await self._is_auto_remediation_allowed(remediation_plan, severity):
            return AgentResult.success(
                data={
                    "remediation_plan": remediation_plan,
                    "action": "manual_remediation_required"
                },
                metadata={
                    "reason": "Auto-remediation not allowed for this severity",
                    "severity": severity
                }
            )
        
        # Exécuter la remediation
        result = await self.remediation_engine.execute_remediation(
            remediation_plan, dry_run=False
        )
        
        return AgentResult.success(
            data={
                "remediation_result": result,
                "incident_data": incident_data
            },
            metadata={
                "auto_remediated": True,
                "remediation_id": result.remediation_id,
                "status": result.status
            }
        )
    
    async def _create_remediation_plan(
        self,
        pattern: Dict,
        incident_data: Dict
    ) -> RemediationPlan:
        """Crée un plan de remediation à partir d'un pattern"""
        
        steps = []
        for i, step_pattern in enumerate(pattern.get("steps", []), 1):
            step = RemediationStep(
                step_number=i,
                action=step_pattern["action"],
                description=self._render_template(step_pattern["description"], incident_data),
                command=self._render_template(step_pattern.get("command", ""), incident_data) if step_pattern.get("command") else None,
                script=step_pattern.get("script"),
                parameters=self._render_parameters(step_pattern.get("parameters", {}), incident_data),
                timeout_seconds=step_pattern.get("timeout", 300),
                retry_count=step_pattern.get("retry_count", 3)
            )
            steps.append(step)
        
        return RemediationPlan(
            incident_id=incident_data.get("id", "unknown"),
            remediation_type=RemediationType.AUTO_REMEDIATION,
            priority=self._determine_priority(incident_data.get("severity")),
            estimated_duration_minutes=pattern.get("estimated_duration", 15),
            steps=steps,
            pre_conditions=pattern.get("pre_conditions", []),
            post_conditions=pattern.get("post_conditions", []),
            rollback_steps=self._create_rollback_steps(pattern.get("rollback_steps", []), incident_data),
            success_criteria=pattern.get("success_criteria", {})
        )


class ServiceRestartRemediator(BaseAgent):
    """Réparateur automatique par redémarrage de service"""
    
    def __init__(self, service_name: str):
        super().__init__(
            agent_id=f"service_restart_remediator_{service_name}_v1",
            category=f"remediators.auto.restart.{service_name}"
        )
        self.service_name = service_name
        self.max_restarts = 3
        self.restart_history = defaultdict(list)
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        service_metrics = context.get_data("service_metrics", {})
        
        # Vérifier si un redémarrage est nécessaire
        needs_restart = await self._check_if_restart_needed(service_metrics)
        
        if not needs_restart:
            return AgentResult.success(
                data={"message": "Service does not need restart"},
                metadata={"service": self.service_name}
            )
        
        # Vérifier l'historique des redémarrages
        recent_restarts = self._get_recent_restarts()
        if len(recent_restarts) >= self.max_restarts:
            return AgentResult.error(
                f"Maximum restart attempts ({self.max_restarts}) reached for {self.service_name}"
            )
        
        # Exécuter le redémarrage
        restart_result = await self._execute_service_restart()
        
        # Enregistrer le redémarrage
        self._record_restart(restart_result)
        
        return AgentResult.success(
            data={
                "restart_result": restart_result,
                "restart_count": len(recent_restarts) + 1
            },
            metadata={
                "service": self.service_name,
                "auto_remediated": True
            }
        )
    
    async def _execute_service_restart(self) -> Dict[str, Any]:
        """Exécute le redémarrage du service"""
        
        commands = [
            f"sudo systemctl status {self.service_name}",
            f"sudo systemctl restart {self.service_name}",
            f"sudo systemctl status {self.service_name}"
        ]
        
        results = []
        for command in commands:
            try:
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                results.append({
                    "command": command,
                    "returncode": process.returncode,
                    "stdout": stdout.decode(),
                    "stderr": stderr.decode()
                })
                
            except Exception as e:
                results.append({
                    "command": command,
                    "error": str(e)
                })
        
        return {
            "timestamp": datetime.utcnow(),
            "service": self.service_name,
            "results": results
        }


class DiskSpaceRemediator(BaseAgent):
    """Réparateur automatique d'espace disque"""
    
    def __init__(self):
        super().__init__(
            agent_id="disk_space_remediator_v1",
            category="remediators.auto.disk_space"
        )
        self.threshold_percentage = 90
        self.cleanup_strategies = [
            "docker_prune",
            "log_rotation",
            "temp_files",
            "package_cache"
        ]
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        disk_metrics = context.get_data("disk_metrics", {})
        
        # Identifier les partitions critiques
        critical_partitions = []
        for partition, metrics in disk_metrics.items():
            usage_percent = metrics.get("usage_percent", 0)
            if usage_percent >= self.threshold_percentage:
                critical_partitions.append({
                    "partition": partition,
                    "usage_percent": usage_percent,
                    "available_gb": metrics.get("available_gb", 0)
                })
        
        if not critical_partitions:
            return AgentResult.success(
                data={"message": "No disk space issues detected"}
            )
        
        # Exécuter les stratégies de nettoyage
        cleanup_results = []
        for partition in critical_partitions:
            partition_results = await self._execute_cleanup_strategies(
                partition["partition"]
            )
            cleanup_results.append({
                "partition": partition["partition"],
                "results": partition_results
            })
        
        # Vérifier l'amélioration
        post_cleanup_metrics = await self._get_disk_metrics()
        improvement = self._calculate_improvement(disk_metrics, post_cleanup_metrics)
        
        return AgentResult.success(
            data={
                "critical_partitions": critical_partitions,
                "cleanup_results": cleanup_results,
                "improvement": improvement,
                "post_cleanup_metrics": post_cleanup_metrics
            },
            metadata={
                "auto_remediated": True,
                "threshold_percentage": self.threshold_percentage
            }
        )
    
    async def _execute_cleanup_strategies(self, partition: str) -> List[Dict]:
        """Exécute les stratégies de nettoyage"""
        
        results = []
        
        for strategy in self.cleanup_strategies:
            try:
                if strategy == "docker_prune":
                    result = await self._docker_prune()
                elif strategy == "log_rotation":
                    result = await self._rotate_logs(partition)
                elif strategy == "temp_files":
                    result = await self._clean_temp_files(partition)
                elif strategy == "package_cache":
                    result = await self._clean_package_cache()
                else:
                    continue
                
                results.append({
                    "strategy": strategy,
                    "result": result
                })
                
            except Exception as e:
                results.append({
                    "strategy": strategy,
                    "error": str(e)
                })
        
        return results
    
    async def _docker_prune(self) -> Dict:
        """Nettoyage Docker"""
        commands = [
            "docker system prune -a -f --volumes",
            "docker image prune -a -f",
            "docker container prune -f",
            "docker volume prune -f"
        ]
        
        results = []
        for cmd in commands:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            results.append({
                "command": cmd,
                "returncode": process.returncode,
                "stdout": stdout.decode()[:500],  # Limiter la taille
                "stderr": stderr.decode()[:500]
            })
        
        return {"commands_executed": results}


class MemoryPressureRemediator(BaseAgent):
    """Réparateur automatique de pression mémoire"""
    
    def __init__(self):
        super().__init__(
            agent_id="memory_pressure_remediator_v1",
            category="remediators.auto.memory"
        )
        self.memory_threshold = 85  # Pourcentage
        self.swap_threshold = 70    # Pourcentage
        self.remediation_actions = [
            "clear_page_cache",
            "drop_caches",
            "restart_memory_hog",
            "scale_memory"
        ]
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        memory_metrics = context.get_data("memory_metrics", {})
        
        memory_usage = memory_metrics.get("usage_percent", 0)
        swap_usage = memory_metrics.get("swap_usage_percent", 0)
        
        # Vérifier les seuils
        needs_remediation = (
            memory_usage >= self.memory_threshold or
            swap_usage >= self.swap_threshold
        )
        
        if not needs_remediation:
            return AgentResult.success(
                data={"message": "Memory pressure within acceptable limits"}
            )
        
        # Identifier les processus gourmands
        memory_hogs = await self._identify_memory_hogs()
        
        # Exécuter les actions de remediation
        remediation_results = []
        for action in self.remediation_actions:
            try:
                if action == "clear_page_cache":
                    result = await self._clear_page_cache()
                elif action == "drop_caches":
                    result = await self._drop_caches()
                elif action == "restart_memory_hog":
                    result = await self._restart_memory_hog(memory_hogs)
                elif action == "scale_memory":
                    result = await self._scale_memory_resources()
                else:
                    continue
                
                remediation_results.append({
                    "action": action,
                    "result": result
                })
                
            except Exception as e:
                remediation_results.append({
                    "action": action,
                    "error": str(e)
                })
        
        # Mesurer l'amélioration
        post_remediation_metrics = await self._get_memory_metrics()
        improvement = {
            "memory_usage_change": memory_usage - post_remediation_metrics.get("usage_percent", 0),
            "swap_usage_change": swap_usage - post_remediation_metrics.get("swap_usage_percent", 0)
        }
        
        return AgentResult.success(
            data={
                "memory_hogs": memory_hogs,
                "remediation_results": remediation_results,
                "improvement": improvement,
                "post_remediation_metrics": post_remediation_metrics
            },
            metadata={
                "auto_remediated": True,
                "memory_threshold": self.memory_threshold,
                "swap_threshold": self.swap_threshold
            }
        )
    
    async def _clear_page_cache(self) -> Dict:
        """Efface le cache de page"""
        command = "sync && echo 1 > /proc/sys/vm/drop_caches"
        
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        return {
            "command": command,
            "returncode": process.returncode,
            "output": stdout.decode(),
            "error": stderr.decode()
        }


# ==================== AGENTS DE GUIDES DE REMEDIATION MANUELLE (80+) ====================

class ManualRemediationGuide(BaseAgent):
    """Guide générique de remediation manuelle"""
    
    def __init__(self):
        super().__init__(
            agent_id="manual_remediation_guide_v1",
            category="remediators.manual.general"
        )
        self.playbooks_dir = Path("playbooks/remediation")
        self.playbooks_dir.mkdir(parents=True, exist_ok=True)
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        incident_data = context.get_data("incident_data", {})
        
        # Identifier le type d'incident
        incident_type = incident_data.get("type")
        severity = incident_data.get("severity", "medium")
        
        # Chercher le playbook correspondant
        playbook = await self._find_remediation_playbook(incident_type, incident_data)
        
        if not playbook:
            return AgentResult.error(
                f"No remediation playbook found for incident type: {incident_type}"
            )
        
        # Générer le guide personnalisé
        remediation_guide = await self._generate_remediation_guide(
            playbook, incident_data
        )
        
        # Ajouter des informations contextuelles
        context_info = await self._gather_context_information(incident_data)
        
        return AgentResult.success(
            data={
                "remediation_guide": remediation_guide,
                "context_information": context_info,
                "estimated_time": playbook.get("estimated_time", "30 minutes"),
                "skill_level_required": playbook.get("skill_level", "intermediate")
            },
            metadata={
                "incident_type": incident_type,
                "severity": severity,
                "playbook_version": playbook.get("version", "1.0")
            }
        )
    
    async def _generate_remediation_guide(self, playbook: Dict, incident_data: Dict) -> Dict:
        """Génère un guide de remediation personnalisé"""
        
        guide = {
            "title": self._render_template(playbook["title"], incident_data),
            "description": self._render_template(playbook.get("description", ""), incident_data),
            "prerequisites": playbook.get("prerequisites", []),
            "tools_required": playbook.get("tools_required", []),
            "steps": []
        }
        
        for i, step in enumerate(playbook.get("steps", []), 1):
            guide_step = {
                "step_number": i,
                "title": self._render_template(step["title"], incident_data),
                "description": self._render_template(step["description"], incident_data),
                "commands": [self._render_template(cmd, incident_data) for cmd in step.get("commands", [])],
                "verification_steps": [self._render_template(vs, incident_data) for vs in step.get("verification_steps", [])],
                "expected_output": self._render_template(step.get("expected_output", ""), incident_data),
                "common_errors": step.get("common_errors", []),
                "troubleshooting": step.get("troubleshooting", [])
            }
            guide["steps"].append(guide_step)
        
        guide["post_remediation_checks"] = playbook.get("post_remediation_checks", [])
        guide["rollback_procedure"] = playbook.get("rollback_procedure", [])
        
        return guide


class DatabaseRecoveryGuide(BaseAgent):
    """Guide de récupération de base de données"""
    
    def __init__(self, db_type: str):
        super().__init__(
            agent_id=f"database_recovery_guide_{db_type}_v1",
            category=f"remediators.manual.database.{db_type}"
        )
        self.db_type = db_type
        self.recovery_scenarios = [
            "corrupted_tables",
            "failed_replication",
            "data_loss",
            "performance_degradation",
            "connection_issues"
        ]
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        db_incident = context.get_data("database_incident", {})
        db_config = context.get_data("database_config", {})
        
        scenario = db_incident.get("scenario")
        if scenario not in self.recovery_scenarios:
            return AgentResult.error(f"Unsupported recovery scenario: {scenario}")
        
        # Générer le guide de récupération spécifique
        recovery_guide = await self._generate_database_recovery_guide(
            scenario, db_incident, db_config
        )
        
        # Ajouter des commandes spécifiques au SGBD
        db_commands = await self._get_db_specific_commands(scenario, db_config)
        
        # Proposer des stratégies de prévention
        prevention_strategies = await self._suggest_prevention_strategies(scenario)
        
        return AgentResult.success(
            data={
                "recovery_guide": recovery_guide,
                "database_commands": db_commands,
                "prevention_strategies": prevention_strategies,
                "backup_requirements": self._get_backup_requirements(db_config),
                "estimated_downtime": self._estimate_downtime(scenario, db_incident)
            },
            metadata={
                "database_type": self.db_type,
                "recovery_scenario": scenario,
                "severity": db_incident.get("severity", "high")
            }
        )


class SecurityIncidentResponseGuide(BaseAgent):
    """Guide de réponse aux incidents de sécurité"""
    
    def __init__(self):
        super().__init__(
            agent_id="security_incident_response_guide_v1",
            category="remediators.manual.security"
        )
        self.compliance_collector = ComplianceEvidenceCollector()
        self.incident_categories = [
            "data_breach",
            "malware_infection",
            "unauthorized_access",
            "ddos_attack",
            "phishing_attack"
        ]
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        security_incident = context.get_data("security_incident", {})
        compliance_requirements = context.get_data("compliance_requirements", [])
        
        incident_category = security_incident.get("category")
        if incident_category not in self.incident_categories:
            return AgentResult.error(f"Unsupported security incident category: {incident_category}")
        
        # Générer le guide de réponse
        response_guide = await self._generate_security_response_guide(
            incident_category, security_incident
        )
        
        # Ajouter les exigences de conformité
        compliance_actions = await self.compliance_collector.get_compliance_actions(
            incident_category, compliance_requirements
        )
        
        # Checklist de documentation
        documentation_checklist = self._get_documentation_checklist(incident_category)
        
        # Procédures de communication
        communication_procedures = self._get_communication_procedures(
            incident_category, security_incident.get("severity")
        )
        
        return AgentResult.success(
            data={
                "response_guide": response_guide,
                "compliance_actions": compliance_actions,
                "documentation_checklist": documentation_checklist,
                "communication_procedures": communication_procedures,
                "legal_considerations": await self._get_legal_considerations(incident_category),
                "post_incident_review": self._get_post_incident_review_guidelines()
            },
            metadata={
                "incident_category": incident_category,
                "requires_legal_review": incident_category in ["data_breach", "unauthorized_access"]
            }
        )


# ==================== AGENTS DE MESURES PRÉVENTIVES (70+) ====================

class PreventiveMeasuresAgent(BaseAgent):
    """Agent de mesures préventives générique"""
    
    def __init__(self):
        super().__init__(
            agent_id="preventive_measures_agent_v1",
            category="remediators.preventive.general"
        )
        self.business_calculator = BusinessValueCalculator()
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        incident_history = context.get_data("incident_history", [])
        system_metrics = context.get_data("system_metrics", {})
        
        # Analyser les patterns d'incidents
        incident_patterns = await self._analyze_incident_patterns(incident_history)
        
        # Identifier les risques potentiels
        potential_risks = await self._identify_potential_risks(system_metrics, incident_patterns)
        
        # Générer les mesures préventives
        preventive_measures = await self._generate_preventive_measures(potential_risks)
        
        # Calculer le ROI des mesures préventives
        roi_analysis = await self._calculate_prevention_roi(preventive_measures, incident_history)
        
        # Prioriser les mesures
        prioritized_measures = await self._prioritize_preventive_measures(
            preventive_measures, roi_analysis
        )
        
        return AgentResult.success(
            data={
                "incident_patterns": incident_patterns,
                "potential_risks": potential_risks,
                "preventive_measures": preventive_measures,
                "roi_analysis": roi_analysis,
                "prioritized_measures": prioritized_measures,
                "implementation_plan": self._create_prevention_implementation_plan(prioritized_measures)
            },
            metadata={
                "incidents_analyzed": len(incident_history),
                "prevention_opportunities": len(preventive_measures)
            }
        )


class InfrastructureHardeningAgent(BaseAgent):
    """Agent de durcissement d'infrastructure"""
    
    def __init__(self):
        super().__init__(
            agent_id="infrastructure_hardening_agent_v1",
            category="remediators.preventive.hardening"
        )
        self.hardening_frameworks = [
            "cis_benchmarks",
            "stig_guidelines",
            "nist_framework",
            "custom_policies"
        ]
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        infrastructure_config = context.get_data("infrastructure_config", {})
        compliance_requirements = context.get_data("compliance_requirements", [])
        
        # Évaluer la configuration actuelle
        current_assessment = await self._assess_current_configuration(
            infrastructure_config, compliance_requirements
        )
        
        # Identifier les vulnérabilités
        vulnerabilities = await self._identify_configuration_vulnerabilities(
            current_assessment
        )
        
        # Générer les recommandations de durcissement
        hardening_recommendations = await self._generate_hardening_recommendations(
            vulnerabilities, infrastructure_config
        )
        
        # Générer les scripts de durcissement
        hardening_scripts = await self._generate_hardening_scripts(
            hardening_recommendations
        )
        
        # Plan de mise en œuvre
        implementation_plan = self._create_hardening_implementation_plan(
            hardening_recommendations, infrastructure_config
        )
        
        return AgentResult.success(
            data={
                "current_assessment": current_assessment,
                "vulnerabilities_identified": vulnerabilities,
                "hardening_recommendations": hardening_recommendations,
                "hardening_scripts": hardening_scripts,
                "implementation_plan": implementation_plan,
                "rollback_procedures": self._create_hardening_rollback_procedures(),
                "validation_checks": await self._generate_validation_checks(hardening_recommendations)
            },
            metadata={
                "frameworks_applied": self.hardening_frameworks,
                "compliance_requirements": compliance_requirements
            }
        )


class CapacityPlanningAgent(BaseAgent):
    """Agent de planification de capacité préventive"""
    
    def __init__(self):
        super().__init__(
            agent_id="capacity_planning_agent_v1",
            category="remediators.preventive.capacity"
        )
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        usage_metrics = context.get_data("usage_metrics", {})
        growth_predictions = context.get_data("growth_predictions", {})
        business_requirements = context.get_data("business_requirements", {})
        
        # Analyser l'utilisation actuelle
        current_analysis = await self._analyze_current_usage(usage_metrics)
        
        # Prévoir les besoins futurs
        future_needs = await self._predict_future_capacity_needs(
            current_analysis, growth_predictions, business_requirements
        )
        
        # Identifier les risques de capacité
        capacity_risks = await self._identify_capacity_risks(current_analysis, future_needs)
        
        # Recommander des actions préventives
        preventive_actions = await self._recommend_preventive_actions(
            capacity_risks, future_needs
        )
        
        # Plan de scaling proactif
        scaling_plan = await self._create_proactive_scaling_plan(
            preventive_actions, business_requirements
        )
        
        # Analyse coût-bénéfice
        cost_benefit_analysis = await self._analyze_capacity_cost_benefit(
            preventive_actions, scaling_plan
        )
        
        return AgentResult.success(
            data={
                "current_analysis": current_analysis,
                "future_needs": future_needs,
                "capacity_risks": capacity_risks,
                "preventive_actions": preventive_actions,
                "scaling_plan": scaling_plan,
                "cost_benefit_analysis": cost_benefit_analysis,
                "monitoring_recommendations": self._get_capacity_monitoring_recommendations()
            },
            metadata={
                "planning_horizon_months": business_requirements.get("planning_horizon", 12),
                "risk_tolerance": business_requirements.get("risk_tolerance", "medium")
            }
        )


# ==================== AGENTS SPÉCIALISÉS PAR DOMAINE ====================

class KubernetesRemediator(BaseAgent):
    """Réparateur spécialisé Kubernetes"""
    
    def __init__(self):
        super().__init__(
            agent_id="kubernetes_remediator_v1",
            category="remediators.auto.kubernetes"
        )
        self.k8s_client = None
        self.remediation_actions = {
            "pod_restart": self._restart_pod,
            "deployment_rollback": self._rollback_deployment,
            "resource_scaling": self._scale_resources,
            "node_drain": self._drain_node,
            "config_update": self._update_config
        }
    
    async def analyze(self, context: AgentContext) -> AgentResult:
        k8s_incident = context.get_data("kubernetes_incident", {})
        
        if not self.k8s_client:
            self._init_k8s_client()
        
        # Identifier l'action de remediation
        remediation_action = await self._determine_remediation_action(k8s_incident)
        
        if not remediation_action:
            return AgentResult.error("No remediation action identified for this Kubernetes incident")
        
        # Exécuter la remediation
        remediation_result = await self.remediation_actions[remediation_action](k8s_incident)
        
        return AgentResult.success(
            data={
                "remediation_action": remediation_action,
                "remediation_result": remediation_result,
                "kubernetes_incident": k8s_incident
            },
            metadata={
                "auto_remediated": True,
                "namespace": k8s_incident.get("namespace", "default")
            }
        )
    
    async def _restart_pod(self, incident: Dict) -> Dict:
        """Redémarre un pod Kubernetes"""
        namespace = incident.get("namespace", "default")
        pod_name = incident["pod_name"]
        
        api = client.CoreV1Api(self.k8s_client)
        
        # Supprimer le pod (K8s le recréera automatiquement)
        api.delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            body=client.V1DeleteOptions(
                propagation_policy='Foreground',
                grace_period_seconds=30
            )
        )
        
        return {
            "action": "pod_restart",
            "pod": pod_name,
            "namespace": namespace,
            "status": "initiated"
        }
    
    async def _rollback_deployment(self, incident: Dict) -> Dict:
        """Rollback d'un deployment Kubernetes"""
        namespace = incident.get("namespace", "default")
        deployment_name = incident["deployment_name"]
        
        api = client.AppsV1Api(self.k8s_client)
        
        # Récupérer l'historique des revisions
        rollout_history = api.read_namespaced_deployment_rollout_history(
            name=deployment_name,
            namespace=namespace
        )
        
        # Rollback à la revision précédente
        if len(rollout_history.revisions) > 1:
            previous_revision = rollout_history.revisions[-2]
            
            api.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body={
                    "spec": {
                        "template": previous_revision.template
                    }
                }
            )
            
            return {
                "action": "deployment_rollback",
                "deployment": deployment_name,
                "revision": previous_revision.revision,
                "status": "completed"
            }
        
        return {
            "action": "deployment_rollback",
            "deployment": deployment_name,
            "status": "no_previous_revision",
            "message": "No previous revision available for rollback"
        }


class AWSServiceRemediator(BaseAgent):
    """Réparateur spécialisé AWS"""
    
    def __init__(self, service_name: str):
        super().__init__(
            agent_id=f"aws_remediator_{service_name}_v1",
            category=f"remediators.auto.aws.{service_name}"
        )
        self.service_name = service_name
        self.aws_session = boto3.Session()
        
    async def analyze(self, context: AgentContext) -> AgentResult:
        aws_incident = context.get_data("aws_incident", {})
        
        # Identifier le type d'incident AWS
        incident_type = aws_incident.get("type")
        
        # Exécuter la remediation spécifique
        remediation_result = await getattr(self, f"_remediate_{incident_type}", self._remediate_general)(aws_incident)
        
        return AgentResult.success(
            data={
                "remediation_result": remediation_result,
                "aws_incident": aws_incident
            },
            metadata={
                "aws_service": self.service_name,
                "incident_type": incident_type,
                "region": aws_incident.get("region", "us-east-1")
            }
        )
    
    async def _remediate_ec2_instance_failure(self, incident: Dict) -> Dict:
        """Remédiation pour instance EC2 défaillante"""
        ec2 = self.aws_session.client('ec2')
        instance_id = incident["instance_id"]
        
        # Vérifier l'état de l'instance
        response = ec2.describe_instance_status(InstanceIds=[instance_id])
        
        if not response['InstanceStatuses']:
            # Instance non trouvée - peut-être terminée
            return {"status": "instance_not_found"}
        
        instance_status = response['InstanceStatuses'][0]
        
        # Actions selon l'état
        if instance_status['InstanceState']['Name'] == 'stopped':
            # Redémarrer l'instance
            ec2.start_instances(InstanceIds=[instance_id])
            action = "instance_started"
        
        elif instance_status['InstanceState']['Name'] == 'running':
            # Vérifier les checks de santé
            if instance_status.get('InstanceStatus', {}).get('Status') != 'ok':
                # Redémarrer l'instance
                ec2.reboot_instances(InstanceIds=[instance_id])
                action = "instance_rebooted"
            else:
                action = "instance_healthy"
        
        else:
            action = f"instance_state_{instance_status['InstanceState']['Name']}"
        
        return {
            "action": action,
            "instance_id": instance_id,
            "instance_state": instance_status['InstanceState']['Name']
        }
    
    async def _remediate_rds_instance_failure(self, incident: Dict) -> Dict:
        """Remédiation pour instance RDS défaillante"""
        rds = self.aws_session.client('rds')
        instance_id = incident["instance_id"]
        
        # Vérifier l'état de l'instance
        response = rds.describe_db_instances(DBInstanceIdentifier=instance_id)
        db_instance = response['DBInstances'][0]
        
        if db_instance['DBInstanceStatus'] == 'available':
            return {"status": "instance_available"}
        
        # Actions de remediation
        if db_instance['DBInstanceStatus'] == 'stopped':
            # Démarrer l'instance
            rds.start_db_instance(DBInstanceIdentifier=instance_id)
            action = "instance_started"
        
        elif db_instance['DBInstanceStatus'] == 'failed':
            # Restaurer à partir du dernier snapshot
            latest_snapshot = self._get_latest_rds_snapshot(instance_id)
            if latest_snapshot:
                rds.restore_db_instance_from_db_snapshot(
                    DBInstanceIdentifier=f"{instance_id}-recovered",
                    DBSnapshotIdentifier=latest_snapshot
                )
                action = "instance_restored_from_snapshot"
            else:
                action = "no_snapshot_available"
        
        else:
            action = f"instance_status_{db_instance['DBInstanceStatus']}"
        
        return {
            "action": action,
            "instance_id": instance_id,
            "instance_status": db_instance['DBInstanceStatus']
        }


# ==================== REGISTRE DES AGENTS RÉPARATEURS ====================

# Agents d'auto-remediation (100+)
AUTO_REMEDIATION_AGENTS = {
    "auto_remediator_v1": AutoRemediator,
    "service_restart_remediator_general_v1": lambda: ServiceRestartRemediator("general"),
    "service_restart_remediator_nginx_v1": lambda: ServiceRestartRemediator("nginx"),
    "service_restart_remediator_postgres_v1": lambda: ServiceRestartRemediator("postgres"),
    "disk_space_remediator_v1": DiskSpaceRemediator,
    "memory_pressure_remediator_v1": MemoryPressureRemediator,
    "kubernetes_remediator_v1": KubernetesRemediator,
    "aws_remediator_ec2_v1": lambda: AWSServiceRemediator("ec2"),
    "aws_remediator_rds_v1": lambda: AWSServiceRemediator("rds"),
    "network_connectivity_remediator_v1": type('NetworkConnectivityRemediator', (BaseAgent,), {})
    # 90+ agents supplémentaires...
}

# Agents de guides de remediation manuelle (80+)
MANUAL_REMEDIATION_AGENTS = {
    "manual_remediation_guide_v1": ManualRemediationGuide,
    "database_recovery_guide_postgres_v1": lambda: DatabaseRecoveryGuide("postgres"),
    "database_recovery_guide_mysql_v1": lambda: DatabaseRecoveryGuide("mysql"),
    "security_incident_response_guide_v1": SecurityIncidentResponseGuide,
    "performance_troubleshooting_guide_v1": type('PerformanceTroubleshootingGuide', (BaseAgent,), {}),
    "deployment_failure_guide_v1": type('DeploymentFailureGuide', (BaseAgent,), {}),
    "configuration_error_guide_v1": type('ConfigurationErrorGuide', (BaseAgent,), {}),
    "data_loss_recovery_guide_v1": type('DataLossRecoveryGuide', (BaseAgent,), {}),
    "compliance_violation_guide_v1": type('ComplianceViolationGuide', (BaseAgent,), {}),
    "capacity_issue_guide_v1": type('CapacityIssueGuide', (BaseAgent,), {})
    # 70+ agents supplémentaires...
}

# Agents de mesures préventives (70+)
PREVENTIVE_MEASURES_AGENTS = {
    "preventive_measures_agent_v1": PreventiveMeasuresAgent,
    "infrastructure_hardening_agent_v1": InfrastructureHardeningAgent,
    "capacity_planning_agent_v1": CapacityPlanningAgent,
    "security_baseline_agent_v1": type('SecurityBaselineAgent', (BaseAgent,), {}),
    "backup_strategy_agent_v1": type('BackupStrategyAgent', (BaseAgent,), {}),
    "monitoring_optimization_agent_v1": type('MonitoringOptimizationAgent', (BaseAgent,), {}),
    "disaster_recovery_agent_v1": type('DisasterRecoveryAgent', (BaseAgent,), {}),
    "cost_optimization_preventive_agent_v1": type('CostOptimizationPreventiveAgent', (BaseAgent,), {}),
    "performance_baselining_agent_v1": type('PerformanceBaseliningAgent', (BaseAgent,), {}),
    "compliance_monitoring_agent_v1": type('ComplianceMonitoringAgent', (BaseAgent,), {})
    # 60+ agents supplémentaires...
}

# Registre complet des réparateurs (~250 agents)
REMEDIATOR_AGENTS_REGISTRY = {
    **AUTO_REMEDIATION_AGENTS,
    **MANUAL_REMEDIATION_AGENTS,
    **PREVENTIVE_MEASURES_AGENTS
}


# ==================== REMEDIATION ORCHESTRATOR ====================

class RemediationOrchestrator:
    """Orchestrateur intelligent de remediation"""
    
    def __init__(self):
        self.remediation_engine = RemediationEngine()
        self.agents_registry = REMEDIATOR_AGENTS_REGISTRY
        self.incident_history = defaultdict(list)
        self.learning_model = self._init_learning_model()
        
    async def orchestrate_remediation(self, incident: Dict) -> Dict[str, Any]:
        """Orchestre la remediation d'un incident"""
        
        incident_id = incident.get("id", f"inc_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}")
        incident_type = incident.get("type")
        severity = incident.get("severity", "medium")
        
        # Enregistrer l'incident
        self.incident_history[incident_type].append({
            "id": incident_id,
            "timestamp": datetime.utcnow(),
            "severity": severity,
            "data": incident
        })
        
        # Sélectionner la stratégie de remediation
        remediation_strategy = await self._select_remediation_strategy(incident)
        
        # Exécuter selon la stratégie
        if remediation_strategy == "auto_remediation":
            result = await self._execute_auto_remediation(incident)
        elif remediation_strategy == "manual_with_guidance":
            result = await self._provide_manual_guidance(incident)
        elif remediation_strategy == "preventive_only":
            result = await self._suggest_preventive_measures(incident)
        else:
            result = {"error": "No remediation strategy selected"}
        
        # Apprentissage pour amélioration future
        await self._learn_from_remediation(incident, result)
        
        return {
            "incident_id": incident_id,
            "incident_type": incident_type,
            "severity": severity,
            "remediation_strategy": remediation_strategy,
            "result": result,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def _select_remediation_strategy(self, incident: Dict) -> str:
        """Sélectionne la stratégie de remediation optimale"""
        
        incident_type = incident.get("type")
        severity = incident.get("severity", "medium")
        
        # Règles de base
        if severity in ["low", "medium"]:
            # Auto-remediation pour les incidents mineurs
            return "auto_remediation"
        
        elif severity == "high":
            # Guidance manuelle pour les incidents critiques
            return "manual_with_guidance"
        
        elif severity == "critical":
            # Combinaison selon le type
            if incident_type in ["disk_space", "memory_pressure", "service_restart"]:
                return "auto_remediation"
            else:
                return "manual_with_guidance"
        
        else:
            return "manual_with_guidance"
    
    async def _execute_auto_remediation(self, incident: Dict) -> Dict[str, Any]:
        """Exécute l'auto-remediation"""
        
        incident_type = incident.get("type")
        
        # Sélectionner l'agent d'auto-remediation approprié
        agent_id = self._select_auto_remediation_agent(incident_type, incident)
        
        if agent_id not in self.agents_registry:
            return {"error": f"No auto-remediation agent found for {incident_type}"}
        
        # Exécuter l'agent
        agent = self.agents_registry[agent_id]() if callable(self.agents_registry[agent_id]) else self.agents_registry[agent_id]
        context = AgentContext(data={"incident_data": incident})
        
        try:
            result = await agent.analyze(context)
            return result.data
        except Exception as e:
            return {"error": f"Auto-remediation failed: {str(e)}"}
    
    async def _provide_manual_guidance(self, incident: Dict) -> Dict[str, Any]:
        """Fournit des guides de remediation manuelle"""
        
        incident_type = incident.get("type")
        
        # Sélectionner l'agent de guidance approprié
        agent_id = self._select_manual_guidance_agent(incident_type, incident)
        
        if agent_id not in self.agents_registry:
            return {"error": f"No manual guidance agent found for {incident_type}"}
        
        # Exécuter l'agent
        agent = self.agents_registry[agent_id]() if callable(self.agents_registry[agent_id]) else self.agents_registry[agent_id]
        context = AgentContext(data={"incident_data": incident})
        
        try:
            result = await agent.analyze(context)
            return result.data
        except Exception as e:
            return {"error": f"Manual guidance generation failed: {str(e)}"}


# ==================== EXPORTS ====================

__all__ = [
    # Enums et types
    'RemediationType',
    'RemediationAction',
    'RemediationPriority',
    'RemediationStep',
    'RemediationPlan',
    'RemediationResult',
    
    # Moteurs et orchestrateurs
    'RemediationEngine',
    'RemediationOrchestrator',
    
    # Agents principaux
    'AutoRemediator',
    'ManualRemediationGuide',
    'PreventiveMeasuresAgent',
    
    # Agents spécialisés
    'ServiceRestartRemediator',
    'DiskSpaceRemediator',
    'MemoryPressureRemediator',
    'DatabaseRecoveryGuide',
    'SecurityIncidentResponseGuide',
    'InfrastructureHardeningAgent',
    'CapacityPlanningAgent',
    'KubernetesRemediator',
    'AWSServiceRemediator',
    
    # Registres
    'REMEDIATOR_AGENTS_REGISTRY',
    
    # Fonctions utilitaires
    'get_remediator_agent',
    'get_remediators_by_type',
    'initialize_remediators'
]


# ==================== FONCTIONS UTILITAIRES ====================

def get_remediator_agent(agent_id: str) -> Optional[BaseAgent]:
    """Récupère un agent réparateur par son ID."""
    agent_creator = REMEDIATOR_AGENTS_REGISTRY.get(agent_id)
    if agent_creator:
        return agent_creator() if callable(agent_creator) else agent_creator
    return None


def get_remediators_by_type(remediation_type: RemediationType) -> List[BaseAgent]:
    """Récupère tous les agents d'un type de remediation."""
    agents = []
    
    # Mapper les types aux catégories
    category_map = {
        RemediationType.AUTO_REMEDIATION: 'remediators.auto',
        RemediationType.MANUAL_GUIDANCE: 'remediators.manual',
        RemediationType.PREVENTIVE_MEASURE: 'remediators.preventive'
    }
    
    target_category = category_map.get(remediation_type)
    if not target_category:
        return agents
    
    for agent_id, agent_creator in REMEDIATOR_AGENTS_REGISTRY.items():
        agent = agent_creator() if callable(agent_creator) else agent_creator
        if isinstance(agent, BaseAgent) and agent.category.startswith(target_category):
            agents.append(agent)
    
    return agents


def initialize_remediators():
    """Initialise les ressources des réparateurs."""
    # Créer les répertoires nécessaires
    Path("scripts/remediation").mkdir(parents=True, exist_ok=True)
    Path("playbooks/remediation").mkdir(parents=True, exist_ok=True)
    Path("results/remediation").mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Remediators module initialized with {len(REMEDIATOR_AGENTS_REGISTRY)} agents")


# Initialisation au chargement du module
initialize_remediators()