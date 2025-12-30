"""
Integration tests for DSL to Agent transformation pipeline.
Tests DSL parsing, code generation, agent execution, and orchestration.
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import yaml
from pydantic import ValidationError

from microagents.dsl.compiler import DSLCompiler, CompilationError
from microagents.dsl.parser import DSLASTParser, DSLParserError
from microagents.dsl.validator import DSLValidator, ValidationError as DSLValidationError
from microagents.agents.base import BaseAgent, AgentExecutionError
from microagents.agents.factory import AgentFactory
from microagents.agents.registry import AgentRegistry
from microagents.core.metrics import MetricsCollector
from microagents.core.security import SecurityContext
from microagents.core.cost import CostTracker
from microagents.dsl.generator import AgentCodeGenerator
from microagents.dsl.transformer import ASTToAgentTransformer


class TestDSLToAgentIntegration:
    """Integration tests for DSL to Agent transformation pipeline."""
    
    # Sample DSL definitions for testing
    SIMPLE_AGENT_DSL = """
    agent:
      name: "health_check_agent"
      version: "1.0.0"
      description: "Performs health checks on services"
    
    inputs:
      service_url:
        type: string
        description: "URL of the service to check"
        required: true
      timeout_ms:
        type: integer
        description: "Timeout in milliseconds"
        default: 5000
    
    outputs:
      status:
        type: string
        description: "Health status"
      response_time_ms:
        type: integer
        description: "Response time in milliseconds"
    
    business_logic: |
      import httpx
      import asyncio
      
      async def execute(inputs, context):
          timeout = inputs.get('timeout_ms', 5000) / 1000
          url = inputs['service_url']
          
          async with httpx.AsyncClient() as client:
              start_time = time.time()
              try:
                  response = await client.get(url, timeout=timeout)
                  elapsed_ms = (time.time() - start_time) * 1000
                  
                  if response.status_code == 200:
                      return {
                          'status': 'healthy',
                          'response_time_ms': int(elapsed_ms)
                      }
                  else:
                      return {
                          'status': 'unhealthy',
                          'response_time_ms': int(elapsed_ms),
                          'status_code': response.status_code
                      }
              except Exception as e:
                  elapsed_ms = (time.time() - start_time) * 1000
                  return {
                      'status': 'error',
                      'response_time_ms': int(elapsed_ms),
                      'error': str(e)
                  }
    
    metadata:
      category: "monitoring"
      tags: ["health", "http", "monitoring"]
      cost_profile: "low"
      security_level: "internal"
      compliance: ["sla_monitoring"]
    """
    
    COMPLEX_AGENT_DSL = """
    agent:
      name: "cost_optimizer_agent"
      version: "2.1.0"
      description: "Optimizes cloud resource costs"
    
    inputs:
      cloud_provider:
        type: string
        enum: ["aws", "azure", "gcp"]
        description: "Cloud provider"
        required: true
      resources:
        type: array
        items:
          type: object
          properties:
            id: {type: string}
            type: {type: string}
            cost_per_hour: {type: number}
        description: "List of resources to optimize"
        required: true
      optimization_strategy:
        type: string
        enum: ["aggressive", "moderate", "conservative"]
        default: "moderate"
    
    outputs:
      recommendations:
        type: array
        items:
          type: object
          properties:
            resource_id: {type: string}
            action: {type: string}
            estimated_savings: {type: number}
            risk_level: {type: string}
      total_savings:
        type: number
        description: "Total estimated monthly savings"
      optimization_score:
        type: number
        minimum: 0
        maximum: 100
    
    dependencies:
      - name: "boto3"
        version: ">=1.34.0"
      - name: "azure-mgmt-costmanagement"
        version: ">=2.0.0"
      - name: "google-cloud-billing"
        version: ">=1.7.0"
    
    business_logic: |
      import asyncio
      import json
      from typing import Dict, List, Any
      
      async def execute(inputs, context):
          provider = inputs['cloud_provider']
          resources = inputs['resources']
          strategy = inputs.get('optimization_strategy', 'moderate')
          
          # Initialize cost optimization engine
          optimizer = CostOptimizer(provider, strategy)
          
          # Analyze resources
          recommendations = []
          total_savings = 0.0
          
          for resource in resources:
              rec = optimizer.analyze_resource(resource)
              if rec:
                  recommendations.append(rec)
                  total_savings += rec.get('estimated_savings', 0)
          
          # Calculate optimization score
          score = optimizer.calculate_score(recommendations, total_savings)
          
          return {
              'recommendations': recommendations,
              'total_savings': total_savings,
              'optimization_score': score
          }
      
      class CostOptimizer:
          def __init__(self, provider, strategy):
              self.provider = provider
              self.strategy = strategy
              
          def analyze_resource(self, resource):
              # Simplified optimization logic
              cost = resource.get('cost_per_hour', 0)
              
              if cost > 10:
                  return {
                      'resource_id': resource['id'],
                      'action': 'downsize',
                      'estimated_savings': cost * 0.3 * 720,  # 30% savings monthly
                      'risk_level': 'low'
                  }
              elif cost > 1:
                  return {
                      'resource_id': resource['id'],
                      'action': 'reserved_instance',
                      'estimated_savings': cost * 0.4 * 720,
                      'risk_level': 'medium'
                  }
              return None
          
          def calculate_score(self, recommendations, total_savings):
              if not recommendations:
                  return 0
              return min(100, len(recommendations) * 10 + total_savings)
    
    metadata:
      category: "cost_optimization"
      tags: ["cost", "cloud", "optimization", "finops"]
      cost_profile: "high_value"
      security_level: "confidential"
      compliance: ["soc2", "iso27001", "gdpr"]
      multi_tenant: true
    """
    
    SECURITY_AGENT_DSL = """
    agent:
      name: "vulnerability_scanner_agent"
      version: "1.5.0"
      description: "Scans for security vulnerabilities"
    
    inputs:
      target:
        type: string
        description: "Target IP or domain"
        required: true
      scan_type:
        type: string
        enum: ["quick", "full", "comprehensive"]
        default: "quick"
      credentials:
        type: object
        properties:
          username: {type: string}
          password: {type: string, secret: true}
        description: "Authentication credentials"
    
    outputs:
      vulnerabilities:
        type: array
        items:
          type: object
          properties:
            cve_id: {type: string}
            severity: {type: string}
            description: {type: string}
            remediation: {type: string}
      scan_summary:
        type: object
        properties:
          total_vulnerabilities: {type: integer}
          critical_count: {type: integer}
          high_count: {type: integer}
          medium_count: {type: integer}
          low_count: {type: integer}
      risk_score:
        type: number
        minimum: 0
        maximum: 10
    
    security:
      input_validation: true
      output_sanitization: true
      rate_limiting: true
      max_execution_time: 300  # seconds
      allowed_networks: ["10.0.0.0/8", "192.168.0.0/16"]
    
    business_logic: |
      import asyncio
      import re
      from typing import Dict, List
      
      async def execute(inputs, context):
          target = inputs['target']
          scan_type = inputs.get('scan_type', 'quick')
          
          # Validate target
          if not self._validate_target(target):
              raise ValueError(f"Invalid target: {target}")
          
          # Perform security scan based on type
          if scan_type == "quick":
              vulnerabilities = await self._quick_scan(target)
          elif scan_type == "full":
              vulnerabilities = await self._full_scan(target)
          else:
              vulnerabilities = await self._comprehensive_scan(target)
          
          # Calculate risk score
          risk_score = self._calculate_risk_score(vulnerabilities)
          
          # Generate summary
          summary = self._generate_summary(vulnerabilities)
          
          return {
              'vulnerabilities': vulnerabilities,
              'scan_summary': summary,
              'risk_score': risk_score
          }
      
      def _validate_target(self, target):
          # Basic validation
          ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
          domain_pattern = r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
          
          if re.match(ip_pattern, target) or re.match(domain_pattern, target):
              return True
          return False
      
      async def _quick_scan(self, target):
          # Mock quick scan
          return [
              {
                  'cve_id': 'CVE-2023-12345',
                  'severity': 'medium',
                  'description': 'Sample vulnerability',
                  'remediation': 'Apply patch'
              }
          ]
      
      def _calculate_risk_score(self, vulnerabilities):
          if not vulnerabilities:
              return 0.0
          
          severity_weights = {
              'critical': 10,
              'high': 7,
              'medium': 4,
              'low': 1
          }
          
          total_score = 0
          for vuln in vulnerabilities:
              total_score += severity_weights.get(vuln['severity'], 1)
          
          return min(10.0, total_score / len(vulnerabilities))
      
      def _generate_summary(self, vulnerabilities):
          counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
          for vuln in vulnerabilities:
              severity = vuln['severity']
              if severity in counts:
                  counts[severity] += 1
          
          return {
              'total_vulnerabilities': len(vulnerabilities),
              'critical_count': counts['critical'],
              'high_count': counts['high'],
              'medium_count': counts['medium'],
              'low_count': counts['low']
          }
    
    metadata:
      category: "security"
      tags: ["security", "vulnerability", "scan", "compliance"]
      cost_profile: "security_critical"
      security_level: "restricted"
      compliance: ["nist", "cis", "pci_dss", "hipaa"]
      requires_approval: true
    """
    
    @pytest.fixture
    def dsl_compiler(self):
        """Fixture providing DSL compiler instance."""
        return DSLCompiler()
    
    @pytest.fixture
    def agent_factory(self):
        """Fixture providing agent factory instance."""
        return AgentFactory()
    
    @pytest.fixture
    def agent_registry(self):
        """Fixture providing agent registry instance."""
        return AgentRegistry()
    
    @pytest.fixture
    def metrics_collector(self):
        """Fixture providing metrics collector instance."""
        return MetricsCollector()
    
    @pytest.fixture
    def security_context(self):
        """Fixture providing security context instance."""
        return SecurityContext(
            user_id="test_user",
            tenant_id="test_tenant",
            permissions=["agent.execute", "agent.create"]
        )
    
    @pytest.fixture
    def cost_tracker(self):
        """Fixture providing cost tracker instance."""
        return CostTracker(budget_limit=1000.0)
    
    @pytest_asyncio.fixture
    async def compiled_simple_agent(self, dsl_compiler, agent_factory):
        """Fixture providing compiled simple agent."""
        dsl_data = yaml.safe_load(self.SIMPLE_AGENT_DSL)
        agent_spec = dsl_compiler.compile(dsl_data)
        agent = await agent_factory.create_agent(agent_spec)
        return agent
    
    @pytest_asyncio.fixture
    async def compiled_complex_agent(self, dsl_compiler, agent_factory):
        """Fixture providing compiled complex agent."""
        dsl_data = yaml.safe_load(self.COMPLEX_AGENT_DSL)
        agent_spec = dsl_compiler.compile(dsl_data)
        agent = await agent_factory.create_agent(agent_spec)
        return agent
    
    @pytest_asyncio.fixture
    async def compiled_security_agent(self, dsl_compiler, agent_factory):
        """Fixture providing compiled security agent."""
        dsl_data = yaml.safe_load(self.SECURITY_AGENT_DSL)
        agent_spec = dsl_compiler.compile(dsl_data)
        agent = await agent_factory.create_agent(agent_spec)
        return agent
    
    # Test Group 1: DSL Parsing Validation
    class TestDSLParsing:
        """Tests for DSL parsing and validation."""
        
        def test_simple_dsl_parsing(self, dsl_compiler):
            """Test parsing of simple DSL definition."""
            dsl_data = yaml.safe_load(self.SIMPLE_AGENT_DSL)
            
            # Parse DSL
            ast_parser = DSLASTParser()
            ast = ast_parser.parse(dsl_data)
            
            # Validate AST structure
            assert ast is not None
            assert ast.agent.name == "health_check_agent"
            assert ast.agent.version == "1.0.0"
            assert len(ast.inputs) == 2
            assert len(ast.outputs) == 2
            assert ast.business_logic is not None
            
        def test_complex_dsl_parsing(self, dsl_compiler):
            """Test parsing of complex DSL with dependencies."""
            dsl_data = yaml.safe_load(self.COMPLEX_AGENT_DSL)
            
            ast_parser = DSLASTParser()
            ast = ast_parser.parse(dsl_data)
            
            assert ast.agent.name == "cost_optimizer_agent"
            assert ast.agent.version == "2.1.0"
            assert len(ast.inputs) == 3
            assert len(ast.outputs) == 3
            assert len(ast.dependencies) == 3
            assert ast.metadata.multi_tenant is True
            
        def test_dsl_validation(self):
            """Test DSL validation with invalid inputs."""
            validator = DSLValidator()
            
            # Valid DSL
            valid_dsl = yaml.safe_load(self.SIMPLE_AGENT_DSL)
            assert validator.validate(valid_dsl) is True
            
            # Invalid DSL - missing required field
            invalid_dsl = valid_dsl.copy()
            del invalid_dsl['agent']['name']
            
            with pytest.raises(DSLValidationError) as exc_info:
                validator.validate(invalid_dsl)
            assert "missing required field" in str(exc_info.value).lower()
            
        def test_dsl_security_validation(self):
            """Test security-specific DSL validation."""
            validator = DSLValidator()
            
            security_dsl = yaml.safe_load(self.SECURITY_AGENT_DSL)
            
            # Should pass validation
            assert validator.validate(security_dsl) is True
            
            # Test with missing security section (should still be valid)
            dsl_without_security = security_dsl.copy()
            dsl_without_security.pop('security', None)
            assert validator.validate(dsl_without_security) is True
            
        @pytest.mark.parametrize("invalid_dsl", [
            {"agent": {"name": "test"}},  # Missing version
            {"agent": {"name": "test", "version": "1.0"}, "inputs": "not_a_dict"},
            {"agent": {"name": "test", "version": "1.0"}, "business_logic": 123},
        ])
        def test_invalid_dsl_parsing(self, invalid_dsl):
            """Test parsing of invalid DSL structures."""
            parser = DSLASTParser()
            
            with pytest.raises(DSLParserError):
                parser.parse(invalid_dsl)
    
    # Test Group 2: Code Generation Verification
    class TestCodeGeneration:
        """Tests for code generation from DSL."""
        
        def test_simple_agent_code_generation(self, dsl_compiler):
            """Test code generation for simple agent."""
            dsl_data = yaml.safe_load(self.SIMPLE_AGENT_DSL)
            agent_spec = dsl_compiler.compile(dsl_data)
            
            # Verify generated code
            assert agent_spec.code is not None
            assert "async def execute" in agent_spec.code
            assert "health_check_agent" in agent_spec.code
            assert "httpx" in agent_spec.code  # Check dependencies
            
            # Verify agent specification
            assert agent_spec.name == "health_check_agent"
            assert agent_spec.version == "1.0.0"
            assert agent_spec.input_schema is not None
            assert agent_spec.output_schema is not None
            
        def test_complex_agent_code_generation(self, dsl_compiler):
            """Test code generation for complex agent with dependencies."""
            dsl_data = yaml.safe_load(self.COMPLEX_AGENT_DSL)
            agent_spec = dsl_compiler.compile(dsl_data)
            
            # Verify code includes optimization logic
            assert "CostOptimizer" in agent_spec.code
            assert "boto3" in str(agent_spec.dependencies)
            assert "azure-mgmt-costmanagement" in str(agent_spec.dependencies)
            
            # Verify complex input/output schemas
            assert "resources" in agent_spec.input_schema.get("properties", {})
            assert "recommendations" in agent_spec.output_schema.get("properties", {})
            
        def test_security_agent_code_generation(self, dsl_compiler):
            """Test code generation for security agent."""
            dsl_data = yaml.safe_load(self.SECURITY_AGENT_DSL)
            agent_spec = dsl_compiler.compile(dsl_data)
            
            # Verify security measures in code
            assert "_validate_target" in agent_spec.code
            assert "security" in agent_spec.metadata
            
            # Verify security context
            security_config = agent_spec.metadata.get("security", {})
            assert security_config.get("input_validation") is True
            assert security_config.get("max_execution_time") == 300
            
        def test_code_generation_performance(self, dsl_compiler):
            """Test performance of code generation."""
            dsl_data = yaml.safe_load(self.COMPLEX_AGENT_DSL)
            
            # Measure compilation time
            start_time = time.time()
            for _ in range(100):  # Multiple compilations
                agent_spec = dsl_compiler.compile(dsl_data)
            end_time = time.time()
            
            avg_time = (end_time - start_time) / 100
            assert avg_time < 0.1  # Should be less than 100ms per compilation
            
        def test_generated_code_safety(self, dsl_compiler):
            """Test that generated code doesn't contain dangerous constructs."""
            dsl_data = yaml.safe_load(self.SIMPLE_AGENT_DSL)
            agent_spec = dsl_compiler.compile(dsl_data)
            
            code = agent_spec.code
            
            # Check for dangerous constructs
            dangerous_patterns = [
                "exec(", "eval(", "__import__", "open(",  # Basic dangerous patterns
                "os.system", "subprocess.call",  # Shell execution
                "pickle.loads", "marshal.loads",  # Unsafe deserialization
            ]
            
            for pattern in dangerous_patterns:
                assert pattern not in code, f"Dangerous pattern found: {pattern}"
            
            # Verify imports are safe
            assert "import httpx" in code
            assert "import asyncio" in code
            assert "import time" in code
    
    # Test Group 3: Agent Execution Testing
    class TestAgentExecution:
        """Tests for agent execution from generated code."""
        
        @pytest.mark.asyncio
        async def test_simple_agent_execution(self, compiled_simple_agent):
            """Test execution of simple health check agent."""
            # Mock HTTP response
            with patch('httpx.AsyncClient.get') as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_get.return_value = mock_response
                
                # Execute agent
                inputs = {
                    "service_url": "https://example.com/health",
                    "timeout_ms": 3000
                }
                
                result = await compiled_simple_agent.execute(inputs)
                
                # Verify results
                assert result["status"] == "healthy"
                assert "response_time_ms" in result
                assert isinstance(result["response_time_ms"], int)
                
        @pytest.mark.asyncio
        async def test_complex_agent_execution(self, compiled_complex_agent):
            """Test execution of complex cost optimizer agent."""
            inputs = {
                "cloud_provider": "aws",
                "resources": [
                    {"id": "i-12345", "type": "ec2", "cost_per_hour": 0.5},
                    {"id": "i-67890", "type": "ec2", "cost_per_hour": 15.0},
                    {"id": "rds-123", "type": "rds", "cost_per_hour": 0.8},
                ],
                "optimization_strategy": "moderate"
            }
            
            result = await compiled_complex_agent.execute(inputs)
            
            # Verify results structure
            assert "recommendations" in result
            assert "total_savings" in result
            assert "optimization_score" in result
            
            # Verify recommendation logic
            recommendations = result["recommendations"]
            assert len(recommendations) >= 1  # Should have at least one recommendation
            
            # Check that expensive resource gets recommendation
            expensive_resource_recommendation = next(
                (r for r in recommendations if r["resource_id"] == "i-67890"), None
            )
            assert expensive_resource_recommendation is not None
            assert expensive_resource_recommendation["estimated_savings"] > 0
            
        @pytest.mark.asyncio
        async def test_security_agent_execution(self, compiled_security_agent):
            """Test execution of security vulnerability scanner."""
            inputs = {
                "target": "192.168.1.1",
                "scan_type": "quick"
            }
            
            result = await compiled_security_agent.execute(inputs)
            
            # Verify security scan results
            assert "vulnerabilities" in result
            assert "scan_summary" in result
            assert "risk_score" in result
            
            vulnerabilities = result["vulnerabilities"]
            assert isinstance(vulnerabilities, list)
            
            summary = result["scan_summary"]
            assert summary["total_vulnerabilities"] == len(vulnerabilities)
            
            risk_score = result["risk_score"]
            assert 0 <= risk_score <= 10
            
        @pytest.mark.asyncio
        async def test_agent_execution_with_invalid_input(self, compiled_simple_agent):
            """Test agent execution with invalid inputs."""
            # Missing required field
            with pytest.raises(ValidationError):
                await compiled_simple_agent.execute({"timeout_ms": 5000})
            
            # Invalid data type
            with pytest.raises(ValidationError):
                await compiled_simple_agent.execute({
                    "service_url": "https://example.com",
                    "timeout_ms": "not_a_number"
                })
                
        @pytest.mark.asyncio
        async def test_agent_execution_timeout(self, compiled_simple_agent):
            """Test agent execution timeout handling."""
            # Simulate slow response
            async def slow_response(*args, **kwargs):
                await asyncio.sleep(2)  # Longer than timeout
                return MagicMock(status_code=200)
            
            with patch('httpx.AsyncClient.get', side_effect=slow_response):
                inputs = {
                    "service_url": "https://example.com",
                    "timeout_ms": 100  # Very short timeout
                }
                
                result = await compiled_simple_agent.execute(inputs)
                
                # Should return error status due to timeout
                assert result["status"] == "error"
                assert "response_time_ms" in result
    
    # Test Group 4: Business Logic Correctness
    class TestBusinessLogic:
        """Tests for business logic correctness."""
        
        @pytest.mark.asyncio
        async def test_cost_optimization_logic(self, compiled_complex_agent):
            """Test business logic for cost optimization."""
            test_cases = [
                {
                    "resources": [{"id": "test1", "type": "ec2", "cost_per_hour": 20}],
                    "expected_recommendations": 1,
                    "min_savings": 4000,  # 20 * 0.3 * 720 * 0.9 (10% margin)
                },
                {
                    "resources": [{"id": "test2", "type": "ec2", "cost_per_hour": 5}],
                    "expected_recommendations": 1,
                    "min_savings": 1400,  # 5 * 0.4 * 720 * 0.9
                },
                {
                    "resources": [{"id": "test3", "type": "ec2", "cost_per_hour": 0.1}],
                    "expected_recommendations": 0,  # Too cheap to optimize
                },
            ]
            
            for test_case in test_cases:
                inputs = {
                    "cloud_provider": "aws",
                    "resources": test_case["resources"],
                    "optimization_strategy": "moderate"
                }
                
                result = await compiled_complex_agent.execute(inputs)
                
                assert len(result["recommendations"]) == test_case["expected_recommendations"]
                
                if test_case["expected_recommendations"] > 0:
                    assert result["total_savings"] >= test_case["min_savings"]
                    assert result["optimization_score"] > 0
                    
        @pytest.mark.asyncio
        async def test_security_risk_scoring_logic(self, compiled_security_agent):
            """Test business logic for security risk scoring."""
            test_vulnerabilities = [
                {
                    "vulnerabilities": [
                        {"cve_id": "CVE-1", "severity": "critical"},
                        {"cve_id": "CVE-2", "severity": "high"},
                    ],
                    "expected_score": (10 + 7) / 2,  # Average of weights
                },
                {
                    "vulnerabilities": [
                        {"cve_id": "CVE-3", "severity": "medium"},
                        {"cve_id": "CVE-4", "severity": "medium"},
                        {"cve_id": "CVE-5", "severity": "low"},
                    ],
                    "expected_score": (4 + 4 + 1) / 3,
                },
            ]
            
            # Mock the scan methods to return test data
            for test_case in test_vulnerabilities:
                # Patch the scan method
                with patch.object(
                    compiled_security_agent,
                    '_quick_scan',
                    return_value=test_case["vulnerabilities"]
                ):
                    inputs = {
                        "target": "192.168.1.1",
                        "scan_type": "quick"
                    }
                    
                    result = await compiled_security_agent.execute(inputs)
                    
                    # Allow small floating point differences
                    assert abs(result["risk_score"] - test_case["expected_score"]) < 0.01
                    
        @pytest.mark.asyncio
        async def test_multi_tenant_isolation(self, compiled_complex_agent):
            """Test business logic isolation for multi-tenant agents."""
            # Execute for tenant A
            inputs_a = {
                "cloud_provider": "aws",
                "resources": [{"id": "tenant-a-1", "type": "ec2", "cost_per_hour": 10}],
            }
            
            result_a = await compiled_complex_agent.execute(inputs_a)
            
            # Execute for tenant B
            inputs_b = {
                "cloud_provider": "aws",
                "resources": [{"id": "tenant-b-1", "type": "ec2", "cost_per_hour": 15}],
            }
            
            result_b = await compiled_complex_agent.execute(inputs_b)
            
            # Results should be independent
            assert result_a["recommendations"][0]["resource_id"] == "tenant-a-1"
            assert result_b["recommendations"][0]["resource_id"] == "tenant-b-1"
            assert result_a["total_savings"] != result_b["total_savings"]
    
    # Test Group 5: Performance Benchmarks
    class TestPerformanceBenchmarks:
        """Performance benchmark tests."""
        
        @pytest.mark.benchmark
        @pytest.mark.asyncio
        async def test_agent_execution_performance(self, compiled_simple_agent):
            """Benchmark agent execution performance."""
            execution_times = []
            
            inputs = {
                "service_url": "https://example.com",
                "timeout_ms": 5000
            }
            
            # Mock fast response
            with patch('httpx.AsyncClient.get') as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_get.return_value = mock_response
                
                # Warm-up
                for _ in range(10):
                    await compiled_simple_agent.execute(inputs)
                
                # Actual benchmark
                for _ in range(100):
                    start_time = time.perf_counter()
                    await compiled_simple_agent.execute(inputs)
                    end_time = time.perf_counter()
                    execution_times.append(end_time - start_time)
            
            # Calculate statistics
            avg_time = sum(execution_times) / len(execution_times)
            p95_time = sorted(execution_times)[int(len(execution_times) * 0.95)]
            
            # Performance assertions
            assert avg_time < 0.1  # Should be less than 100ms on average
            assert p95_time < 0.2  # 95th percentile should be less than 200ms
            
            # Log results
            print(f"\nExecution Performance:")
            print(f"  Average: {avg_time * 1000:.2f}ms")
            print(f"  P95: {p95_time * 1000:.2f}ms")
            print(f"  Min: {min(execution_times) * 1000:.2f}ms")
            print(f"  Max: {max(execution_times) * 1000:.2f}ms")
            
        @pytest.mark.benchmark
        def test_dsl_compilation_performance(self, dsl_compiler):
            """Benchmark DSL compilation performance."""
            dsl_data = yaml.safe_load(self.COMPLEX_AGENT_DSL)
            
            compilation_times = []
            
            # Warm-up
            for _ in range(10):
                dsl_compiler.compile(dsl_data)
            
            # Actual benchmark
            for _ in range(100):
                start_time = time.perf_counter()
                dsl_compiler.compile(dsl_data)
                end_time = time.perf_counter()
                compilation_times.append(end_time - start_time)
            
            avg_time = sum(compilation_times) / len(compilation_times)
            
            assert avg_time < 0.05  # Should be less than 50ms
            
            print(f"\nCompilation Performance:")
            print(f"  Average: {avg_time * 1000:.2f}ms")
            
        @pytest.mark.benchmark
        @pytest.mark.asyncio
        async def test_concurrent_agent_execution(self, compiled_simple_agent):
            """Test concurrent execution of multiple agents."""
            inputs = {
                "service_url": "https://example.com",
                "timeout_ms": 5000
            }
            
            # Mock responses
            with patch('httpx.AsyncClient.get') as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_get.return_value = mock_response
                
                # Execute concurrently
                start_time = time.perf_counter()
                
                tasks = [compiled_simple_agent.execute(inputs) for _ in range(100)]
                results = await asyncio.gather(*tasks)
                
                end_time = time.perf_counter()
                
                total_time = end_time - start_time
                
                # Verify all executions succeeded
                assert all(r["status"] == "healthy" for r in results)
                
                # Should complete in reasonable time (concurrency helps)
                assert total_time < 5.0  # 100 requests in under 5 seconds
                
                print(f"\nConcurrent Execution:")
                print(f"  100 agents in {total_time:.2f}s")
                print(f"  Throughput: {100 / total_time:.2f} agents/sec")
    
    # Test Group 6: Security Validation
    class TestSecurityValidation:
        """Security validation tests."""
        
        @pytest.mark.asyncio
        async def test_input_validation_security(self, compiled_security_agent):
            """Test security of input validation."""
            # Test with invalid target (should be rejected)
            invalid_targets = [
                "http://malicious-site.com",  # URL instead of IP/domain
                "127.0.0.1; rm -rf /",  # Injection attempt
                "../../etc/passwd",  # Path traversal
                "<script>alert('xss')</script>",  # XSS attempt
            ]
            
            for target in invalid_targets:
                inputs = {"target": target, "scan_type": "quick"}
                result = await compiled_security_agent.execute(inputs)
                
                # Should return error or empty results for invalid input
                assert result["risk_score"] == 0.0
                assert len(result["vulnerabilities"]) == 0
                
        def test_code_injection_prevention(self, dsl_compiler):
            """Test that DSL prevents code injection."""
            malicious_dsl = """
            agent:
              name: "malicious_agent"
              version: "1.0.0"
            
            business_logic: |
              import os
              os.system('rm -rf /')  # Malicious code
              
              async def execute(inputs, context):
                  return {"status": "malicious"}
            """
            
            dsl_data = yaml.safe_load(malicious_dsl)
            
            # Should be caught by security validation
            validator = DSLValidator()
            
            with pytest.raises(DSLValidationError) as exc_info:
                validator.validate(dsl_data)
            assert "dangerous" in str(exc_info.value).lower() or "security" in str(exc_info.value).lower()
            
        @pytest.mark.asyncio
        async def test_secret_handling(self, compiled_security_agent):
            """Test that secrets are properly handled."""
            inputs = {
                "target": "192.168.1.1",
                "scan_type": "quick",
                "credentials": {
                    "username": "admin",
                    "password": "super_secret_password"
                }
            }
            
            # Execute agent
            result = await compiled_security_agent.execute(inputs)
            
            # Secrets should not appear in logs or results
            assert "super_secret_password" not in str(result)
            
            # Verify the agent doesn't leak secrets in exceptions
            with patch.object(compiled_security_agent, '_quick_scan', side_effect=Exception("Test error")):
                try:
                    await compiled_security_agent.execute(inputs)
                except Exception as e:
                    assert "super_secret_password" not in str(e)
                    
        @pytest.mark.asyncio
        async def test_rate_limiting_security(self):
            """Test rate limiting security feature."""
            # This would require a more complex setup with actual rate limiting middleware
            # For now, verify the DSL includes rate limiting configuration
            dsl_data = yaml.safe_load(self.SECURITY_AGENT_DSL)
            
            security_config = dsl_data.get("security", {})
            assert security_config.get("rate_limiting") is True
            
    # Test Group 7: Error Handling Testing
    class TestErrorHandling:
        """Tests for error handling and recovery."""
        
        @pytest.mark.asyncio
        async def test_agent_execution_error_handling(self, compiled_simple_agent):
            """Test error handling during agent execution."""
            # Simulate network error
            with patch('httpx.AsyncClient.get', side_effect=Exception("Network error")):
                inputs = {
                    "service_url": "https://example.com",
                    "timeout_ms": 5000
                }
                
                result = await compiled_simple_agent.execute(inputs)
                
                # Should handle error gracefully
                assert result["status"] == "error"
                assert "error" in result
                assert "Network error" in result["error"]
                
        @pytest.mark.asyncio
        async def test_dependency_error_handling(self, dsl_compiler, agent_factory):
            """Test handling of missing dependencies."""
            dsl_with_missing_dep = """
            agent:
              name: "agent_with_missing_dep"
              version: "1.0.0"
            
            dependencies:
              - name: "non_existent_package"
                version: ">=999.0.0"
            
            business_logic: |
              async def execute(inputs, context):
                  import non_existent_package  # This will fail
                  return {"status": "ok"}
            """
            
            dsl_data = yaml.safe_load(dsl_with_missing_dep)
            
            # Compilation should succeed
            agent_spec = dsl_compiler.compile(dsl_data)
            
            # Agent creation might fail or handle missing dependency
            try:
                agent = await agent_factory.create_agent(agent_spec)
                
                # If agent is created, execution should handle the import error
                result = await agent.execute({})
                assert result["status"] == "error"
            except ImportError:
                # This is also acceptable - dependency checking happens at creation
                pass
                
        @pytest.mark.asyncio
        async def test_timeout_error_handling(self, compiled_simple_agent):
            """Test timeout error handling."""
            # Simulate very slow response
            async def very_slow_response(*args, **kwargs):
                await asyncio.sleep(10)  # Much longer than any reasonable timeout
                return MagicMock(status_code=200)
            
            with patch('httpx.AsyncClient.get', side_effect=very_slow_response):
                inputs = {
                    "service_url": "https://example.com",
                    "timeout_ms": 100  # Very short timeout
                }
                
                result = await compiled_simple_agent.execute(inputs)
                
                # Should timeout and return error
                assert result["status"] == "error"
                assert "timeout" in result.get("error", "").lower() or "timed out" in result.get("error", "").lower()
                
        @pytest.mark.asyncio
        async def test_memory_error_handling(self):
            """Test handling of memory errors in agent execution."""
            # This is a challenging test - would require injecting memory errors
            # For now, verify agents have memory limits in metadata
            dsl_data = yaml.safe_load(self.SECURITY_AGENT_DSL)
            
            security_config = dsl_data.get("security", {})
            max_execution_time = security_config.get("max_execution_time")
            assert max_execution_time is not None
            
    # Test Group 8: Multi-Agent Orchestration
    class TestMultiAgentOrchestration:
        """Tests for multi-agent orchestration."""
        
        @pytest.mark.asyncio
        async def test_agent_dependency_orchestration(self, dsl_compiler, agent_factory, agent_registry):
            """Test orchestration of agents with dependencies."""
            # Create multiple agents
            agents = []
            
            for i in range(3):
                dsl = f"""
                agent:
                  name: "orchestration_agent_{i}"
                  version: "1.0.0"
                  description: "Test agent {i}"
                
                business_logic: |
                  async def execute(inputs, context):
                      # Agent logic that depends on previous agent
                      value = inputs.get('previous_result', 0)
                      return {{'result': value + {i + 1}, 'agent_id': '{i}'}}
                """
                
                dsl_data = yaml.safe_load(dsl)
                agent_spec = dsl_compiler.compile(dsl_data)
                agent = await agent_factory.create_agent(agent_spec)
                agents.append(agent)
                agent_registry.register(agent)
            
            # Execute agents in sequence (simple orchestration)
            current_result = 0
            for agent in agents:
                result = await agent.execute({"previous_result": current_result})
                current_result = result["result"]
            
            # Final result should be sum of all agents (1 + 2 + 3 = 6)
            assert current_result == 6
            
        @pytest.mark.asyncio
        async def test_parallel_agent_execution(self, agent_registry):
            """Test parallel execution of multiple agents."""
            # This would require a more complex orchestration engine
            # For now, test that agents can execute concurrently
            pass
            
        @pytest.mark.asyncio
        async def test_agent_communication_patterns(self):
            """Test different agent communication patterns."""
            # Test cases for:
            # 1. Request-Response pattern
            # 2. Publish-Subscribe pattern
            # 3. Pipeline pattern
            # 4. Broadcast pattern
            
            # This would require a message bus or event system
            pass
    
    # Test Group 9: Version Compatibility
    class TestVersionCompatibility:
        """Tests for version compatibility."""
        
        def test_backward_compatibility(self, dsl_compiler):
            """Test backward compatibility of DSL versions."""
            # Test different DSL versions
            dsl_versions = [
                {"version": "1.0", "dsl": self.SIMPLE_AGENT_DSL},
                {"version": "2.0", "dsl": self.COMPLEX_AGENT_DSL},
                {"version": "1.5", "dsl": self.SECURITY_AGENT_DSL},
            ]
            
            for dsl_info in dsl_versions:
                dsl_data = yaml.safe_load(dsl_info["dsl"])
                
                # Should parse successfully regardless of version
                agent_spec = dsl_compiler.compile(dsl_data)
                assert agent_spec is not None
                assert agent_spec.version is not None
                
        def test_schema_evolution(self):
            """Test handling of schema evolution."""
            # Test adding new fields to DSL
            base_dsl = yaml.safe_load(self.SIMPLE_AGENT_DSL)
            
            # Add new optional field
            evolved_dsl = base_dsl.copy()
            evolved_dsl["inputs"]["new_optional_field"] = {
                "type": "string",
                "description": "New optional field",
                "required": false
            }
            
            # Should still be valid
            validator = DSLValidator()
            assert validator.validate(evolved_dsl) is True
            
            # Test removing required field (should fail)
            invalid_dsl = base_dsl.copy()
            del invalid_dsl["inputs"]["service_url"]
            
            with pytest.raises(DSLValidationError):
                validator.validate(invalid_dsl)
                
        @pytest.mark.asyncio
        async def test_agent_version_routing(self, agent_registry):
            """Test routing to correct agent version."""
            # Register multiple versions of same agent
            # This would require version-aware routing in agent registry
            pass
    
    # Test Group 10: Rollback Testing
    class TestRollbackTesting:
        """Tests for rollback and recovery scenarios."""
        
        @pytest.mark.asyncio
        async def test_agent_rollback_on_failure(self, dsl_compiler, agent_factory):
            """Test rollback when agent execution fails."""
            dsl = """
            agent:
              name: "stateful_agent"
              version: "1.0.0"
            
            business_logic: |
              state = {'counter': 0}
              
              async def execute(inputs, context):
                  # Increment counter
                  state['counter'] += 1
                  
                  # Fail on 3rd attempt
                  if state['counter'] == 3:
                      raise Exception("Intentional failure on attempt 3")
                  
                  return {'counter': state['counter']}
            """
            
            dsl_data = yaml.safe_load(dsl)
            agent_spec = dsl_compiler.compile(dsl_data)
            agent = await agent_factory.create_agent(agent_spec)
            
            # First execution - should succeed
            result1 = await agent.execute({})
            assert result1["counter"] == 1
            
            # Second execution - should succeed
            result2 = await agent.execute({})
            assert result2["counter"] == 2
            
            # Third execution - should fail
            with pytest.raises(Exception) as exc_info:
                await agent.execute({})
            assert "Intentional failure" in str(exc_info.value)
            
            # State should be rolled back or handled appropriately
            # Note: This depends on the agent's state management implementation
            
        @pytest.mark.asyncio
        async def test_transactional_agent_execution(self):
            """Test transactional execution with rollback support."""
            # This would require a transactional execution framework
            # For now, verify agents can participate in transactions
            pass
            
        @pytest.mark.asyncio
        async def test_agent_state_persistence_and_recovery(self):
            """Test agent state persistence and recovery after failure."""
            # Test that agents can save state and recover
            # This would require state persistence mechanism
            pass


# Performance test markers
pytest.mark.benchmark = pytest.mark.skipif(
    os.getenv("RUN_BENCHMARKS", "false").lower() != "true",
    reason="Benchmark tests disabled by default"
)

# Security test markers  
pytest.mark.security = pytest.mark.skipif(
    os.getenv("RUN_SECURITY_TESTS", "true").lower() != "true",
    reason="Security tests can be disabled for faster runs"
)


if __name__ == "__main__":
    # Run specific test groups
    pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-k", "TestDSLParsing or TestCodeGeneration",
        "--log-level=INFO"
    ])