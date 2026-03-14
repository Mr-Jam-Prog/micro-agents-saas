# MicroAgents DSL - Exemple Complet d'Agent
# Agent: CostAnomalyDetectorAdvanced
# Description: Détecteur d'anomalies de coûts multi-cloud avec IA
# Version: 2.1.0
# ROI Score: 9.8/10
# Complexity: Advanced

## ============================================================================
## SECTION 1: MÉTADONNÉES ET CONFIGURATION
## ============================================================================

metadata:
  # ==================== IDENTIFICATION ====================
  name: "CostAnomalyDetectorAdvanced"
  version: "2.1.0"
  description: |
    Détecte les anomalies de coûts multi-cloud en temps réel avec apprentissage automatique.
    Identifie les dérives budgétaires, les configurations inefficaces et optimise automatiquement.
    ROI garanti: 300% sur 6 mois.
  
  # ==================== CLASSIFICATION ====================
  category: "cost-optimization"
  subcategory: "anomaly-detection"
  tags: ["aws", "azure", "gcp", "ml", "real-time", "multi-cloud"]
  criticality: "high"  # Impact business élevé
  
  # ==================== PROPRIÉTÉS D'ENTREPRISE ====================
  business_value:
    roi_calculation:
      base_metric: "cost_savings_per_month"
      expected_savings: 15000  # USD/mois
      implementation_cost: 5000
      payback_period: "2 months"
      annual_roi: "300%"
    
    kpis:
      - "reduction_cost_overruns"
      - "increase_budget_accuracy"
      - "decrease_incident_response_time"
    
    sla:  # Service Level Agreement
      availability: "99.95%"
      detection_time: "< 5 minutes"
      accuracy: "> 95%"
      false_positive_rate: "< 3%"

  # ==================== CONFORMITÉ ET SÉCURITÉ ====================
  compliance:
    standards: ["SOC2", "ISO27001", "GDPR", "HIPAA"]
    data_classification: "confidential"
    retention_policy: "30 days"
    
  security:
    authentication_required: true
    encryption_level: "AES-256-GCM"
    audit_logging: "detailed"
    pii_handling: "masked"

## ============================================================================
## SECTION 2: CONFIGURATION DES ENTRÉES
## ============================================================================

inputs:
  # ==================== DONNÉES DE COÛTS ====================
  cost_data:
    type: "structured_data"
    format: "json"
    description: "Données de coûts historiques et en temps réel"
    required: true
    validation:
      schema: "cost_data_schema_v2"
      rules:
        - "field:amount must be positive"
        - "field:timestamp must be ISO8601"
        - "field:service must be in allowed_services"
    example: |
      {
        "timestamp": "2024-01-15T10:30:00Z",
        "service": "aws-ec2",
        "region": "us-east-1",
        "instance_type": "m5.xlarge",
        "cost_usd": 0.192,
        "usage_hours": 24,
        "tags": {"environment": "production", "team": "platform"}
      }
  
  # ==================== CONFIGURATION BUDGÉTAIRE ====================
  budget_config:
    type: "configuration"
    format: "yaml"
    description: "Configuration des budgets et alertes"
    required: false
    default: |
      thresholds:
        warning: 70%
        critical: 90%
        emergency: 110%
      notifications:
        channels: ["email", "slack", "pagerduty"]
        escalation_policy: "2h-4h-8h"
    validation:
      - "thresholds must sum to 100% or less"
      - "escalation_policy must follow pattern"
  
  # ==================== CONTEXTE MÉTIER ====================
  business_context:
    type: "context"
    format: "json"
    description: "Contexte métier pour priorisation des alertes"
    required: true
    fields:
      department:
        type: "string"
        enum: ["engineering", "finance", "operations", "sales"]
      criticality:
        type: "string"
        enum: ["low", "medium", "high", "critical"]
      fiscal_period:
        type: "string"
        pattern: "^Q[1-4]-[0-9]{4}$"
    example: |
      {
        "department": "engineering",
        "criticality": "high",
        "fiscal_period": "Q1-2024",
        "project_code": "PROJ-1234",
        "cost_center": "CC-5678"
      }
  
  # ==================== CONFIGURATION ML ====================
  ml_config:
    type: "machine_learning"
    format: "json"
    description: "Configuration du modèle d'apprentissage automatique"
    required: false
    default: |
      {
        "model_type": "isolation_forest",
        "contamination": 0.05,
        "n_estimators": 100,
        "random_state": 42,
        "retraining_frequency": "weekly",
        "features": ["cost_usd", "usage_hours", "day_of_week", "hour_of_day"]
      }
    validation:
      - "contamination must be between 0.01 and 0.2"
      - "n_estimators must be >= 50"

## ============================================================================
## SECTION 3: LOGIQUE MÉTIER - RÈGLES ET CONDITIONS
## ============================================================================

business_logic:
  # ==================== DÉTECTION D'ANOMALIES ====================
  anomaly_detection:
    algorithm: "hybrid_ml_rules"
    
    # Règle 1: Détection statistique
    statistical_rules:
      - name: "z_score_outlier"
        description: "Détecte les déviations statistiques significatives"
        condition: |
          ABS((current_cost - rolling_7d_avg) / rolling_7d_std) > 3.0
        action: "flag_anomaly"
        severity: "medium"
        
      - name: "percentage_change"
        description: "Changement de coût supérieur à 50% sur 24h"
        condition: |
          (current_cost - cost_24h_ago) / cost_24h_ago > 0.5
        action: "flag_anomaly"
        severity: "high"
    
    # Règle 2: Détection saisonnière
    seasonal_rules:
      - name: "weekend_spike"
        description: "Pic de coût inhabituel le weekend"
        condition: |
          day_of_week IN ['Saturday', 'Sunday'] AND 
          current_cost > (weekly_avg * 1.5)
        action: "flag_critical_anomaly"
        severity: "critical"
        
      - name: "off_hours_usage"
        description: "Utilisation importante en dehors des heures de bureau"
        condition: |
          hour_of_day BETWEEN 0 AND 7 AND
          usage_hours > 4 AND
          service_type = 'compute'
        action: "investigate"
        severity: "medium"
  
  # ==================== RÈGLES DE CORRÉLATION ====================
  correlation_rules:
    - name: "cost_capacity_correlation"
      description: "Vérifie la corrélation entre coût et utilisation de capacité"
      condition: |
        cost_increase > 0.3 AND capacity_utilization < 0.5
      action: "flag_inefficiency"
      priority: "high"
      remediation: "scale_down_or_optimize"
      
    - name: "multi_service_spike"
      description: "Pic de coût simultané sur plusieurs services"
      condition: |
        COUNT(DISTINCT service WHERE cost_increase > 0.4) >= 3
      action: "flag_cross_service_issue"
      priority: "critical"
      investigation: "check_dependencies"
  
  # ==================== RÈGLES MÉTIER ====================
  business_rules:
    - name: "budget_burn_rate"
      description: "Taux de consommation du budget trop élevé"
      condition: |
        days_elapsed_in_period > 0 AND
        (cost_to_date / budget) > (days_elapsed_in_period / total_days_in_period * 1.2)
      action: "alert_finance_team"
      threshold: "110% of expected burn rate"
      
    - name: "project_over_budget"
      description: "Projet dépassant son budget alloué"
      condition: |
        project_actual_cost > project_budget AND
        project_completion < 0.8
      action: "escalate_to_project_manager"
      escalation_level: 2
  
  # ==================== LOGIQUE DE PRIORISATION ====================
  prioritization_logic:
    scoring_formula: |
      priority_score = 
        (anomaly_severity * 0.4) +
        (business_criticality * 0.3) +
        (potential_savings * 0.2) +
        (time_sensitivity * 0.1)
    
    severity_matrix:
      - score_range: [0.0, 3.0]
        level: "low"
        sla: "24h"
        notification: "email_only"
        
      - score_range: [3.0, 7.0]
        level: "medium"
        sla: "4h"
        notification: ["email", "slack"]
        
      - score_range: [7.0, 9.0]
        level: "high"
        sla: "1h"
        notification: ["email", "slack", "sms"]
        
      - score_range: [9.0, 10.0]
        level: "critical"
        sla: "15min"
        notification: ["email", "slack", "sms", "pagerduty"]

## ============================================================================
## SECTION 4: GESTION DES ERREURS ET RÉSILIENCE
## ============================================================================

error_handling:
  # ==================== STRATÉGIES DE RÉTENTATIVE ====================
  retry_policy:
    max_attempts: 3
    backoff_strategy: "exponential"
    initial_delay: "1s"
    max_delay: "30s"
    retryable_errors:
      - "network_timeout"
      - "rate_limit_exceeded"
      - "temporary_unavailable"
  
  # ==================== DÉGRADATION GRACIEUSE ====================
  fallback_strategies:
    - name: "ml_model_fallback"
      trigger: "ml_service_unavailable"
      action: "use_statistical_rules_only"
      performance_impact: "low"
      
    - name: "cache_fallback"
      trigger: "database_unavailable"
      action: "use_cached_data_last_24h"
      ttl: "24h"
      
    - name: "manual_mode"
      trigger: "critical_system_failure"
      action: "send_to_human_review"
      notification: "alert_sre_team"
  
  # ==================== JOURNALISATION DES ERREURS ====================
  logging:
    level: "structured"
    format: "json"
    fields:
      required: ["timestamp", "agent_id", "error_code", "error_message"]
      optional: ["stack_trace", "input_data_hash", "retry_count"]
    retention: "30 days"
    
  # ==================== MONITORING DES ERREURS ====================
  monitoring:
    error_rate_threshold: "5% per hour"
    alert_on_consecutive_failures: 3
    escalation:
      level1: "engineering_team"
      level2: "sre_team"
      level3: "cto"

## ============================================================================
## SECTION 5: CARACTÉRISTIQUES DE PERFORMANCE
## ============================================================================

performance:
  # ==================== EXIGENCES TEMPS RÉEL ====================
  latency:
    p50: "< 100ms"
    p95: "< 500ms"
    p99: "< 1000ms"
    timeout: "5s"
    
  throughput:
    max_concurrent_requests: 1000
    requests_per_second: 100
    batch_size_limit: 1000
  
  # ==================== UTILISATION DES RESSOURCES ====================
  resource_requirements:
    cpu: "0.5 vCPU"
    memory: "512MB"
    storage: "1GB"
    network: "10Mbps"
    
  scaling:
    strategy: "horizontal"
    min_replicas: 2
    max_replicas: 10
    scale_up_threshold: "cpu > 70% for 5min"
    scale_down_threshold: "cpu < 30% for 15min"
  
  # ==================== OPTIMISATIONS ====================
  optimizations:
    caching:
      enabled: true
      ttl: "5 minutes"
      strategy: "lru"
      
    batching:
      enabled: true
      max_batch_size: 100
      max_wait_time: "1s"
      
    parallel_processing:
      enabled: true
      max_workers: 8
      chunk_size: 50

## ============================================================================
## SECTION 6: DÉPENDANCES EXTERNES
## ============================================================================

dependencies:
  # ==================== SERVICES INTERNES ====================
  internal:
    - name: "cost_data_pipeline"
      version: ">=1.2.0"
      endpoint: "grpc://cost-service.internal:50051"
      required: true
      health_check: "/health"
      timeout: "3s"
      
    - name: "ml_prediction_service"
      version: ">=2.0.0"
      endpoint: "http://ml-service.internal:8080"
      required: false  # Fallback disponible
      authentication: "jwt"
      
    - name: "notification_service"
      version: ">=1.5.0"
      endpoint: "http://notifications.internal:8080"
      required: true
      rate_limit: "100 req/min"
  
  # ==================== SERVICES EXTERNES ====================
  external:
    - name: "aws_cost_explorer"
      provider: "aws"
      service: "cost-explorer"
      authentication: "iam_role"
      rate_limit: "5 req/second"
      
    - name: "azure_cost_management"
      provider: "azure"
      service: "cost-management"
      authentication: "service_principal"
      
    - name: "gcp_billing"
      provider: "gcp"
      service: "cloud-billing"
      authentication: "service_account"
  
  # ==================== BIBLIOTHÈQUES ====================
  libraries:
    - name: "pandas"
      version: ">=1.5.0,<2.0.0"
      purpose: "data_processing"
      
    - name: "scikit-learn"
      version: ">=1.0.0"
      purpose: "machine_learning"
      
    - name: "numpy"
      version: ">=1.21.0"
      purpose: "numerical_computations"
    
    - name: "prometheus_client"
      version: ">=0.16.0"
      purpose: "metrics_collection"

## ============================================================================
## SECTION 7: CONFIGURATION DES SORTIES
## ============================================================================

outputs:
  # ==================== RAPPORT D'ANOMALIE ====================
  anomaly_report:
    type: "structured_report"
    format: "json"
    description: "Rapport complet d'anomalie détectée"
    required: true
    structure:
      metadata:
        detection_id: "uuid"
        timestamp: "iso8601"
        agent_version: "string"
        
      anomaly_details:
        severity: ["low", "medium", "high", "critical"]
        confidence: "float between 0 and 1"
        anomaly_score: "float between 0 and 10"
        detected_by: "list of rules that triggered"
        
      cost_impact:
        current_cost: "float"
        expected_cost: "float"
        variance: "float"
        variance_percentage: "float"
        potential_savings: "float"
        
      context:
        time_period: "string"
        service: "string"
        region: "string"
        resource_id: "string"
        tags: "map"
        
      recommendations:
        type: "list"
        items:
          action: "string"
          priority: ["low", "medium", "high"]
          estimated_savings: "float"
          implementation_complexity: ["low", "medium", "high"]
          risk_level: ["low", "medium", "high"]
    
    example: |
      {
        "metadata": {
          "detection_id": "anom-123e4567-e89b-12d3-a456-426614174000",
          "timestamp": "2024-01-15T10:30:00Z",
          "agent_version": "2.1.0"
        },
        "anomaly_details": {
          "severity": "high",
          "confidence": 0.92,
          "anomaly_score": 8.7,
          "detected_by": ["z_score_outlier", "weekend_spike"]
        },
        "cost_impact": {
          "current_cost": 1250.50,
          "expected_cost": 850.75,
          "variance": 399.75,
          "variance_percentage": 47.0,
          "potential_savings": 399.75
        },
        "context": {
          "time_period": "2024-01-14T00:00:00Z to 2024-01-15T00:00:00Z",
          "service": "aws-ec2",
          "region": "us-east-1",
          "resource_id": "i-1234567890abcdef0",
          "tags": {"environment": "production", "team": "platform"}
        },
        "recommendations": [
          {
            "action": "Resize instance from m5.xlarge to m5.large",
            "priority": "high",
            "estimated_savings": 200.25,
            "implementation_complexity": "low",
            "risk_level": "low"
          },
          {
            "action": "Implement auto-scaling based on usage patterns",
            "priority": "medium",
            "estimated_savings": 150.50,
            "implementation_complexity": "medium",
            "risk_level": "medium"
          }
        ]
      }
  
  # ==================== ALERTES ====================
  alerts:
    type: "notifications"
    format: "standardized_alert"
    description: "Alertes à envoyer aux canaux de notification"
    channels:
      - name: "email"
        template: "cost_anomaly_email.html"
        
      - name: "slack"
        template: "cost_anomaly_slack.json"
        
      - name: "pagerduty"
        severity: "calculated_based_on_score"
    
    routing_rules:
      - condition: "severity == 'critical'"
        channels: ["email", "slack", "sms", "pagerduty"]
        escalation: "immediate"
        
      - condition: "severity == 'high'"
        channels: ["email", "slack"]
        escalation: "within_1h"
        
      - condition: "severity == 'medium'"
        channels: ["email"]
        escalation: "within_4h"
  
  # ==================== MÉTRIQUES ====================
  metrics:
    type: "monitoring_metrics"
    format: "prometheus"
    description: "Métriques de performance et d'efficacité"
    labels: ["agent_name", "severity", "service", "region"]
    metrics:
      - name: "cost_anomalies_detected_total"
        type: "counter"
        help: "Total number of cost anomalies detected"
        
      - name: "cost_savings_estimated_usd"
        type: "gauge"
        help: "Estimated cost savings in USD"
        
      - name: "detection_latency_seconds"
        type: "histogram"
        help: "Latency of anomaly detection in seconds"
        buckets: [0.1, 0.5, 1.0, 2.0, 5.0]
        
      - name: "false_positive_rate"
        type: "gauge"
        help: "Rate of false positive detections"

## ============================================================================
## SECTION 8: SPÉCIFICATIONS DE TESTS
## ============================================================================

testing:
  # ==================== TESTS UNITAIRES ====================
  unit_tests:
    coverage_requirement: "> 90%"
    frameworks: ["pytest", "unittest"]
    
    test_cases:
      - name: "test_z_score_calculation"
        description: "Vérifie le calcul correct du Z-Score"
        input:
          current_cost: 1000
          historical_mean: 800
          historical_std: 100
        expected_output:
          z_score: 2.0
          is_anomaly: true
          severity: "medium"
        
      - name: "test_budget_burn_rate"
        description: "Teste la détection de taux de consommation élevé"
        input:
          cost_to_date: 75000
          budget: 100000
          days_elapsed: 15
          total_days: 30
        expected_output:
          burn_rate: "150%"
          alert_triggered: true
        
      - name: "test_weekend_spike_detection"
        description: "Détecte un pic de coût inhabituel le weekend"
        input:
          day_of_week: "Saturday"
          current_cost: 1500
          weekly_avg: 1000
        expected_output:
          anomaly_detected: true
          rule_triggered: "weekend_spike"
  
  # ==================== TESTS D'INTÉGRATION ====================
  integration_tests:
    environment: "staging"
    dependencies_mocking: "partial"
    
    scenarios:
      - name: "end_to_end_cost_anomaly_detection"
        description: "Test complet du flux de détection d'anomalie"
        steps:
          - "ingest_cost_data"
          - "process_with_ml_model"
          - "apply_business_rules"
          - "generate_recommendations"
          - "send_notifications"
        success_criteria:
          - "detection_latency < 2s"
          - "accuracy > 95%"
          - "all_notifications_sent"
        
      - name: "failure_recovery_scenario"
        description: "Test de résilience en cas de panne de service ML"
        precondition: "ml_service_unavailable"
        expected_behavior: "fallback_to_statistical_rules"
        success_criteria:
          - "system_remains_operational"
          - "degraded_but_functional"
          - "appropriate_alert_generated"
  
  # ==================== TESTS DE PERFORMANCE ====================
  performance_tests:
    load_test:
      concurrent_users: 100
      duration: "10 minutes"
      ramp_up: "1 minute"
      acceptable_latency: "p95 < 500ms"
      
    stress_test:
      target: "200% of normal_load"
      duration: "5 minutes"
      success_criteria: "no_crashes"
      
    endurance_test:
      duration: "24 hours"
      memory_leak_threshold: "< 1MB/hour"
  
  # ==================== TESTS DE SÉCURITÉ ====================
  security_tests:
    penetration_testing:
      required: true
      frequency: "quarterly"
      
    vulnerability_scanning:
      tools: ["snyk", "trivy"]
      frequency: "weekly"
      
    data_protection:
      - "test_pii_masking"
      - "test_encryption_at_rest"
      - "test_encryption_in_transit"

## ============================================================================
## SECTION 9: DOCUMENTATION ET MEILLEURES PRATIQUES
## ============================================================================

documentation:
  # ==================== COMMENTAIRES ÉDUCATIFS ====================
  educational_notes:
    - note: |
        # MEILLEURE PRATIQUE: Séparation des préoccupations
        Les règles métier, les règles ML et les règles techniques sont séparées
        pour une maintenance et une évolution plus faciles.
        
    - note: |
        # MEILLEURE PRATIQUE: Dégradation gracieuse
        L'agent est conçu pour continuer à fonctionner même si certaines
        dépendances échouent, en utilisant des stratégies de fallback.
        
    - note: |
        # MEILLEURE PRATIQUE: Observabilité
        Toutes les métriques importantes sont exposées au format Prometheus
        pour un monitoring complet en production.
        
    - note: |
        # MEILLEURE PRATIQUE: Versioning
        L'agent utilise le versioning sémantique (SemVer) pour une gestion
        claire des changements et une compatibilité ascendante.
  
  # ==================== GUIDE DE DÉPLOIEMENT ====================
  deployment_guide:
    prerequisites:
      - "Kubernetes 1.24+"
      - "Prometheus pour le monitoring"
      - "Redis pour le cache"
      - "PostgreSQL pour la persistance"
    
    steps:
      - "1. Vérifier les dépendances"
      - "2. Configurer les secrets"
      - "3. Déployer les ressources Kubernetes"
      - "4. Configurer le monitoring"
      - "5. Exécuter les tests de smoke"
    
    health_checks:
      liveness_probe: "/health/live"
      readiness_probe: "/health/ready"
      startup_probe: "/health/startup"
  
  # ==================== GUIDE D'UTILISATION ====================
  usage_guide:
    quick_start: |
      # Démarrer l'agent en mode développement
      1. Cloner le repository
      2. Exécuter `make dev-env`
      3. Lancer `python -m microagents.agents.cost_anomaly_detector`
      
    configuration_examples: |
      # Exemple de configuration YAML
      agent:
        name: CostAnomalyDetectorAdvanced
        env: production
        ml_model:
          retrain_frequency: weekly
          confidence_threshold: 0.9
        
    troubleshooting:
      common_issues:
        - issue: "High false positive rate"
          solution: "Ajuster le paramètre de contamination du modèle ML"
          
        - issue: "Slow detection times"
          solution: "Augmenter les ressources CPU/mémoire"
          
        - issue: "ML service unavailable"
          solution: "Vérifier que le fallback statistique fonctionne"
  
  # ==================== RÉFÉRENCE D'API ====================
  api_reference:
    endpoints:
      - method: "POST"
        path: "/api/v1/detect"
        description: "Détecte les anomalies dans les données de coûts"
        request_body: "cost_data"
        response: "anomaly_report"
        
      - method: "GET"
        path: "/api/v1/health"
        description: "Vérifie l'état de santé de l'agent"
        response: "health_status"
        
      - method: "GET"
        path: "/api/v1/metrics"
        description: "Retourne les métriques Prometheus"
        response: "metrics_data"
    
    error_codes:
      - code: "400"
        description: "Données d'entrée invalides"
        
      - code: "429"
        description: "Limite de taux dépassée"
        
      - code: "503"
        description: "Service temporairement indisponible"

## ============================================================================
## SECTION 10: VERSIONING ET ÉVOLUTION
## ============================================================================

versioning:
  # ==================== STRATÉGIE DE VERSIONING ====================
  strategy: "semantic"
  schema: "MAJOR.MINOR.PATCH"
  
  changelog:
    "2.1.0":
      added:
        - "Support multi-cloud étendu"
        - "Nouveaux algorithmes ML"
        - "Dashboard de reporting amélioré"
      
      changed:
        - "Optimisation des performances x2"
        - "Nouvelle API de configuration"
      
      deprecated:
        - "Ancien format de sortie CSV"
      
      removed:
        - "Support pour AWS Cost Explorer v1"
    
    "2.0.0":
      breaking_changes:
        - "Nouvelle structure de données d'entrée"
        - "API v2 incompatible avec v1"
  
  # ==================== COMPATIBILITÉ ====================
  backward_compatibility:
    guaranteed: "2 major versions"
    migration_tool: "provided"
    documentation: "migration_guide_v1_to_v2.md"
  
  # ==================== CYCLE DE VIE ====================
  lifecycle:
    release_date: "2024-01-15"
    end_of_life: "2026-01-15"
    end_of_support: "2025-07-15"
    
    support_policy:
      security_fixes: "until end_of_support"
      bug_fixes: "12 months after release"
      new_features: "6 months after release"

## ============================================================================
## FOOTER: SIGNATURE DE VALIDATION
## ============================================================================

# Validé par:
# - Engineering: Jane Doe - Lead Data Scientist
# - Product: John Smith - Product Manager
# - Security: Bob Wilson - Security Architect
# - Compliance: Alice Brown - Compliance Officer

# Date de validation: 2024-01-15
# Statut: Approved for Production
# Environnement cible: Production, Staging, Development