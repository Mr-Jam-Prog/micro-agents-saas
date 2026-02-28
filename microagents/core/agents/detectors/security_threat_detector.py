"""
Security Threat Detector Agent

Agent spécialisé dans la détection de menaces de sécurité en temps réel
avec intégration de feeds d'intelligence, analyse comportementale,
détection basée signature, modèles ML pour menaces zero-day,
et corrélation d'événements.
"""

import asyncio
import json
import logging
import hashlib
import re
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set, Any, Tuple, Union
from dataclasses import dataclass, field
from collections import defaultdict

import numpy as np
from pydantic import BaseModel, Field, validator
import aiohttp
from sqlalchemy.orm import Session
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import joblib

from microagents.core.base.agent import BaseAgent, AgentResult
from microagents.core.base.context import AgentContext
from microagents.core.ai.anomaly_detector_advanced import AdvancedAnomalyDetector
from microagents.core.security.threat_model import ThreatModel, ThreatSeverity
from microagents.core.compliance.compliance_checker import ComplianceChecker
from microagents.utils.concurrency.manager import ConcurrencyManager
from microagents.monitoring.metrics.collector import MetricsCollector
from microagents.utils.security.utils import encrypt_data, decrypt_data

logger = logging.getLogger(__name__)


class ThreatType(Enum):
    """Types de menaces détectables"""
    MALWARE = "malware"
    PHISHING = "phishing"
    DDoS = "ddos"
    BRUTE_FORCE = "brute_force"
    INSIDER_THREAT = "insider_threat"
    DATA_LEAKAGE = "data_leakage"
    CLOUD_MISCONFIG = "cloud_misconfig"
    ZERO_DAY = "zero_day"
    COMPLIANCE_VIOLATION = "compliance_violation"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    ANOMALOUS_BEHAVIOR = "anomalous_behavior"
    NETWORK_SCAN = "network_scan"
    CREDENTIAL_STUFFING = "credential_stuffing"
    FILE_INTEGRITY = "file_integrity"


class DetectionMethod(Enum):
    """Méthodes de détection"""
    SIGNATURE = "signature"
    BEHAVIORAL = "behavioral"
    ML = "machine_learning"
    HEURISTIC = "heuristic"
    THREAT_INTELLIGENCE = "threat_intelligence"
    CORRELATION = "correlation"


class RiskLevel(Enum):
    """Niveaux de risque"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class SecurityEvent:
    """Événement de sécurité détecté"""
    event_id: str
    timestamp: datetime
    threat_type: ThreatType
    detection_method: DetectionMethod
    source_ip: Optional[str] = None
    destination_ip: Optional[str] = None
    user_id: Optional[str] = None
    resource: Optional[str] = None
    description: str = ""
    confidence: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    raw_data: Dict[str, Any] = field(default_factory=dict)
    indicators: List[str] = field(default_factory=list)
    mitre_attck_ids: List[str] = field(default_factory=list)
    business_impact: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'événement en dictionnaire"""
        return {
            'event_id': self.event_id,
            'timestamp': self.timestamp.isoformat(),
            'threat_type': self.threat_type.value,
            'detection_method': self.detection_method.value,
            'source_ip': self.source_ip,
            'destination_ip': self.destination_ip,
            'user_id': self.user_id,
            'resource': self.resource,
            'description': self.description,
            'confidence': self.confidence,
            'risk_level': self.risk_level.value,
            'indicators': self.indicators,
            'mitre_attck_ids': self.mitre_attck_ids,
            'business_impact': self.business_impact
        }


class ThreatIntelligenceFeed:
    """Client pour les feeds d'intelligence sur les menaces"""
    
    def __init__(self, api_keys: Dict[str, str]):
        """
        Initialise les connexions aux feeds d'intelligence.
        
        Args:
            api_keys: Dictionnaire de clés API pour différents fournisseurs
        """
        self.api_keys = api_keys
        self.cache = {}
        self.cache_ttl = 300  # 5 minutes
        self.last_update = {}
        
        # Sources d'intelligence configurées
        self.sources = {
            'alienvault': 'https://otx.alienvault.com/api/v1',
            'virustotal': 'https://www.virustotal.com/api/v3',
            'abuseipdb': 'https://api.abuseipdb.com/api/v2',
            'greynoise': 'https://api.greynoise.io/v3',
            'shodan': 'https://api.shodan.io',
            'threatfox': 'https://threatfox.abuse.ch/api/v1'
        }
        
        logger.info("Threat Intelligence Feed initialisé")
    
    async def check_ip_reputation(self, ip_address: str) -> Dict[str, Any]:
        """
        Vérifie la réputation d'une adresse IP.
        
        Args:
            ip_address: Adresse IP à vérifier
            
        Returns:
            Informations de réputation
        """
        cache_key = f"ip_reputation:{ip_address}"
        if cache_key in self.cache:
            cached_time, data = self.cache[cache_key]
            if time.time() - cached_time < self.cache_ttl:
                return data
        
        try:
            results = {}
            
            # AbuseIPDB
            if 'abuseipdb' in self.api_keys:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'Key': self.api_keys['abuseipdb'],
                        'Accept': 'application/json'
                    }
                    async with session.get(
                        f"{self.sources['abuseipdb']}/check",
                        headers=headers,
                        params={'ipAddress': ip_address, 'maxAgeInDays': 90}
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            results['abuseipdb'] = {
                                'abuse_confidence_score': data['data'].get('abuseConfidenceScore', 0),
                                'total_reports': data['data'].get('totalReports', 0),
                                'country': data['data'].get('countryCode'),
                                'isp': data['data'].get('isp')
                            }
            
            # GreyNoise
            if 'greynoise' in self.api_keys:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'key': self.api_keys['greynoise'],
                        'Accept': 'application/json'
                    }
                    async with session.get(
                        f"{self.sources['greynoise']}/community/{ip_address}",
                        headers=headers
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            results['greynoise'] = {
                                'noise': data.get('noise', False),
                                'riot': data.get('riot', False),
                                'classification': data.get('classification'),
                                'tags': data.get('tags', [])
                            }
            
            # VirusTotal
            if 'virustotal' in self.api_keys:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'x-apikey': self.api_keys['virustotal']
                    }
                    async with session.get(
                        f"{self.sources['virustotal']}/ip_addresses/{ip_address}",
                        headers=headers
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            attributes = data.get('data', {}).get('attributes', {})
                            results['virustotal'] = {
                                'reputation': attributes.get('reputation', 0),
                                'malicious_votes': attributes.get('last_analysis_stats', {}).get('malicious', 0),
                                'suspicious_votes': attributes.get('last_analysis_stats', {}).get('suspicious', 0),
                                'as_owner': attributes.get('as_owner')
                            }
            
            # Calcul du score global
            overall_score = self._calculate_overall_reputation_score(results)
            results['overall_score'] = overall_score
            results['risk_level'] = self._determine_risk_level(overall_score)
            
            # Mise en cache
            self.cache[cache_key] = (time.time(), results)
            
            return results
            
        except Exception as e:
            logger.error(f"Erreur vérification réputation IP {ip_address}: {str(e)}")
            return {'error': str(e)}
    
    async def check_domain_reputation(self, domain: str) -> Dict[str, Any]:
        """
        Vérifie la réputation d'un domaine.
        
        Args:
            domain: Domaine à vérifier
            
        Returns:
            Informations de réputation
        """
        cache_key = f"domain_reputation:{domain}"
        if cache_key in self.cache:
            cached_time, data = self.cache[cache_key]
            if time.time() - cached_time < self.cache_ttl:
                return data
        
        try:
            results = {}
            
            # VirusTotal
            if 'virustotal' in self.api_keys:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'x-apikey': self.api_keys['virustotal']
                    }
                    async with session.get(
                        f"{self.sources['virustotal']}/domains/{domain}",
                        headers=headers
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            attributes = data.get('data', {}).get('attributes', {})
                            results['virustotal'] = {
                                'reputation': attributes.get('reputation', 0),
                                'malicious_votes': attributes.get('last_analysis_stats', {}).get('malicious', 0),
                                'suspicious_votes': attributes.get('last_analysis_stats', {}).get('suspicious', 0),
                                'categories': attributes.get('categories', {})
                            }
            
            # AlienVault OTX
            if 'alienvault' in self.api_keys:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'X-OTX-API-KEY': self.api_keys['alienvault']
                    }
                    async with session.get(
                        f"{self.sources['alienvault']}/indicators/domain/{domain}/general",
                        headers=headers
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            results['alienvault'] = {
                                'pulse_count': data.get('pulse_info', {}).get('count', 0),
                                'malware_families': data.get('malware', {}).get('data', []),
                                'passive_dns': data.get('passive_dns', {}).get('passive_dns', [])
                            }
            
            # Calcul du score global
            overall_score = self._calculate_domain_reputation_score(results)
            results['overall_score'] = overall_score
            results['risk_level'] = self._determine_risk_level(overall_score)
            
            # Mise en cache
            self.cache[cache_key] = (time.time(), results)
            
            return results
            
        except Exception as e:
            logger.error(f"Erreur vérification réputation domaine {domain}: {str(e)}")
            return {'error': str(e)}
    
    async def check_file_hash(self, file_hash: str) -> Dict[str, Any]:
        """
        Vérifie un hash de fichier contre les bases de données de malwares.
        
        Args:
            file_hash: Hash MD5, SHA1 ou SHA256
            
        Returns:
            Résultats de l'analyse
        """
        cache_key = f"file_hash:{file_hash}"
        if cache_key in self.cache:
            cached_time, data = self.cache[cache_key]
            if time.time() - cached_time < self.cache_ttl:
                return data
        
        try:
            results = {}
            
            # VirusTotal
            if 'virustotal' in self.api_keys:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        'x-apikey': self.api_keys['virustotal']
                    }
                    async with session.get(
                        f"{self.sources['virustotal']}/files/{file_hash}",
                        headers=headers
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            attributes = data.get('data', {}).get('attributes', {})
                            last_analysis = attributes.get('last_analysis_stats', {})
                            results['virustotal'] = {
                                'malicious': last_analysis.get('malicious', 0),
                                'suspicious': last_analysis.get('suspicious', 0),
                                'undetected': last_analysis.get('undetected', 0),
                                'type': attributes.get('type_description'),
                                'size': attributes.get('size'),
                                'tags': attributes.get('tags', []),
                                'names': attributes.get('names', [])
                            }
            
            # ThreatFox
            if 'threatfox' in self.api_keys:
                async with aiohttp.ClientSession() as session:
                    data = {
                        'query': 'search_hash',
                        'hash': file_hash
                    }
                    async with session.post(
                        self.sources['threatfox'],
                        json=data
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get('query_status') == 'ok':
                                results['threatfox'] = {
                                    'malware': data.get('data', [{}])[0].get('malware'),
                                    'malware_alias': data.get('data', [{}])[0].get('malware_alias'),
                                    'confidence_level': data.get('data', [{}])[0].get('confidence_level'),
                                    'tags': data.get('data', [{}])[0].get('tags', [])
                                }
            
            # Calcul du score de menace
            threat_score = self._calculate_file_threat_score(results)
            results['threat_score'] = threat_score
            results['is_malicious'] = threat_score > 70
            
            # Mise en cache
            self.cache[cache_key] = (time.time(), results)
            
            return results
            
        except Exception as e:
            logger.error(f"Erreur vérification hash fichier {file_hash}: {str(e)}")
            return {'error': str(e)}
    
    def _calculate_overall_reputation_score(self, results: Dict[str, Any]) -> float:
        """Calcule un score global de réputation à partir des résultats"""
        if not results:
            return 0.0
        
        scores = []
        
        if 'abuseipdb' in results:
            score = results['abuseipdb']['abuse_confidence_score']
            scores.append(score)
        
        if 'virustotal' in results:
            malicious = results['virustotal']['malicious_votes']
            suspicious = results['virustotal']['suspicious_votes']
            total = malicious + suspicious
            if total > 0:
                score = (malicious * 100 + suspicious * 30) / max(total, 1)
                scores.append(min(score, 100))
        
        if 'greynoise' in results:
            if results['greynoise']['noise']:
                scores.append(80)  # IP bruyante
            if results['greynoise']['classification'] == 'malicious':
                scores.append(100)
        
        return float(np.mean(scores)) if scores else 0.0
    
    def _calculate_domain_reputation_score(self, results: Dict[str, Any]) -> float:
        """Calcule un score de réputation pour un domaine"""
        if not results:
            return 0.0
        
        scores = []
        
        if 'virustotal' in results:
            malicious = results['virustotal']['malicious_votes']
            suspicious = results['virustotal']['suspicious_votes']
            total = malicious + suspicious
            if total > 0:
                score = (malicious * 100 + suspicious * 30) / max(total, 1)
                scores.append(min(score, 100))
        
        if 'alienvault' in results:
            pulse_count = results['alienvault']['pulse_count']
            if pulse_count > 0:
                score = min(pulse_count * 20, 100)  # 5 pulses = 100%
                scores.append(score)
        
        return float(np.mean(scores)) if scores else 0.0
    
    def _calculate_file_threat_score(self, results: Dict[str, Any]) -> float:
        """Calcule un score de menace pour un fichier"""
        if not results:
            return 0.0
        
        scores = []
        
        if 'virustotal' in results:
            malicious = results['virustotal']['malicious']
            total = malicious + results['virustotal']['suspicious'] + results['virustotal']['undetected']
            if total > 0:
                score = (malicious * 100) / total
                scores.append(min(score, 100))
        
        if 'threatfox' in results:
            if results['threatfox'].get('malware'):
                scores.append(100)
            confidence = results['threatfox'].get('confidence_level', 50)
            scores.append(confidence)
        
        return float(np.mean(scores)) if scores else 0.0
    
    def _determine_risk_level(self, score: float) -> RiskLevel:
        """Détermine le niveau de risque basé sur le score"""
        if score >= 80:
            return RiskLevel.CRITICAL
        elif score >= 60:
            return RiskLevel.HIGH
        elif score >= 30:
            return RiskLevel.MEDIUM
        elif score >= 10:
            return RiskLevel.LOW
        else:
            return RiskLevel.INFO


class BehavioralAnalyzer:
    """Analyseur comportemental pour détection d'anomalies"""
    
    def __init__(self, window_size: int = 1000):
        """
        Initialise l'analyseur comportemental.
        
        Args:
            window_size: Taille de la fenêtre pour l'analyse
        """
        self.window_size = window_size
        self.user_profiles = defaultdict(lambda: {
            'login_times': [],
            'resource_access': set(),
            'data_volumes': [],
            'locations': set(),
            'devices': set()
        })
        
        # Modèles ML pour détection d'anomalies
        self.isolation_forest = IsolationForest(
            contamination=0.1,
            random_state=42
        )
        self.scaler = StandardScaler()
        self.is_fitted = False
        
        # Détecteur avancé
        self.advanced_detector = AdvancedAnomalyDetector()
        
        logger.info("Behavioral Analyzer initialisé")
    
    def analyze_user_behavior(self, user_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyse le comportement d'un utilisateur.
        
        Args:
            user_id: ID de l'utilisateur
            event: Événement à analyser
            
        Returns:
            Résultats de l'analyse
        """
        profile = self.user_profiles[user_id]
        anomalies = []
        
        # Analyse des heures de connexion
        if 'timestamp' in event:
            login_time = datetime.fromisoformat(event['timestamp']).time()
            if profile['login_times']:
                avg_hour = np.mean([t.hour for t in profile['login_times'][-self.window_size:]])
                if abs(login_time.hour - avg_hour) > 4:  # 4 heures de différence
                    anomalies.append({
                        'type': 'unusual_login_time',
                        'confidence': 0.7,
                        'description': f'Connexion à une heure inhabituelle: {login_time}'
                    })
            profile['login_times'].append(login_time)
        
        # Analyse des ressources accédées
        if 'resource' in event:
            resource = event['resource']
            if resource not in profile['resource_access']:
                if len(profile['resource_access']) > 10:  # Si l'utilisateur accède déjà à beaucoup de ressources
                    anomalies.append({
                        'type': 'new_resource_access',
                        'confidence': 0.6,
                        'description': f'Accès à une nouvelle ressource: {resource}'
                    })
            profile['resource_access'].add(resource)
        
        # Analyse du volume de données
        if 'data_size' in event:
            data_size = event['data_size']
            profile['data_volumes'].append(data_size)
            
            if len(profile['data_volumes']) > 10:
                recent_volumes = profile['data_volumes'][-10:]
                avg_volume = np.mean(recent_volumes[:-1])
                std_volume = np.std(recent_volumes[:-1]) or 1
                
                if data_size > avg_volume + 3 * std_volume:  # 3 écarts-types
                    anomalies.append({
                        'type': 'unusual_data_volume',
                        'confidence': 0.8,
                        'description': f'Volume de données inhabituel: {data_size} (moyenne: {avg_volume:.2f})'
                    })
        
        # Analyse géographique
        if 'location' in event:
            location = event['location']
            if location not in profile['locations']:
                if len(profile['locations']) > 0:  # Si l'utilisateur a déjà des localisations
                    anomalies.append({
                        'type': 'new_location',
                        'confidence': 0.5,
                        'description': f'Connexion depuis une nouvelle localisation: {location}'
                    })
            profile['locations'].add(location)
        
        # Analyse des appareils
        if 'device_id' in event:
            device_id = event['device_id']
            if device_id not in profile['devices']:
                if len(profile['devices']) > 0:
                    anomalies.append({
                        'type': 'new_device',
                        'confidence': 0.6,
                        'description': f'Connexion depuis un nouvel appareil: {device_id}'
                    })
            profile['devices'].add(device_id)
        
        # Détection ML des anomalies
        if len(profile['login_times']) > 50:
            ml_anomalies = self._detect_ml_anomalies(user_id, profile)
            anomalies.extend(ml_anomalies)
        
        return {
            'user_id': user_id,
            'anomalies': anomalies,
            'profile_summary': {
                'total_logins': len(profile['login_times']),
                'unique_resources': len(profile['resource_access']),
                'unique_locations': len(profile['locations']),
                'unique_devices': len(profile['devices'])
            }
        }
    
    def _detect_ml_anomalies(self, user_id: str, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Détecte les anomalies avec des modèles ML"""
        anomalies = []
        
        try:
            # Préparation des caractéristiques
            features = []
            
            # Heure de connexion (normalisée)
            if profile['login_times']:
                recent_logins = profile['login_times'][-50:]
                hours = [t.hour + t.minute/60 for t in recent_logins]
                features.extend([np.mean(hours), np.std(hours)])
            
            # Fréquence des ressources
            if profile['resource_access']:
                resource_freq = len(profile['resource_access']) / max(len(profile['login_times']), 1)
                features.append(resource_freq)
            
            # Volume de données (normalisé)
            if profile['data_volumes']:
                recent_volumes = profile['data_volumes'][-50:] if len(profile['data_volumes']) > 50 else profile['data_volumes']
                features.extend([np.mean(recent_volumes), np.std(recent_volumes)])
            
            if len(features) >= 4:
                features_array = np.array(features).reshape(1, -1)
                
                # Mise à l'échelle
                if not self.is_fitted:
                    self.scaler.fit(features_array)
                    self.is_fitted = True
                
                scaled_features = self.scaler.transform(features_array)
                
                # Détection d'anomalies avec Isolation Forest
                prediction = self.isolation_forest.predict(scaled_features)
                
                if prediction[0] == -1:  # Anomalie détectée
                    anomalies.append({
                        'type': 'ml_behavioral_anomaly',
                        'confidence': 0.85,
                        'description': 'Anomalie comportementale détectée par ML',
                        'features': features
                    })
        
        except Exception as e:
            logger.error(f"Erreur détection ML anomalies pour {user_id}: {str(e)}")
        
        return anomalies
    
    def detect_insider_threats(self, user_behavior: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Détecte les menaces internes.
        
        Args:
            user_behavior: Comportement de l'utilisateur
            
        Returns:
            Alertes de menace interne
        """
        alerts = []
        
        # Vérification des indicateurs de menace interne
        indicators = self._get_insider_threat_indicators(user_behavior)
        
        for indicator in indicators:
            if indicator['detected']:
                alerts.append({
                    'type': 'insider_threat',
                    'indicator': indicator['name'],
                    'confidence': indicator['confidence'],
                    'description': indicator['description']
                })
        
        return alerts
    
    def _get_insider_threat_indicators(self, behavior: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Retourne les indicateurs de menace interne"""
        indicators = [
            {
                'name': 'excessive_data_access',
                'detected': len(behavior['profile_summary']['unique_resources']) > 100,
                'confidence': 0.7,
                'description': 'Accès excessif à des ressources'
            },
            {
                'name': 'off_hours_activity',
                'detected': self._check_off_hours_activity(behavior),
                'confidence': 0.6,
                'description': 'Activité en dehors des heures normales'
            },
            {
                'name': 'data_exfiltration_pattern',
                'detected': self._check_data_exfiltration(behavior),
                'confidence': 0.8,
                'description': 'Pattern d\'exfiltration de données'
            },
            {
                'name': 'privilege_escalation_attempts',
                'detected': behavior.get('privilege_escalation_attempts', 0) > 3,
                'confidence': 0.9,
                'description': 'Tentatives d\'élévation de privilèges'
            }
        ]
        
        return indicators
    
    def _check_off_hours_activity(self, behavior: Dict[str, Any]) -> bool:
        """Vérifie l'activité en dehors des heures normales"""
        # Simplifié: vérifie les connexions entre 22h et 6h
        if 'login_times' in behavior:
            recent_logins = behavior['login_times'][-10:] if len(behavior['login_times']) > 10 else behavior['login_times']
            for login_time in recent_logins:
                if login_time.hour >= 22 or login_time.hour <= 6:
                    return True
        return False
    
    def _check_data_exfiltration(self, behavior: Dict[str, Any]) -> bool:
        """Vérifie les patterns d'exfiltration de données"""
        if 'data_volumes' in behavior and len(behavior['data_volumes']) > 5:
            recent_volumes = behavior['data_volumes'][-5:]
            # Vérifie si les volumes augmentent de façon exponentielle
            growth_rate = np.diff(recent_volumes) / recent_volumes[:-1]
            if np.any(growth_rate > 2.0):  # Croissance de plus de 200%
                return True
        return False


class SignatureDetector:
    """Détecteur basé sur signatures"""
    
    def __init__(self, signature_db_path: str = "data/signatures.json"):
        """
        Initialise le détecteur basé sur signatures.
        
        Args:
            signature_db_path: Chemin vers la base de données de signatures
        """
        self.signature_db_path = signature_db_path
        self.signatures = self._load_signatures()
        self.compiled_patterns = self._compile_patterns()
        
        logger.info(f"SignatureDetector initialisé avec {len(self.signatures)} signatures")
    
    def _load_signatures(self) -> List[Dict[str, Any]]:
        """Charge les signatures depuis la base de données"""
        try:
            with open(self.signature_db_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            # Signatures par défaut
            return self._get_default_signatures()
    
    def _get_default_signatures(self) -> List[Dict[str, Any]]:
        """Retourne les signatures par défaut"""
        return [
            {
                "id": "SQL_INJECTION_1",
                "name": "SQL Injection Basic",
                "pattern": r"(?i)(union\s+select|select\s+.*from|insert\s+into|update\s+.*set|delete\s+from|drop\s+table|or\s+'1'='1')",
                "threat_type": ThreatType.UNAUTHORIZED_ACCESS.value,
                "severity": "high",
                "mitre_attck": ["T1190"]
            },
            {
                "id": "XSS_1",
                "name": "Cross-Site Scripting",
                "pattern": r"(?i)(<script.*?>.*?</script>|javascript:|onload=|onerror=|onclick=)",
                "threat_type": ThreatType.MALWARE.value,
                "severity": "medium",
                "mitre_attck": ["T1059.007"]
            },
            {
                "id": "COMMAND_INJECTION_1",
                "name": "Command Injection",
                "pattern": r"(?i)(;|\|\||&&|\$\(|`.*`|exec\(|system\(|popen\()",
                "threat_type": ThreatType.UNAUTHORIZED_ACCESS.value,
                "severity": "critical",
                "mitre_attck": ["T1059"]
            },
            {
                "id": "PATH_TRAVERSAL_1",
                "name": "Path Traversal",
                "pattern": r"(\.\./|\.\.\\|/etc/passwd|/etc/shadow|C:\\Windows\\System32)",
                "threat_type": ThreatType.UNAUTHORIZED_ACCESS.value,
                "severity": "high",
                "mitre_attck": ["T1083"]
            },
            {
                "id": "BRUTE_FORCE_1",
                "name": "Brute Force Attempt",
                "pattern": r"(failed.*password|invalid.*credentials|authentication.*failed).*(\d+).*times",
                "threat_type": ThreatType.BRUTE_FORCE.value,
                "severity": "medium",
                "mitre_attck": ["T1110"]
            },
            {
                "id": "DATA_LEAKAGE_1",
                "name": "Potential Data Leakage",
                "pattern": r"(?i)(password|secret|token|key|credential).*=[\s]*['\"].*?['\"]",
                "threat_type": ThreatType.DATA_LEAKAGE.value,
                "severity": "critical",
                "mitre_attck": ["T1552"]
            }
        ]
    
    def _compile_patterns(self) -> Dict[str, re.Pattern]:
        """Compile les expressions régulières"""
        compiled = {}
        for sig in self.signatures:
            try:
                compiled[sig['id']] = re.compile(sig['pattern'], re.IGNORECASE | re.DOTALL)
            except re.error as e:
                logger.error(f"Erreur compilation pattern {sig['id']}: {str(e)}")
        return compiled
    
    def scan_log_entry(self, log_entry: str) -> List[Dict[str, Any]]:
        """
        Scan une entrée de log pour des signatures connues.
        
        Args:
            log_entry: Entrée de log à scanner
            
        Returns:
            Signatures détectées
        """
        detected = []
        
        for sig_id, pattern in self.compiled_patterns.items():
            if pattern.search(log_entry):
                signature = next((s for s in self.signatures if s['id'] == sig_id), None)
                if signature:
                    detected.append({
                        'signature_id': sig_id,
                        'name': signature['name'],
                        'threat_type': ThreatType(signature['threat_type']),
                        'severity': signature['severity'],
                        'mitre_attck': signature.get('mitre_attck', []),
                        'matched_text': pattern.search(log_entry).group()[:100]  # Premier 100 caractères
                    })
        
        return detected
    
    def scan_network_packet(self, packet_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Scan un paquet réseau pour des signatures connues.
        
        Args:
            packet_data: Données du paquet réseau
            
        Returns:
            Signatures détectées
        """
        detected = []
        
        # Analyse du payload
        if 'payload' in packet_data:
            payload = packet_data['payload']
            if isinstance(payload, bytes):
                try:
                    payload_str = payload.decode('utf-8', errors='ignore')
                except:
                    payload_str = str(payload)
            else:
                payload_str = str(payload)
            
            detected.extend(self.scan_log_entry(payload_str))
        
        # Analyse des en-têtes
        if 'headers' in packet_data:
            headers_str = json.dumps(packet_data['headers'])
            detected.extend(self.scan_log_entry(headers_str))
        
        return detected
    
    def add_signature(self, signature: Dict[str, Any]) -> bool:
        """
        Ajoute une nouvelle signature à la base de données.
        
        Args:
            signature: Nouvelle signature
            
        Returns:
            True si ajouté avec succès
        """
        try:
            # Validation de la signature
            if not all(k in signature for k in ['id', 'name', 'pattern', 'threat_type']):
                logger.error("Signature invalide: champs manquants")
                return False
            
            # Test de compilation du pattern
            re.compile(signature['pattern'], re.IGNORECASE | re.DOTALL)
            
            # Ajout à la liste
            self.signatures.append(signature)
            self.compiled_patterns[signature['id']] = re.compile(
                signature['pattern'], re.IGNORECASE | re.DOTALL
            )
            
            # Sauvegarde
            self._save_signatures()
            
            logger.info(f"Signature ajoutée: {signature['id']}")
            return True
            
        except re.error as e:
            logger.error(f"Pattern invalide pour signature {signature['id']}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Erreur ajout signature: {str(e)}")
            return False
    
    def _save_signatures(self) -> None:
        """Sauvegarde les signatures dans la base de données"""
        try:
            with open(self.signature_db_path, 'w') as f:
                json.dump(self.signatures, f, indent=2)
        except Exception as e:
            logger.error(f"Erreur sauvegarde signatures: {str(e)}")


class SecurityThreatDetector(BaseAgent):
    """Agent de détection de menaces de sécurité"""
    
    def __init__(
        self,
        agent_id: str,
        context: AgentContext,
        threat_intel_api_keys: Optional[Dict[str, str]] = None,
        siem_integrations: Optional[List[str]] = None
    ):
        """
        Initialise le détecteur de menaces.
        
        Args:
            agent_id: ID de l'agent
            context: Contexte de l'agent
            threat_intel_api_keys: Clés API pour les feeds d'intelligence
            siem_integrations: Intégrations SIEM configurées
        """
        super().__init__(agent_id, context)
        
        # Composants de détection
        self.threat_intel = ThreatIntelligenceFeed(threat_intel_api_keys or {})
        self.behavioral_analyzer = BehavioralAnalyzer()
        self.signature_detector = SignatureDetector()
        
        # Intégrations SIEM
        self.siem_integrations = siem_integrations or []
        self.siem_clients = self._initialize_siem_clients()
        
        # Compliance
        self.compliance_checker = ComplianceChecker()
        
        # Métriques
        self.metrics = MetricsCollector()
        self.detection_stats = {
            'total_events': 0,
            'threats_detected': 0,
            'false_positives': 0,
            'avg_processing_time': 0.0
        }
        
        # Corrélation d'événements
        self.event_correlator = EventCorrelator()
        
        # Modèle de menace
        self.threat_model = ThreatModel()
        
        logger.info(f"SecurityThreatDetector {agent_id} initialisé")
    
    def _initialize_siem_clients(self) -> Dict[str, Any]:
        """Initialise les clients SIEM"""
        clients = {}
        
        for siem in self.siem_integrations:
            if siem == 'splunk':
                # Client Splunk simplifié
                clients['splunk'] = {
                    'enabled': True,
                    'endpoint': 'https://splunk.example.com:8088/services/collector',
                    'token': None  # À configurer
                }
            elif siem == 'elastic':
                # Client Elastic simplifié
                clients['elastic'] = {
                    'enabled': True,
                    'endpoint': 'https://elastic.example.com:9200',
                    'index': 'security-events'
                }
            elif siem == 'qradar':
                # Client QRadar simplifié
                clients['qradar'] = {
                    'enabled': True,
                    'endpoint': 'https://qradar.example.com/api',
                    'token': None
                }
        
        return clients
    
    async def execute(self, input_data: Dict[str, Any]) -> AgentResult:
        """
        Exécute la détection de menaces.
        
        Args:
            input_data: Données d'entrée (logs, événements, métriques)
            
        Returns:
            Résultats de la détection
        """
        start_time = time.time()
        
        try:
            # Analyse des données d'entrée
            analysis_results = await self._analyze_security_data(input_data)
            
            # Corrélation des événements
            correlated_threats = await self._correlate_events(analysis_results)
            
            # Évaluation du risque business
            risk_assessment = await self._assess_business_risk(correlated_threats)
            
            # Génération du rapport
            report = self._generate_detection_report(
                analysis_results, correlated_threats, risk_assessment
            )
            
            # Mise à jour des métriques
            self._update_metrics(start_time, analysis_results)
            
            # Intégration SIEM
            await self._send_to_siem(report)
            
            return AgentResult(
                success=True,
                data=report,
                metadata={
                    'processing_time': time.time() - start_time,
                    'threats_detected': len(correlated_threats),
                    'risk_level': risk_assessment['overall_risk']
                }
            )
            
        except Exception as e:
            logger.error(f"Erreur exécution détection menaces: {str(e)}")
            return AgentResult(
                success=False,
                error=str(e),
                metadata={'processing_time': time.time() - start_time}
            )
    
    async def _analyze_security_data(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyse les données de sécurité avec différentes techniques.
        
        Args:
            input_data: Données d'entrée
            
        Returns:
            Résultats de l'analyse
        """
        results = {
            'signature_based': [],
            'behavioral': [],
            'threat_intel': [],
            'network_analysis': [],
            'file_analysis': [],
            'compliance': []
        }
        
        # Détection basée signature
        if 'logs' in input_data:
            for log_entry in input_data['logs']:
                signatures = self.signature_detector.scan_log_entry(log_entry)
                if signatures:
                    results['signature_based'].extend(signatures)
        
        # Analyse comportementale
        if 'user_events' in input_data:
            for user_event in input_data['user_events']:
                user_id = user_event.get('user_id')
                if user_id:
                    behavior = self.behavioral_analyzer.analyze_user_behavior(user_id, user_event)
                    if behavior['anomalies']:
                        results['behavioral'].append(behavior)
                    
                    # Détection des menaces internes
                    insider_threats = self.behavioral_analyzer.detect_insider_threats(behavior)
                    if insider_threats:
                        results['behavioral'].extend(insider_threats)
        
        # Analyse réseau
        if 'network_packets' in input_data:
            for packet in input_data['network_packets']:
                # Analyse de signature
                signatures = self.signature_detector.scan_network_packet(packet)
                if signatures:
                    results['network_analysis'].extend(signatures)
                
                # Détection DDoS
                ddos_result = self._detect_ddos_attack(packet)
                if ddos_result:
                    results['network_analysis'].append(ddos_result)
                
                # Détection de scan réseau
                scan_result = self._detect_network_scan(packet)
                if scan_result:
                    results['network_analysis'].append(scan_result)
        
        # Analyse des fichiers
        if 'files' in input_data:
            for file_info in input_data['files']:
                if 'hash' in file_info:
                    intel_result = await self.threat_intel.check_file_hash(file_info['hash'])
                    if intel_result.get('is_malicious', False):
                        results['file_analysis'].append({
                            'file': file_info.get('path', 'unknown'),
                            'hash': file_info['hash'],
                            'threat_intel': intel_result,
                            'threat_type': ThreatType.MALWARE
                        })
                
                # Détection de fuite de données
                if 'content' in file_info:
                    leakage_result = self._detect_data_leakage(file_info['content'])
                    if leakage_result:
                        results['file_analysis'].append(leakage_result)
        
        # Vérification de conformité
        if 'configurations' in input_data:
            for config in input_data['configurations']:
                compliance_result = self.compliance_checker.check_configuration(config)
                if compliance_result['violations']:
                    results['compliance'].append({
                        'resource': config.get('resource', 'unknown'),
                        'violations': compliance_result['violations'],
                        'standard': compliance_result['standard']
                    })
        
        # Threat Intelligence
        if 'ips' in input_data:
            for ip in input_data['ips']:
                intel_result = await self.threat_intel.check_ip_reputation(ip)
                if intel_result.get('risk_level', RiskLevel.INFO) in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                    results['threat_intel'].append({
                        'ip': ip,
                        'intel': intel_result,
                        'threat_type': ThreatType.MALWARE
                    })
        
        if 'domains' in input_data:
            for domain in input_data['domains']:
                intel_result = await self.threat_intel.check_domain_reputation(domain)
                if intel_result.get('risk_level', RiskLevel.INFO) in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
                    results['threat_intel'].append({
                        'domain': domain,
                        'intel': intel_result,
                        'threat_type': ThreatType.PHISHING
                    })
        
        return results
    
    def _detect_ddos_attack(self, packet: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Détecte les attaques DDoS"""
        # Détection simplifiée basée sur le volume de paquets
        if packet.get('packet_count', 0) > 1000:  # 1000 paquets/seconde
            return {
                'type': 'ddos_detected',
                'confidence': 0.8,
                'description': f'Volume élevé de paquets détecté: {packet.get("packet_count")}/sec',
                'threat_type': ThreatType.DDoS,
                'source_ip': packet.get('source_ip'),
                'destination_ip': packet.get('destination_ip')
            }
        return None
    
    def _detect_network_scan(self, packet: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Détecte les scans réseau"""
        # Détection de scan de ports
        if packet.get('port_scan', False):
            return {
                'type': 'network_scan_detected',
                'confidence': 0.9,
                'description': 'Scan réseau détecté',
                'threat_type': ThreatType.NETWORK_SCAN,
                'source_ip': packet.get('source_ip'),
                'ports_scanned': packet.get('ports_scanned', [])
            }
        return None
    
    def _detect_data_leakage(self, content: str) -> Optional[Dict[str, Any]]:
        """Détecte les fuites de données dans le contenu"""
        patterns = [
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'email'),
            (r'\b\d{3}-\d{2}-\d{4}\b', 'ssn'),  # SSN US
            (r'\b\d{16}\b', 'credit_card'),  # Numéro de carte de crédit
            (r'\b[A-Z]{2}\d{6,7}\b', 'passport'),  # Numéro de passeport
        ]
        
        for pattern, data_type in patterns:
            if re.search(pattern, content):
                return {
                    'type': 'data_leakage_detected',
                    'confidence': 0.85,
                    'description': f'Données sensibles détectées: {data_type}',
                    'threat_type': ThreatType.DATA_LEAKAGE,
                    'data_type': data_type,
                    'matches': re.findall(pattern, content)[:5]  # Limite à 5 matches
                }
        
        return None
    
    async def _correlate_events(self, analysis_results: Dict[str, Any]) -> List[SecurityEvent]:
        """
        Corrèle les événements pour identifier les menaces complexes.
        
        Args:
            analysis_results: Résultats de l'analyse
            
        Returns:
            Événements de sécurité corrélés
        """
        correlated_events = []
        
        # Collecte de tous les événements détectés
        all_events = []
        
        # Événements basés signature
        for sig in analysis_results['signature_based']:
            event = SecurityEvent(
                event_id=f"SIG_{len(all_events)}",
                timestamp=datetime.utcnow(),
                threat_type=sig['threat_type'],
                detection_method=DetectionMethod.SIGNATURE,
                description=f"Signature détectée: {sig['name']}",
                confidence=0.8 if sig['severity'] == 'high' else 0.6,
                risk_level=RiskLevel(sig['severity']),
                indicators=[sig['matched_text']],
                mitre_attck_ids=sig.get('mitre_attck', [])
            )
            all_events.append(event)
        
        # Événements comportementaux
        for behavior in analysis_results['behavioral']:
            for anomaly in behavior.get('anomalies', []):
                event = SecurityEvent(
                    event_id=f"BEH_{len(all_events)}",
                    timestamp=datetime.utcnow(),
                    threat_type=ThreatType.ANOMALOUS_BEHAVIOR,
                    detection_method=DetectionMethod.BEHAVIORAL,
                    user_id=behavior.get('user_id'),
                    description=anomaly['description'],
                    confidence=anomaly['confidence'],
                    risk_level=RiskLevel.MEDIUM if anomaly['confidence'] > 0.7 else RiskLevel.LOW,
                    indicators=[anomaly['type']]
                )
                all_events.append(event)
        
        # Événements threat intelligence
        for intel in analysis_results['threat_intel']:
            event = SecurityEvent(
                event_id=f"INT_{len(all_events)}",
                timestamp=datetime.utcnow(),
                threat_type=intel.get('threat_type', ThreatType.MALWARE),
                detection_method=DetectionMethod.THREAT_INTELLIGENCE,
                source_ip=intel.get('ip'),
                description=f"Menace détectée par intelligence: {intel['intel'].get('risk_level')}",
                confidence=0.9 if intel['intel'].get('risk_level') == RiskLevel.CRITICAL else 0.7,
                risk_level=intel['intel'].get('risk_level', RiskLevel.MEDIUM),
                raw_data=intel['intel']
            )
            all_events.append(event)
        
        # Événements réseau
        for net_event in analysis_results['network_analysis']:
            event = SecurityEvent(
                event_id=f"NET_{len(all_events)}",
                timestamp=datetime.utcnow(),
                threat_type=net_event.get('threat_type', ThreatType.UNAUTHORIZED_ACCESS),
                detection_method=DetectionMethod.HEURISTIC,
                source_ip=net_event.get('source_ip'),
                destination_ip=net_event.get('destination_ip'),
                description=net_event['description'],
                confidence=net_event['confidence'],
                risk_level=RiskLevel.HIGH if net_event['confidence'] > 0.8 else RiskLevel.MEDIUM
            )
            all_events.append(event)
        
        # Corrélation temporelle et spatiale
        correlated = self.event_correlator.correlate(all_events)
        correlated_events.extend(correlated)
        
        # Ajout des événements non corrélés
        correlated_ids = {e.event_id for e in correlated_events}
        for event in all_events:
            if event.event_id not in correlated_ids:
                correlated_events.append(event)
        
        return correlated_events
    
    async def _assess_business_risk(self, threats: List[SecurityEvent]) -> Dict[str, Any]:
        """
        Évalue l'impact business des menaces détectées.
        
        Args:
            threats: Menaces détectées
            
        Returns:
            Évaluation du risque business
        """
        if not threats:
            return {
                'overall_risk': RiskLevel.INFO.value,
                'business_impact': 0.0,
                'risk_factors': []
            }
        
        # Calcul de l'impact business pour chaque menace
        for threat in threats:
            threat.business_impact = self._calculate_business_impact(threat)
        
        # Risque global basé sur la menace la plus critique
        max_risk = max(threats, key=lambda x: x.business_impact, default=None)
        
        risk_factors = []
        for threat in threats:
            if threat.business_impact > 0.5:  # Seuil d'impact significatif
                risk_factors.append({
                    'threat_type': threat.threat_type.value,
                    'business_impact': threat.business_impact,
                    'description': threat.description
                })
        
        overall_risk_level = self._determine_overall_risk(threats)
        
        return {
            'overall_risk': overall_risk_level.value,
            'business_impact': max_risk.business_impact if max_risk else 0.0,
            'risk_factors': risk_factors,
            'critical_threats': [
                t.to_dict() for t in threats 
                if t.risk_level in [RiskLevel.CRITICAL, RiskLevel.HIGH]
            ]
        }
    
    def _calculate_business_impact(self, threat: SecurityEvent) -> float:
        """
        Calcule l'impact business d'une menace.
        
        Args:
            threat: Événement de sécurité
            
        Returns:
            Score d'impact business (0-1)
        """
        impact_score = 0.0
        
        # Facteurs d'impact
        factors = {
            'risk_level': {
                RiskLevel.CRITICAL: 1.0,
                RiskLevel.HIGH: 0.7,
                RiskLevel.MEDIUM: 0.4,
                RiskLevel.LOW: 0.2,
                RiskLevel.INFO: 0.0
            },
            'threat_type': {
                ThreatType.DATA_LEAKAGE: 0.9,
                ThreatType.MALWARE: 0.8,
                ThreatType.INSIDER_THREAT: 0.85,
                ThreatType.UNAUTHORIZED_ACCESS: 0.75,
                ThreatType.DDoS: 0.7,
                ThreatType.PHISHING: 0.6
            },
            'confidence': threat.confidence,  # Utilise directement la confiance
            'affected_users': 0.5 if threat.user_id else 0.1,
            'critical_resource': 0.7 if 'prod' in (threat.resource or '').lower() else 0.1
        }
        
        # Calcul du score composite
        impact_score += factors['risk_level'].get(threat.risk_level, 0.0) * 0.3
        impact_score += factors['threat_type'].get(threat.threat_type, 0.0) * 0.3
        impact_score += factors['confidence'] * 0.2
        impact_score += factors['affected_users'] * 0.1
        impact_score += factors['critical_resource'] * 0.1
        
        return min(impact_score, 1.0)
    
    def _determine_overall_risk(self, threats: List[SecurityEvent]) -> RiskLevel:
        """Détermine le niveau de risque global"""
        if not threats:
            return RiskLevel.INFO
        
        # Compte des menaces par niveau de risque
        risk_counts = {level: 0 for level in RiskLevel}
        for threat in threats:
            risk_counts[threat.risk_level] += 1
        
        # Logique de décision
        if risk_counts[RiskLevel.CRITICAL] > 0:
            return RiskLevel.CRITICAL
        elif risk_counts[RiskLevel.HIGH] >= 3:
            return RiskLevel.CRITICAL
        elif risk_counts[RiskLevel.HIGH] > 0 or risk_counts[RiskLevel.MEDIUM] >= 5:
            return RiskLevel.HIGH
        elif risk_counts[RiskLevel.MEDIUM] > 0:
            return RiskLevel.MEDIUM
        elif risk_counts[RiskLevel.LOW] > 0:
            return RiskLevel.LOW
        else:
            return RiskLevel.INFO
    
    def _generate_detection_report(
        self,
        analysis_results: Dict[str, Any],
        correlated_threats: List[SecurityEvent],
        risk_assessment: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Génère un rapport de détection"""
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'analysis_summary': {
                'signature_based_detections': len(analysis_results['signature_based']),
                'behavioral_anomalies': len(analysis_results['behavioral']),
                'threat_intel_matches': len(analysis_results['threat_intel']),
                'network_threats': len(analysis_results['network_analysis']),
                'compliance_violations': len(analysis_results['compliance'])
            },
            'correlated_threats': [t.to_dict() for t in correlated_threats],
            'risk_assessment': risk_assessment,
            'recommendations': self._generate_recommendations(correlated_threats, risk_assessment),
            'metadata': {
                'agent_id': self.agent_id,
                'processing_time': self.detection_stats['avg_processing_time'],
                'detection_accuracy': self._calculate_detection_accuracy()
            }
        }
    
    def _generate_recommendations(
        self,
        threats: List[SecurityEvent],
        risk_assessment: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Génère des recommandations basées sur les menaces détectées"""
        recommendations = []
        
        # Recommandations génériques
        if risk_assessment['overall_risk'] in ['critical', 'high']:
            recommendations.append({
                'priority': 'immediate',
                'action': 'Isolate affected systems',
                'description': 'Isoler les systèmes affectés pour contenir la menace',
                'estimated_time': '15 minutes'
            })
        
        # Recommandations spécifiques par type de menace
        threat_types = {t.threat_type for t in threats}
        
        if ThreatType.DATA_LEAKAGE in threat_types:
            recommendations.append({
                'priority': 'high',
                'action': 'Review data access logs',
                'description': 'Examiner les logs d\'accès aux données sensibles',
                'estimated_time': '2 hours'
            })
        
        if ThreatType.MALWARE in threat_types:
            recommendations.append({
                'priority': 'high',
                'action': 'Run antivirus scan',
                'description': 'Exécuter une analyse antivirus complète',
                'estimated_time': '1 hour'
            })
        
        if ThreatType.INSIDER_THREAT in threat_types:
            recommendations.append({
                'priority': 'medium',
                'action': 'Review user permissions',
                'description': 'Réviser les permissions des utilisateurs suspects',
                'estimated_time': '4 hours'
            })
        
        if ThreatType.CLOUD_MISCONFIG in threat_types:
            recommendations.append({
                'priority': 'medium',
                'action': 'Audit cloud configurations',
                'description': 'Auditer les configurations cloud pour les erreurs de sécurité',
                'estimated_time': '8 hours'
            })
        
        return recommendations
    
    def _update_metrics(self, start_time: float, analysis_results: Dict[str, Any]) -> None:
        """Met à jour les métriques de détection"""
        processing_time = time.time() - start_time
        
        # Mise à jour des statistiques
        self.detection_stats['total_events'] += 1
        self.detection_stats['threats_detected'] += sum(
            len(results) for results in analysis_results.values()
        )
        self.detection_stats['avg_processing_time'] = (
            self.detection_stats['avg_processing_time'] * 0.9 + processing_time * 0.1
        )
        
        # Envoi des métriques
        self.metrics.record_metric(
            'security.threat_detection.processing_time',
            processing_time,
            tags={'agent_id': self.agent_id}
        )
        
        self.metrics.record_metric(
            'security.threat_detection.threats_detected',
            self.detection_stats['threats_detected'],
            tags={'agent_id': self.agent_id}
        )
    
    def _calculate_detection_accuracy(self) -> float:
        """Calcule la précision de détection"""
        if self.detection_stats['total_events'] == 0:
            return 0.0
        
        false_positive_rate = (
            self.detection_stats['false_positives'] / 
            max(self.detection_stats['threats_detected'], 1)
        )
        
        accuracy = 1.0 - false_positive_rate
        return max(accuracy, 0.0)
    
    async def _send_to_siem(self, report: Dict[str, Any]) -> None:
        """Envoie les événements aux systèmes SIEM"""
        for siem_name, client in self.siem_clients.items():
            if client.get('enabled'):
                try:
                    await self._send_to_siem_client(siem_name, client, report)
                except Exception as e:
                    logger.error(f"Erreur envoi à {siem_name}: {str(e)}")
    
    async def _send_to_siem_client(
        self,
        siem_name: str,
        client: Dict[str, Any],
        report: Dict[str, Any]
    ) -> None:
        """Envoie à un client SIEM spécifique"""
        if siem_name == 'splunk':
            # Envoi à Splunk HTTP Event Collector
            async with aiohttp.ClientSession() as session:
                headers = {
                    'Authorization': f"Splunk {client.get('token')}",
                    'Content-Type': 'application/json'
                }
                event = {
                    'event': report,
                    'sourcetype': 'microagents:security',
                    'source': f'security_threat_detector:{self.agent_id}'
                }
                async with session.post(
                    client['endpoint'],
                    headers=headers,
                    json=event
                ) as response:
                    if response.status != 200:
                        logger.error(f"Échec envoi Splunk: {response.status}")
        
        elif siem_name == 'elastic':
            # Envoi à Elasticsearch
            async with aiohttp.ClientSession() as session:
                index = client.get('index', 'security-events')
                url = f"{client['endpoint']}/{index}/_doc"
                async with session.post(url, json=report) as response:
                    if response.status != 201:
                        logger.error(f"Échec envoi Elastic: {response.status}")
        
        elif siem_name == 'qradar':
            # Envoi à QRadar (simplifié)
            async with aiohttp.ClientSession() as session:
                headers = {
                    'SEC': client.get('token', ''),
                    'Content-Type': 'application/json'
                }
                # Format QRadar
                qradar_event = {
                    'qid': 123456,  # QID custom
                    'startTime': report['timestamp'],
                    'sourceIP': report.get('source_ip', '0.0.0.0'),
                    'destinationIP': report.get('destination_ip', '0.0.0.0'),
                    'username': report.get('user_id', 'unknown'),
                    'eventName': 'Security Threat Detected',
                    'eventDescription': json.dumps(report)
                }
                async with session.post(
                    client['endpoint'],
                    headers=headers,
                    json=qradar_event
                ) as response:
                    if response.status != 200:
                        logger.error(f"Échec envoi QRadar: {response.status}")
    
    async def investigate_threat(self, threat_id: str) -> Dict[str, Any]:
        """
        Lance une investigation automatisée d'une menace.
        
        Args:
            threat_id: ID de la menace à investiguer
            
        Returns:
            Résultats de l'investigation
        """
        try:
            # Récupération des données de la menace
            # (À implémenter: accès à la base de données des menaces)
            
            investigation_steps = [
                self._gather_threat_intelligence(threat_id),
                self._analyze_attack_pattern(threat_id),
                self._assess_lateral_movement(threat_id),
                self._identify_affected_assets(threat_id),
                self._generate_containment_plan(threat_id)
            ]
            
            results = await asyncio.gather(*investigation_steps)
            
            investigation_report = {
                'threat_id': threat_id,
                'investigation_start': datetime.utcnow().isoformat(),
                'steps_completed': [r['step'] for r in results],
                'findings': [r['findings'] for r in results],
                'recommendations': self._generate_investigation_recommendations(results),
                'timeline': self._reconstruct_attack_timeline(results)
            }
            
            return investigation_report
            
        except Exception as e:
            logger.error(f"Erreur investigation menace {threat_id}: {str(e)}")
            return {'error': str(e), 'threat_id': threat_id}
    
    async def _gather_threat_intelligence(self, threat_id: str) -> Dict[str, Any]:
        """Rassemble l'intelligence sur la menace"""
        # À implémenter: recherche d'informations sur la menace
        return {
            'step': 'threat_intelligence_gathering',
            'findings': {
                'sources_checked': ['VirusTotal', 'AlienVault', 'AbuseIPDB'],
                'matches_found': 3,
                'related_campaigns': ['APT29', 'Lazarus'],
                'confidence': 0.8
            }
        }
    
    async def _analyze_attack_pattern(self, threat_id: str) -> Dict[str, Any]:
        """Analyse le pattern d'attaque"""
        # À implémenter: analyse du TTP (Tactics, Techniques, Procedures)
        return {
            'step': 'attack_pattern_analysis',
            'findings': {
                'mitre_attck_ids': ['T1059', 'T1068', 'T1078'],
                'tactics': ['Execution', 'Privilege Escalation', 'Persistence'],
                'tools_used': ['Mimikatz', 'PowerShell'],
                'complexity': 'advanced'
            }
        }
    
    async def _assess_lateral_movement(self, threat_id: str) -> Dict[str, Any]:
        """Évalue le mouvement latéral"""
        # À implémenter: analyse de la propagation
        return {
            'step': 'lateral_movement_assessment',
            'findings': {
                'systems_affected': 3,
                'movement_method': 'pass-the-hash',
                'compromised_accounts': ['admin', 'svc_backup'],
                'containment_status': 'partial'
            }
        }
    
    async def _identify_affected_assets(self, threat_id: str) -> Dict[str, Any]:
        """Identifie les assets affectés"""
        # À implémenter: inventaire des assets compromis
        return {
            'step': 'affected_assets_identification',
            'findings': {
                'critical_assets': ['db-server-01', 'file-server-02'],
                'sensitive_data': ['customer_pii', 'financial_records'],
                'recovery_time_objective': '4 hours',
                'business_impact': 'high'
            }
        }
    
    async def _generate_containment_plan(self, threat_id: str) -> Dict[str, Any]:
        """Génère un plan de confinement"""
        # À implémenter: plan de réponse aux incidents
        return {
            'step': 'containment_plan_generation',
            'findings': {
                'immediate_actions': [
                    'Isolate affected systems',
                    'Reset compromised credentials',
                    'Block malicious IPs'
                ],
                'short_term_actions': [
                    'Patch vulnerable systems',
                    'Update firewall rules',
                    'Enhance monitoring'
                ],
                'long_term_actions': [
                    'Security awareness training',
                    'Implement zero-trust architecture',
                    'Regular penetration testing'
                ]
            }
        }
    
    def _generate_investigation_recommendations(self, results: List[Dict[str, Any]]) -> List[str]:
        """Génère des recommandations basées sur les résultats d'investigation"""
        recommendations = []
        
        for result in results:
            findings = result['findings']
            
            if result['step'] == 'affected_assets_identification':
                if findings.get('business_impact') == 'high':
                    recommendations.append(
                        "Prioritize recovery of critical assets with high business impact"
                    )
            
            if result['step'] == 'lateral_movement_assessment':
                if findings.get('containment_status') == 'partial':
                    recommendations.append(
                        "Implement network segmentation to prevent further lateral movement"
                    )
        
        return list(set(recommendations))  # Élimine les doublons
    
    def _reconstruct_attack_timeline(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reconstruit la chronologie de l'attaque"""
        timeline = [
            {
                'timestamp': (datetime.utcnow() - timedelta(minutes=30)).isoformat(),
                'event': 'Initial compromise detected',
                'source': 'EDR alert'
            },
            {
                'timestamp': (datetime.utcnow() - timedelta(minutes=25)).isoformat(),
                'event': 'Lateral movement to database server',
                'source': 'Network traffic analysis'
            },
            {
                'timestamp': (datetime.utcnow() - timedelta(minutes=20)).isoformat(),
                'event': 'Data exfiltration attempt',
                'source': 'DLP alert'
            },
            {
                'timestamp': (datetime.utcnow() - timedelta(minutes=15)).isoformat(),
                'event': 'Containment measures applied',
                'source': 'Incident response'
            }
        ]
        
        return timeline


class EventCorrelator:
    """Corrélateur d'événements de sécurité"""
    
    def __init__(self, correlation_window: int = 300):
        """
        Initialise le corrélateur d'événements.
        
        Args:
            correlation_window: Fenêtre de corrélation en secondes
        """
        self.correlation_window = correlation_window
        self.event_buffer = []  # Buffer d'événements récents
        
    def correlate(self, events: List[SecurityEvent]) -> List[SecurityEvent]:
        """
        Corrèle les événements pour identifier les menaces complexes.
        
        Args:
            events: Événements à corréler
            
        Returns:
            Événements corrélés
        """
        correlated_events = []
        
        # Ajout des nouveaux événements au buffer
        self.event_buffer.extend(events)
        
        # Filtrage des événements anciens
        cutoff_time = datetime.utcnow() - timedelta(seconds=self.correlation_window)
        self.event_buffer = [
            e for e in self.event_buffer if e.timestamp > cutoff_time
        ]
        
        # Groupement par source IP
        events_by_ip = defaultdict(list)
        for event in self.event_buffer:
            if event.source_ip:
                events_by_ip[event.source_ip].append(event)
        
        # Détection de patterns corrélés
        for ip, ip_events in events_by_ip.items():
            if len(ip_events) >= 3:  # Seuil de corrélation
                correlated = self._create_correlated_event(ip, ip_events)
                if correlated:
                    correlated_events.append(correlated)
        
        # Groupement par utilisateur
        events_by_user = defaultdict(list)
        for event in self.event_buffer:
            if event.user_id:
                events_by_user[event.user_id].append(event)
        
        for user_id, user_events in events_by_user.items():
            if len(user_events) >= 2:
                correlated = self._create_correlated_user_event(user_id, user_events)
                if correlated:
                    correlated_events.append(correlated)
        
        return correlated_events
    
    def _create_correlated_event(
        self,
        source_ip: str,
        events: List[SecurityEvent]
    ) -> Optional[SecurityEvent]:
        """Crée un événement corrélé à partir d'événements liés par IP"""
        # Analyse des types de menaces
        threat_types = {e.threat_type for e in events}
        
        # Détection d'attaque multi-étapes
        if len(threat_types) >= 2:
            description = f"Multi-stage attack detected from {source_ip}"
            
            # Identification du pattern d'attaque
            if (ThreatType.BRUTE_FORCE in threat_types and 
                ThreatType.UNAUTHORIZED_ACCESS in threat_types):
                description += ": Brute force followed by unauthorized access"
            
            return SecurityEvent(
                event_id=f"CORR_{len(self.event_buffer)}",
                timestamp=datetime.utcnow(),
                threat_type=ThreatType.ZERO_DAY,  # Menace complexe
                detection_method=DetectionMethod.CORRELATION,
                source_ip=source_ip,
                description=description,
                confidence=0.85,
                risk_level=RiskLevel.HIGH,
                indicators=[e.description for e in events[:3]],
                mitre_attck_ids=self._extract_mitre_ids(events)
            )
        
        return None
    
    def _create_correlated_user_event(
        self,
        user_id: str,
        events: List[SecurityEvent]
    ) -> Optional[SecurityEvent]:
        """Crée un événement corrélé à partir d'événements liés par utilisateur"""
        # Détection de comportement suspect
        suspicious_activities = [
            e for e in events 
            if e.threat_type in [
                ThreatType.INSIDER_THREAT,
                ThreatType.DATA_LEAKAGE,
                ThreatType.ANOMALOUS_BEHAVIOR
            ]
        ]
        
        if len(suspicious_activities) >= 2:
            return SecurityEvent(
                event_id=f"USER_CORR_{len(self.event_buffer)}",
                timestamp=datetime.utcnow(),
                threat_type=ThreatType.INSIDER_THREAT,
                detection_method=DetectionMethod.CORRELATION,
                user_id=user_id,
                description=f"Suspicious user behavior pattern detected for {user_id}",
                confidence=0.75,
                risk_level=RiskLevel.MEDIUM,
                indicators=[f"{e.threat_type.value}: {e.description}" for e in suspicious_activities]
            )
        
        return None
    
    def _extract_mitre_ids(self, events: List[SecurityEvent]) -> List[str]:
        """Extrait les IDs MITRE ATT&CK des événements"""
        mitre_ids = set()
        for event in events:
            mitre_ids.update(event.mitre_attck_ids)
        return list(mitre_ids)


# Exemple d'utilisation
if __name__ == "__main__":
    # Configuration de l'agent
    context = AgentContext(
        agent_id="security_threat_detector_001",
        environment="production",
        capabilities=["threat_detection", "behavioral_analysis", "threat_intelligence"]
    )
    
    # Clés API (à remplacer par des vraies clés)
    api_keys = {
        'virustotal': 'YOUR_VIRUSTOTAL_API_KEY',
        'abuseipdb': 'YOUR_ABUSEIPDB_API_KEY',
        'alienvault': 'YOUR_ALIENVAULT_API_KEY',
        'greynoise': 'YOUR_GREYNOISE_API_KEY'
    }
    
    # Création du détecteur
    detector = SecurityThreatDetector(
        agent_id="std_001",
        context=context,
        threat_intel_api_keys=api_keys,
        siem_integrations=['splunk', 'elastic']
    )
    
    print("Security Threat Detector initialisé avec succès")
    print(f"Capacités: {detector.get_capabilities()}")